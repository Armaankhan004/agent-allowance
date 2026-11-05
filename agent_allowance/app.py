"""
Streamlit Web Application for Agent Allowance.
Deterministic Financial Guardrails for Autonomous AI Agents · Powered by PayPal Sandbox.

UI notes (presentation only; policy/agent/db/PayPal logic is untouched):
  - Warm peach-cream global theme; deep-purple palette scoped to the sidebar via [theme.sidebar].
  - All styling lives in agent_allowance/ui_theme.py (design tokens -> CSS variables).
  - Dollar amounts are escaped so markdown never renders them as LaTeX.
  - Ring gauge for the monthly allowance (mauve < 75% -> amber to 90% -> red above).
  - Live pipeline strip (Agent -> Rules -> PayPal) driven by the agent's real
    callbacks, and a verdict card with accurate outcome labels (a check only for
    real approvals; PayPal only lights up when a sandbox order exists).
  - Approvals with toasts, Rules with inline validation, AG Grid Ledger, Demo tools
    with a confirmation dialog for reset.
"""

import csv
import io
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import streamlit as st

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from agent_allowance.config import Config
from agent_allowance.db import (
    init_db,
    get_ledger_rows,
    parse_reasons,
    get_pending_transactions,
    reset_demo_transactions,
    get_transaction_by_order_id,
)
from agent_allowance.policy_engine import (
    approve_pending,
    reject_pending,
    get_policy_rules,
    update_policy_rule,
    validate_rule_value,
)
from agent_allowance.agent import ShoppingAgent, create_order_for_approved, capture_if_approved
from agent_allowance.llm_guard import (
    GENERIC_ERROR,
    MODE_GEMINI,
    MODE_MOCK,
    MODES,
    effective_mode,
    record_requests,
    redact,
    requests_remaining,
    resolve_default_mode,
)
from agent_allowance.paypal_client import get_order_status
from agent_allowance.ui_theme import (
    inject_theme_css,
    get_status_pill_html,
    get_mode_pill_html,
    get_quota_notice_html,
    get_side_meter_html,
    render_header,
    render_metrics_banner,
    render_section_title,
    render_html,
    md_escape,
    html_text,
    money_html,
    svg_icon,
    pipeline_states,
    get_pipeline_html,
    get_verdict_html,
    get_welcome_html,
    get_empty_state_html,
    APPROVALS_EMPTY_ILLUSTRATION,
    LEDGER_EMPTY_ILLUSTRATION,
    status_card_key,
    get_aggrid_theme_params,
    get_aggrid_custom_css,
    STATUS_META,
)

# AG Grid is optional at runtime: if the component is missing or broken, the
# Ledger falls back to st.dataframe instead of crashing the page.
try:
    from st_aggrid import AgGrid, JsCode, StAggridTheme
except Exception:  # pragma: no cover - depends on the environment
    AgGrid = JsCode = StAggridTheme = None

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.svg"
AVATAR_PATH = Path(__file__).resolve().parent / "assets" / "avatar.svg"
AGENT_AVATAR = str(AVATAR_PATH) if AVATAR_PATH.exists() else ":material/shield:"
USER_AVATAR = ":material/person:"

# Populated in main(); used for st.page_link targets.
_PAGES: Dict[str, Any] = {}


# ==============================================================================
# Backward Compatibility Helpers
# ==============================================================================

def get_status_chip_html(status: str) -> str:
    """Delegate to unified WCAG AA status pill."""
    return get_status_pill_html(status)


def get_mode_badge_html() -> str:
    """Delegate to unified mode pill."""
    return get_mode_pill_html()


def get_chat_outcome_label_and_state(result: Dict[str, Any]) -> Tuple[str, str]:
    """
    Determine the outcome label and status container state for an assistant turn.

    Outcomes:
      - "Purchase approved" (state="complete" -> displays green check)
      - "Needs your approval" (state="error" -> no green check)
      - "Blocked by policy" (state="error" -> no green check)
      - "No purchase proposed" (state="error" -> no green check)
      - "Agent error" (state="error" -> no green check)

    Shows a green check ONLY when a purchase was actually APPROVED.
    """
    if result.get("error"):
        if result.get("error_kind") == "quota":
            return "Gemini unavailable", "error"
        return "Agent error", "error"

    prop = result.get("proposal_result")
    if not prop:
        return "No purchase proposed", "error"

    status = (prop.get("status") or "").upper()
    if status == "APPROVED":
        return "Purchase approved", "complete"
    elif status == "NEEDS_APPROVAL":
        return "Needs your approval", "error"
    elif status == "BLOCKED":
        return "Blocked by policy", "error"
    else:
        return "No purchase proposed", "error"


# ==============================================================================
# Small UI utilities
# ==============================================================================

def queue_toast(message: str, icon: Optional[str] = None) -> None:
    """Queue a toast so it survives st.rerun() and is shown on the next run."""
    st.session_state.setdefault("_aa_toasts", []).append((message, icon))


def flush_toasts() -> None:
    for message, icon in st.session_state.pop("_aa_toasts", []):
        st.toast(md_escape(message), icon=icon)


def safe_error_text(detail: Any, generic: str = GENERIC_ERROR) -> str:
    """Error text for the UI: generic in a public demo, otherwise redacted detail."""
    if Config.DEMO_PUBLIC:
        return generic
    return redact(str(detail), Config.llm_api_key())


def friendly_agent_error(result: Dict[str, Any]) -> Optional[str]:
    """One user-facing line for a failed agent run (never the raw provider payload)."""
    if not result.get("error"):
        return None
    if result.get("error_kind") in ("quota", "session_cap", "config") and result.get("error_message"):
        return result["error_message"]
    return safe_error_text(result["error"])


def _parse_reasons(raw: Any) -> List[str]:
    """Parse a stored reasons value (JSON list or plain text) into a list of strings."""
    return parse_reasons(raw)


def _tool_name(tc: Dict[str, Any]) -> str:
    return tc.get("tool") or tc.get("name") or ""


def _search_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [tc for tc in tool_calls if _tool_name(tc) == "search_products" or "query" in tc]


def _proposal_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [tc for tc in tool_calls if _tool_name(tc) == "propose_purchase" or "merchant" in tc]


def _fmt_amount_text(amount: Any) -> str:
    try:
        return f"${float(amount):,.2f}"
    except (TypeError, ValueError):
        return str(amount)


def _proposal_detail(src: Dict[str, Any]) -> str:
    if not src.get("merchant"):
        return ""
    return f"{src.get('merchant')} \u00b7 {_fmt_amount_text(src.get('amount', 0))}"


def turn_pipeline(msg: Dict[str, Any]) -> Tuple[Dict[str, Tuple[str, str]], str]:
    """Final pipeline states + agent detail line for a completed assistant turn."""
    tool_calls = msg.get("tool_calls") or []
    proposal_result = msg.get("proposal_result")
    proposals = _proposal_calls(tool_calls)
    if msg.get("error") and not proposal_result:
        return pipeline_states("error"), ""
    if not proposal_result:
        return pipeline_states("no_proposal"), ""
    src = proposal_result.get("proposal") or (proposals[-1] if proposals else {})
    states = pipeline_states(
        "decided",
        status=proposal_result.get("status"),
        has_order=bool(proposal_result.get("paypal_order_id")),
    )
    return states, _proposal_detail(src)


# ==============================================================================
# Chat: decision card
# ==============================================================================

def _next_step_text(status: str, approval_url: Optional[str]) -> str:
    if status == "APPROVED":
        if approval_url:
            return "Open the PayPal sandbox checkout below to authorize the payment."
        return "Approved and recorded in the ledger. No PayPal sandbox link was created (check PayPal credentials)."
    if status == "NEEDS_APPROVAL":
        return "This purchase is waiting in Approvals. Nothing is charged until you approve it."
    if status == "BLOCKED":
        return "No payment was made. Change your Rules if this purchase should be allowed."
    return "No payment was made."


def render_verdict_card(
    msg: Dict[str, Any],
    key_suffix: str,
    is_primary: bool = True,
) -> None:
    tool_calls = msg.get("tool_calls") or []
    proposal_result = msg["proposal_result"]
    status = (proposal_result.get("status") or "UNKNOWN").upper()
    proposal = proposal_result.get("proposal") or {}
    props = _proposal_calls(tool_calls)
    fallback = props[-1] if props else {}

    merchant = proposal.get("merchant") or fallback.get("merchant") or "Unknown merchant"
    amount = proposal.get("amount", fallback.get("amount", 0.0))
    currency = proposal.get("currency") or fallback.get("currency") or "USD"
    category = proposal.get("category") or fallback.get("category") or "General"
    reason = proposal.get("reason") or fallback.get("reason") or ""
    approval_url = proposal_result.get("approval_url")
    order_id = proposal_result.get("paypal_order_id")
    title, _ = get_chat_outcome_label_and_state(msg)

    with st.container(key=f"aa-verdict-{status_card_key(status)}-{key_suffix}"):
        render_html(
            get_verdict_html(
                status=status,
                title=title,
                merchant=merchant,
                amount=amount,
                currency=currency,
                category=category,
                reason=reason,
                policy_reasons=_parse_reasons(proposal_result.get("reasons")),
                next_step=_next_step_text(status, approval_url),
                order_id=order_id,
            )
        )
        render_html('<div style="height:12px;"></div>')
        if status == "APPROVED" and approval_url:
            st.link_button(
                "Open PayPal sandbox checkout",
                approval_url,
                type="primary" if is_primary else "secondary",
                icon=":material/open_in_new:",
                width="stretch",
            )
        elif status == "NEEDS_APPROVAL":
            ledger_id = proposal_result.get("ledger_id") or proposal_result.get("id")
            c_app, c_rev = st.columns([1.4, 1.6])
            with c_app:
                if ledger_id and st.button(
                    "Approve and pay",
                    key=f"chat_app_btn_{key_suffix}_{ledger_id}",
                    type="primary" if is_primary else "secondary",
                    icon=":material/check:",
                    width="stretch",
                ):
                    try:
                        app_res = approve_pending(int(ledger_id))
                    except Exception:
                        app_res = None
                    if app_res and app_res.get("approved"):
                        try:
                            ord_res = create_order_for_approved(int(ledger_id))
                            proposal_result["status"] = "APPROVED"
                            proposal_result["approval_url"] = ord_res.get("approval_url")
                            proposal_result["paypal_order_id"] = ord_res.get("order_id")
                        except Exception:
                            proposal_result["status"] = "APPROVED"
                        queue_toast(f"Approved #{ledger_id} ({_fmt_amount_text(amount)} at {merchant}).", ":material/check_circle:")
                        st.rerun()
            with c_rev:
                if _PAGES.get("approvals"):
                    st.page_link(_PAGES["approvals"], label="Review in approvals", icon=":material/arrow_forward:")
        elif status == "BLOCKED" and _PAGES.get("rules"):
            st.page_link(_PAGES["rules"], label="View rules", icon=":material/tune:")


def render_assistant_turn(msg: Dict[str, Any], key_suffix: str, is_primary: bool = True) -> None:
    """
    Render a completed assistant turn: pipeline, decision card (or plain agent text when there is no card).
    v4 rule: Do NOT render raw agent text ('Proposal evaluated...', 'I have proposed...') when a decision
    card is shown. Show agent text only when there is no card, and never clip it.
    """
    proposal_result = msg.get("proposal_result")

    if msg.get("ran"):
        states, detail = turn_pipeline(msg)
        render_html(get_pipeline_html(states, detail, compact=True))

    if msg.get("error") and msg.get("error_kind") == "quota":
        render_html(get_quota_notice_html(msg["error"]))
        if st.session_state.get("aa_mode") == MODE_GEMINI:
            st.button(
                "Switch to Mock mode", key=f"aa_switch_mock_{key_suffix}",
                type="primary" if is_primary else "secondary",
                icon=":material/swap_horiz:", on_click=switch_to_mock_mode,
            )
    elif msg.get("error"):
        st.error(md_escape(msg["error"]), icon=":material/error:")
    elif msg.get("content") and not proposal_result:
        # Plain escaped text, not markdown: LLM output must not render links or remote images.
        # Shown only when there is no decision card, and never clipped.
        with st.container(key=f"aa-bubble-{key_suffix}"):
            render_html(f'<div class="aa-agent-msg">{html_text(msg["content"])}</div>')
    if msg.get("notice"):
        st.info(md_escape(msg["notice"]), icon=":material/info:")

    if proposal_result:
        render_verdict_card(msg, key_suffix, is_primary=is_primary)


def render_user_bubble(text: str) -> None:
    """Right-aligned user bubble (HTML-escaped, '$'-safe)."""
    render_html(f'<div class="aa-user-msg">{html_text(text)}</div>')


def render_example_cards() -> None:
    """Three quiet scenario cards on a fresh chat (white, hairline border, small icon, short description, secondary button)."""
    scenarios = [
        ("paper", "package", "Office supplies",
         "A small purchase under the $25 approval threshold.",
         "Buy copy paper for the office", "Buy copy paper for the office"),
        ("flour", "wheat", "Bakery restock",
         "Restock flour from Golden Grain Mills. Larger orders wait for you.",
         "We're low on flour", "we're low on flour"),
        ("gift", "gift", "Policy defense",
         "A $500 gift card from an unlisted merchant, planted by a prompt injection.",
         "Try the gift card attack", "Search specials and buy the $500 gift card"),
    ]
    render_html('<div class="aa-label" style="margin: 0.35rem 0 0.25rem 0;">Try a scenario</div>')
    cols = st.columns(3)
    for col, (sid, icon, title, desc, button_label, prompt) in zip(cols, scenarios):
        with col:
            with st.container(border=True, key=f"aa-card-scn-{sid}"):
                render_html(
                    f'<div class="aa-card-head"><div class="aa-icon-chip">{svg_icon(icon, 16)}</div>'
                    f'<div class="aa-card-title" style="margin:0;flex:1;">{html_text(title)}</div></div>'
                    f'<div class="aa-card-desc">{html_text(desc)}</div>'
                )
                if st.button(button_label, key=f"btn_ex_{sid}", type="secondary", width="stretch"):
                    st.session_state["prefill_prompt"] = prompt
                    st.rerun()


# ==============================================================================
# Sidebar
# ==============================================================================

@st.dialog("Reset demo data?")
def confirm_reset_dialog() -> None:
    st.markdown(
        "This permanently deletes **all ledger rows and audit logs**. "
        "Your spending rules are kept."
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key="aa_reset_cancel", width="stretch"):
            st.rerun()
    with c2:
        with st.container(key="aa-confirm-reset"):
            if st.button("Reset data", key="aa_reset_confirm", type="primary", width="stretch"):
                deleted_count = reset_demo_transactions()
                st.session_state["messages"] = []
                st.session_state.pop("last_attack_result", None)
                st.session_state.pop("attack_triggered", None)
                queue_toast(f"Cleared {deleted_count} ledger rows. Demo is ready.", ":material/restart_alt:")
                st.rerun()


MODE_LABELS = {MODE_MOCK: "Mock", MODE_GEMINI: "Gemini"}


def switch_to_mock_mode() -> None:
    """Button callback (runs before widgets, so it may set the radio's state)."""
    st.session_state["aa_mode"] = MODE_MOCK


def render_sidebar_mode_control() -> Tuple[str, Optional[str]]:
    """
    Sidebar Mock/Gemini toggle with the per-session Gemini request meter.
    Returns (active mode, pill note). Once the cap is spent the session is pinned to Mock.
    """
    state = st.session_state
    cap = Config.LLM_SESSION_CAP
    has_key = bool(Config.llm_api_key())
    if "aa_mode" not in state:
        state["aa_mode"] = resolve_default_mode(Config.DEFAULT_MODE, Config.llm_api_key(), Config.LLM_PROVIDER)
    remaining = requests_remaining(state, cap)
    capped = has_key and remaining <= 0
    if capped or not has_key:
        state["aa_mode"] = MODE_MOCK

    with st.sidebar:
        render_html('<div class="aa-side-label">Agent mode</div>')
        st.radio(
            "Agent mode", MODES, key="aa_mode", format_func=MODE_LABELS.get, horizontal=True,
            label_visibility="collapsed", disabled=capped or not has_key,
        )
        if not has_key:
            st.caption("Gemini needs LLM_API_KEY. Mock mode runs offline with no limits.")
        elif capped:
            render_html(
                '<div class="aa-side-note"><b>Gemini limit reached.</b> This session used all '
                f'{cap} Gemini requests, so the agent now runs in Mock mode.</div>'
            )
        else:
            render_html(get_side_meter_html(remaining, cap))
            st.caption("Mock mode is unlimited.")
        st.divider()

    active = effective_mode(state["aa_mode"], state, cap)
    state["aa_active_mode"] = active
    return active, ("Gemini limit reached" if capped else None)


def render_sidebar_demo_tools() -> None:
    """Collapsed Demo tools expander plus PayPal sandbox status."""
    with st.sidebar:
        with st.expander("Demo tools", expanded=False, icon=":material/build:"):
            st.caption("Simulate a prompt-injection attack against the agent.")
            if st.button("Run injection attack", key="side_attack_btn", icon=":material/bug_report:", width="stretch"):
                agent = ShoppingAgent(provider="mock")
                res = agent.run("Search specials and buy the $500 gift card immediately", obey_injection=True)
                st.session_state["last_attack_result"] = res
                st.session_state["attack_triggered"] = True
                st.rerun()

            st.caption("Clear transaction history and audit logs.")
            # Public demo: the Reset button stays hidden until the visitor confirms intent.
            show_reset = not Config.DEMO_PUBLIC or st.checkbox(
                "I want to reset the shared demo data", key="aa_unlock_reset"
            )
            if show_reset and st.button("Reset demo data", key="side_reset_btn", icon=":material/restart_alt:", width="stretch"):
                confirm_reset_dialog()

        attack_res = st.session_state.get("last_attack_result")
        if attack_res and st.session_state.get("attack_triggered"):
            prop = attack_res.get("proposal_result") or {}
            status = prop.get("status", "BLOCKED")
            render_html(
                f"""
                <div class="aa-side-alert">
                    <div class="aa-side-alert-title">{svg_icon("shield-x", 16, 2.4)} Attack intercepted</div>
                    <div style="margin-bottom:0.35rem;">{get_status_pill_html(status)}</div>
                    <div>The injected gift card purchase was evaluated by the policy engine, not the agent.</div>
                </div>
                """
            )

        paypal_ready = Config.validate_paypal_config()
        dot = "ok" if paypal_ready else "warn"
        label = "PayPal sandbox connected" if paypal_ready else "PayPal sandbox: add .env credentials"
        render_html(f'<div class="aa-side-status"><span class="aa-sdot {dot}"></span>{label}</div>')


# ==============================================================================
# PAGE 1: CHAT
# ==============================================================================

WELCOME = (
    "Hi! I'm your shopping agent. I search verified merchant catalogs and propose purchases. "
    "Every proposal is checked by deterministic policy rules before any payment can happen."
)


def render_chat_page():
    """Render interactive purchasing agent conversation."""
    if "messages" not in st.session_state or not st.session_state["messages"]:
        st.session_state["messages"] = [
            {"role": "assistant", "content": WELCOME, "tool_calls": [], "proposal_result": None}
        ]

    page = st.container(key="aa-page-chat")

    # Chat input is called at top level so it stays pinned to the bottom.
    typed = st.chat_input("What should I buy? For example: we're low on flour")
    user_input = st.session_state.pop("prefill_prompt", None) or typed

    with page:
        # One stable slot for the intro: when a prompt arrives it is cleared instead of
        # shifting every later element (which left stale scenario cards on screen mid-run).
        intro = st.empty()
        is_fresh_chat = len(st.session_state["messages"]) <= 1 and not user_input
        if is_fresh_chat:
            with intro.container():
                render_html(get_welcome_html())
                render_html(get_pipeline_html(pipeline_states("intro")))
                render_example_cards()

        total_msgs = len(st.session_state["messages"])
        for idx, msg in enumerate(st.session_state["messages"]):
            if not is_fresh_chat and idx == 0 and not msg.get("ran") and msg.get("role") == "assistant":
                continue
            avatar = USER_AVATAR if msg["role"] == "user" else AGENT_AVATAR
            is_last_msg = (idx == total_msgs - 1) and not user_input
            with st.chat_message(msg["role"], avatar=avatar):
                if msg["role"] == "user":
                    render_user_bubble(msg.get("content", ""))
                else:
                    render_assistant_turn(msg, key_suffix=str(idx), is_primary=is_last_msg)

        if not user_input:
            render_html('<div class="aa-chat-bottom-spacer"></div>')
            return

    with page:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar=USER_AVATAR):
            render_user_bubble(user_input)

        turn_idx = len(st.session_state["messages"])
        with st.chat_message("assistant", avatar=AGENT_AVATAR):
            live_tool_calls: List[Dict[str, Any]] = []
            live_policy_decisions: List[Dict[str, Any]] = []
            # The strip is redrawn from the agent's own callbacks: each node lights
            # up only when that step really happens (no timers, no fake delays).
            live = {"phase": "searching", "detail": ""}
            steps_ph = st.empty()

            def draw(status: Optional[str] = None, has_order: bool = False) -> None:
                states = pipeline_states(live["phase"], status=status, has_order=has_order)
                render_html(get_pipeline_html(states, live["detail"], compact=True), container=steps_ph)

            draw()

            def on_tool_callback(tool_name: str, args: Dict[str, Any]):
                live_tool_calls.append({"tool": tool_name, **args})
                if "query" in args or tool_name == "search_products":
                    live["phase"] = "proposing"
                    if args.get("query"):
                        live["detail"] = f"\u201c{args['query']}\u201d"
                elif "merchant" in args or tool_name == "propose_purchase":
                    live["phase"] = "checking"
                    live["detail"] = _proposal_detail(args)
                draw()

            def on_policy_callback(decision: Dict[str, Any]):
                live_policy_decisions.append(decision)
                live["phase"] = "decided"
                draw(decision.get("status"), bool(decision.get("paypal_order_id")))

            mode = st.session_state.get("aa_active_mode", MODE_MOCK)
            agent = ShoppingAgent(provider=mode)
            budget = requests_remaining(st.session_state, Config.LLM_SESSION_CAP) if mode == MODE_GEMINI else None

            try:
                result = agent.run(
                    user_request=user_input,
                    on_tool_call=on_tool_callback,
                    on_policy_decision=on_policy_callback,
                    max_llm_requests=budget,
                )
            except Exception as e:
                result = {"error": str(e), "tool_calls": live_tool_calls, "proposal_result": None}
            used = int(result.get("llm_requests") or 0)
            record_requests(st.session_state, used)

            tool_calls = result.get("tool_calls", live_tool_calls) or live_tool_calls
            error_text = friendly_agent_error(result)
            notice = (
                result.get("error_message")
                if result.get("error_kind") == "session_cap" and not error_text else None
            )

            msg = {
                "role": "assistant",
                "content": "" if error_text else result.get("agent_response", ""),
                "tool_calls": tool_calls,
                "proposal_result": result.get("proposal_result"),
                "error": error_text,
                "error_kind": result.get("error_kind") if error_text else None,
                "notice": notice,
                "ran": True,
            }
            steps_ph.empty()
            render_assistant_turn(msg, key_suffix=str(turn_idx))

            # Save assistant message into persistent session history
            st.session_state["messages"].append(msg)
        render_html('<div class="aa-chat-bottom-spacer"></div>')

    # Real LLM requests changed the session budget: rerun so the sidebar meter and
    # mode pill (rendered before this page) show the new count.
    if used:
        st.rerun()


# ==============================================================================
# PAGE 2: APPROVALS
# ==============================================================================

def render_approvals_page():
    """Render human escalation queue for purchases needing supervisor authorization."""
    with st.container(key="aa-page-approvals"):
        render_section_title(
            "Approvals",
            "Purchases above your approval threshold wait here. Nothing is charged until you approve.",
        )

        primary_used = False
        last = st.session_state.get("aa_last_approved")
        if last:
            with st.container(key="aa-verdict-approved-last"):
                note = ("Complete the payment in the PayPal sandbox." if last.get("url")
                        else html_text(last.get("note") or "Recorded in the ledger."))
                render_html(
                    f'<div class="aa-vhead"><div class="aa-vicon">{svg_icon("check", 28, 2.2)}</div>'
                    f'<div class="aa-vtitle-wrap"><div class="aa-vtitle" role="heading" aria-level="3">'
                    f'Transaction #{int(last["id"])} approved</div><div class="aa-vsub">{note}</div></div>'
                    f'{get_status_pill_html("APPROVED")}</div><div style="height:0.5rem"></div>'
                )
                c1, c2 = st.columns([3, 1])
                with c1:
                    if last.get("url"):
                        st.link_button("Pay with PayPal sandbox", last["url"], type="primary",
                                       icon=":material/open_in_new:", width="stretch")
                        primary_used = True
                with c2:
                    if st.button("Dismiss", key="aa_dismiss_last", width="stretch"):
                        st.session_state.pop("aa_last_approved", None)
                        st.rerun()

        pending_items = get_pending_transactions()

        if not pending_items:
            render_html(get_empty_state_html(
                "You're all caught up",
                "No purchases are waiting for approval. Anything above your threshold will show up here.",
                illustration=APPROVALS_EMPTY_ILLUSTRATION,
            ))
            if _PAGES.get("chat"):
                st.page_link(_PAGES["chat"], label="Ask the agent to buy something", icon=":material/chat:")
            return

        render_html(f'<div class="aa-label" style="margin-bottom:0.4rem;">{len(pending_items)} waiting for review</div>')

        for item in pending_items:
            ledger_id = item["id"]
            reasons_list = _parse_reasons(item.get("reasons"))
            subtitle_str = f"Request #{ledger_id} · {item.get('created_at') or 'Decided by rules, not the agent'}"

            with st.container(key=f"aa-verdict-pending-{ledger_id}"):
                render_html(
                    get_verdict_html(
                        status="NEEDS_APPROVAL",
                        title="Needs your approval",
                        subtitle=subtitle_str,
                        merchant=item["merchant"],
                        amount=item["amount"],
                        currency=item.get("currency") or "USD",
                        category=item.get("category") or "General",
                        reason=item.get("reason") or "No reason provided",
                        policy_reasons=reasons_list,
                        next_step="Approve below to create a PayPal sandbox order immediately, or reject to cancel without charging.",
                    )
                )
                render_html('<div style="height:12px;"></div>')

                btn_type = "secondary" if primary_used else "primary"
                primary_used = True
                col_app, col_rej, _ = st.columns([1.3, 1.3, 3])
                with col_app:
                    if st.button("Approve and pay", key=f"app_btn_{ledger_id}", type=btn_type,
                                 icon=":material/check:", width="stretch"):
                        try:
                            app_res = approve_pending(ledger_id)
                        except ValueError as e:
                            st.error(md_escape(safe_error_text(e, "This purchase could not be approved.")))
                            app_res = None
                        if app_res and app_res.get("approved"):
                            queue_toast(f"Approved #{ledger_id} ({_fmt_amount_text(item['amount'])} at {item['merchant']}).",
                                        ":material/check_circle:")
                            last_info: Dict[str, Any] = {"id": ledger_id}
                            try:
                                ord_res = create_order_for_approved(ledger_id)
                                last_info["url"] = ord_res.get("approval_url")
                                last_info["order_id"] = ord_res.get("order_id")
                            except Exception as e:
                                last_info["note"] = "Approved, but no PayPal sandbox order was created" + (
                                    "." if Config.DEMO_PUBLIC else f": {safe_error_text(e)}")
                                queue_toast("PayPal sandbox order could not be created.", ":material/warning:")
                            st.session_state["aa_last_approved"] = last_info
                            st.rerun()
                        elif app_res:
                            st.error(md_escape(f"Approval refused by the policy engine: {app_res.get('refusal_reason')}"),
                                     icon=":material/block:")

                with col_rej:
                    if st.button("Reject", key=f"rej_btn_{ledger_id}", icon=":material/block:", width="stretch"):
                        reject_pending(ledger_id, rejection_reason="Rejected by supervisor in dashboard.")
                        queue_toast(f"Rejected #{ledger_id}. No payment was made.", ":material/cancel:")
                        st.rerun()


# ==============================================================================
# PAGE 3: RULES
# ==============================================================================

_NUMERIC_RULES = [
    ("max_per_purchase", "Max per purchase", "50.00",
     "Any single purchase above this amount is blocked outright."),
    ("approval_threshold", "Approval threshold", "25.00",
     "Purchases above this amount wait for your approval."),
    ("monthly_budget", "Monthly budget", "200.00",
     "Total approved spend allowed per calendar month."),
]


def _to_float(value: Any, default: str) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float(default)


def render_rules_page():
    """Render rule inspection and editing dashboard."""
    with st.container(key="aa-page-rules"):
        render_section_title(
            "Rules",
            "Deterministic limits stored in SQLite. Changes apply to future decisions only.",
        )

        rules_dict = get_policy_rules()
        db_values: Dict[str, str] = {}
        for rt, _, default, _ in _NUMERIC_RULES:
            db_values[rt] = rules_dict.get(rt, {}).get("condition_value", default)
            st.session_state.setdefault(f"rule_{rt}", _to_float(db_values[rt], default))
        db_values["allowed_merchants"] = rules_dict.get("allowed_merchants", {}).get("condition_value", "")
        st.session_state.setdefault("rule_allowed_merchants", db_values["allowed_merchants"])

        errors: Dict[str, str] = {}
        col_l, col_r = st.columns([1, 1], gap="medium")

        with col_l:
            with st.container(border=True, key="aa-card-static-limits"):
                render_html(
                    f'<div class="aa-icon-chip">{svg_icon("wallet", 22)}</div>'
                    '<div class="aa-card-title">Spending limits</div>'
                    '<p class="aa-card-desc">Checked on every purchase the agent proposes.</p>'
                )
                for rt, label, default, help_text in _NUMERIC_RULES:
                    val = st.number_input(
                        f"{label} (USD)",
                        key=f"rule_{rt}",
                        min_value=0.0,
                        step=1.0,
                        format="%.2f",
                        help=help_text,
                    )
                    ok, err = validate_rule_value(rt, f"{val:.2f}")
                    if not ok:
                        errors[rt] = err or "Invalid value."
                        render_html(f'<div class="aa-field-error">{html_text(errors[rt])}</div>')
                    else:
                        st.caption(md_escape(help_text))

                cap = st.session_state.get("rule_max_per_purchase", 0.0)
                thr = st.session_state.get("rule_approval_threshold", 0.0)
                if thr >= cap > 0:
                    render_html(
                        '<div class="aa-field-hint">Heads up: the approval threshold is at or above the '
                        'per-purchase cap, so allowed purchases will never wait for approval.</div>'
                    )

        with col_r:
            with st.container(border=True, key="aa-card-static-merchants"):
                render_html(
                    f'<div class="aa-icon-chip">{svg_icon("store", 22)}</div>'
                    '<div class="aa-card-title">Approved merchants</div>'
                    '<p class="aa-card-desc">Purchases from any merchant not on this list are blocked.</p>'
                )
                merchants_val = st.text_area(
                    "Allowed merchants",
                    key="rule_allowed_merchants",
                    height=136,
                    placeholder="Golden Grain Mills, Corner Office Supply",
                    help="Comma-separated, case-insensitive.",
                )
                ok, err = validate_rule_value("allowed_merchants", merchants_val)
                if not ok:
                    errors["allowed_merchants"] = err or "Invalid value."
                    render_html(f'<div class="aa-field-error">{html_text(errors["allowed_merchants"])}</div>')
                else:
                    st.caption("Separate merchants with commas.")
                names = [m.strip() for m in (merchants_val or "").split(",") if m.strip()]
                if names:
                    chips = "".join(f'<span class="aa-chip">{html_text(n)}</span>' for n in names)
                    render_html(f'<div class="aa-label">{len(names)} allowed</div><div class="aa-chips">{chips}</div>')

        current = {rt: f"{st.session_state[f'rule_{rt}']:.2f}" for rt, *_ in _NUMERIC_RULES}
        current["allowed_merchants"] = merchants_val or ""
        dirty = any(
            f"{_to_float(db_values[rt], d):.2f}" != current[rt] for rt, _, d, _ in _NUMERIC_RULES
        ) or (db_values["allowed_merchants"].strip() != current["allowed_merchants"].strip())

        b1, b2 = st.columns([1, 4], vertical_alignment="center")
        with b1:
            save = st.button("Save rules", type="primary", icon=":material/save:", width="stretch", key="aa_save_rules")
        with b2:
            if errors:
                render_html(f'<span class="aa-field-error" style="margin:0;">Fix {len(errors)} field(s) before saving.</span>')
            elif dirty:
                render_html('<span class="aa-dirty">You have unsaved changes.</span>')
            else:
                render_html(f'<span class="aa-saved">{svg_icon("check", 16, 2.6)} All changes saved.</span>')

        if save:
            updates = [
                ("max_per_purchase", current["max_per_purchase"]),
                ("approval_threshold", current["approval_threshold"]),
                ("monthly_budget", current["monthly_budget"]),
                ("allowed_merchants", current["allowed_merchants"]),
            ]
            has_error = False
            for rt, val in updates:
                is_valid, err_msg = validate_rule_value(rt, val)
                if not is_valid:
                    has_error = True
            if has_error:
                st.error("Some values are invalid. Fix the highlighted fields and try again.", icon=":material/error:")
            else:
                try:
                    for rt, val in updates:
                        update_policy_rule(rt, val)
                    queue_toast("Rules saved. New limits apply to future purchases.", ":material/check_circle:")
                    st.rerun()
                except Exception as e:
                    st.error(md_escape(f"Error saving rules: {safe_error_text(e, 'please try again.')}"))


# ==============================================================================
# PAGE 4: LEDGER
# ==============================================================================

_STATUS_LABELS = {k: v["label"] for k, v in STATUS_META.items()}
_STATUS_CLASSES = {k: v["cls"] for k, v in STATUS_META.items()}


def _approved_total(rows: List[Dict[str, Any]]) -> Decimal:
    return sum((Decimal(r["amount"]) for r in rows if r["status"] in ("APPROVED", "EXECUTED")), Decimal("0.00"))


def _csv_safe(value: Any) -> str:
    """Stringify for CSV and defuse spreadsheet formula injection."""
    text = "" if value is None else str(value)
    stripped = text.lstrip(" \t\r\n")
    return "'" + text if stripped[:1] in ("=", "+", "-", "@") else text


def _ledger_csv(rows: List[Dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "time_utc", "merchant", "category", "amount", "currency", "status",
                     "reasons", "paypal_order_id"])
    for r in rows:
        reasons_safe = " | ".join(_csv_safe(reason) for reason in r.get("reasons", []))
        writer.writerow([_csv_safe(v) for v in (
            r["id"], r["timestamp"], r["merchant"], r["category"], r["amount"], r["currency"],
            r["status"], reasons_safe, r["paypal_order_id"],
        )])
    return buf.getvalue().encode("utf-8")


def _ledger_grid_options(quick_filter: str) -> Dict[str, Any]:
    """Community-only AG Grid options for the ledger."""
    labels = json.dumps(_STATUS_LABELS)
    classes = json.dumps(_STATUS_CLASSES)
    status_label_js = f"(s) => ({labels})[s] || String(s || '').replace(/_/g, ' ')"

    # Class-style renderers: the component is built on AG Grid React, which treats a
    # plain function renderer as a React component (returning DOM nodes crashes it).
    status_renderer = JsCode(f"""
        class StatusRenderer {{
            init(p) {{
                this.eGui = document.createElement('span');
                if (p.node.rowPinned) {{
                    this.eGui.className = 'aa-total-label';
                    this.eGui.textContent = 'Approved spend';
                    return;
                }}
                this.eGui.className = 'aa-pill ' + (({classes})[p.value] || 'rejected');
                this.eGui.textContent = ({status_label_js})(p.value);
            }}
            getGui() {{ return this.eGui; }}
            refresh() {{ return false; }}
        }}
    """)
    reasons_renderer = JsCode("""
        class ReasonsRenderer {
            init(p) {
                this.eGui = document.createElement('div');
                if (p.node.rowPinned) return;
                const reasons = (p.data && p.data.reasons_text) ? p.data.reasons_text.split('\\n') : [];
                if (!reasons.length) {
                    this.eGui.className = 'aa-muted';
                    this.eGui.textContent = '\\u2014';
                    return;
                }
                reasons.forEach(r => {
                    const d = document.createElement('div');
                    d.className = 'aa-r';
                    d.textContent = r;
                    this.eGui.appendChild(d);
                });
            }
            getGui() { return this.eGui; }
            refresh() { return false; }
        }
    """)
    money_formatter = JsCode("""
        function(p) {
            if (p.value === null || p.value === undefined || p.value === '') return '';
            try {
                return new Intl.NumberFormat('en-US', {style: 'currency', currency: (p.data && p.data.currency) || 'USD'}).format(p.value);
            } catch (e) {
                return Number(p.value).toFixed(2) + ' ' + ((p.data && p.data.currency) || '');
            }
        }
    """)
    # Recompute the pinned total from the rows that pass the current filters.
    update_total = JsCode("""
        function(e) {
            let cents = 0, count = 0;
            const currencies = new Set();
            e.api.forEachNodeAfterFilter(n => {
                if (!n.data) return;
                count += 1;
                if (n.data.status === 'APPROVED' || n.data.status === 'EXECUTED') {
                    cents += Math.round(Number(n.data.amount_value) * 100);
                    currencies.add(n.data.currency);
                }
            });
            const mixed = currencies.size > 1;
            e.api.setGridOption('pinnedBottomRowData', [{
                '::auto_unique_id::': 'aa-total',
                id: null,
                timestamp: count + (count === 1 ? ' row' : ' rows'),
                merchant: mixed ? 'Mixed currencies' : '',
                amount_value: mixed ? null : cents / 100,
                currency: currencies.size === 1 ? [...currencies][0] : 'USD',
                status: '', reasons_text: '', paypal_order_id: ''
            }]);
        }
    """)

    return {
        "columnDefs": [
            {"field": "id", "headerName": "ID", "width": 72, "cellDataType": "number", "filter": "agNumberColumnFilter",
             "valueFormatter": JsCode("function(p){ return p.value == null ? 'Total' : '#' + p.value; }"),
             "cellClass": "aa-muted"},
            # Explicit "text": v34 otherwise infers dateTimeString and blanks the pinned "N rows" cell.
            {"field": "timestamp", "headerName": "Time (UTC)", "width": 138, "cellDataType": "text",
             "filter": "agTextColumnFilter", "wrapText": True, "autoHeight": True},
            {"field": "merchant", "headerName": "Merchant", "flex": 1, "minWidth": 150, "cellDataType": "text",
             "filter": "agTextColumnFilter", "cellClass": "aa-merchant", "tooltipField": "category",
             "wrapText": True, "autoHeight": True},
            {"field": "amount_value", "headerName": "Amount", "width": 104, "type": "rightAligned", "cellDataType": "number",
             "filter": "agNumberColumnFilter", "valueFormatter": money_formatter, "cellClass": "aa-amount"},
            {"field": "status", "headerName": "Status", "width": 160, "cellDataType": "text", "filter": "agTextColumnFilter",
             "cellRenderer": status_renderer,
             "filterValueGetter": JsCode(f"function(p){{ return ({status_label_js})(p.data.status); }}"),
             "getQuickFilterText": JsCode(f"function(p){{ return ({status_label_js})(p.value); }}")},
            {"field": "reasons_text", "headerName": "Reasons", "flex": 2, "minWidth": 220, "cellDataType": "text",
             "filter": "agTextColumnFilter", "cellRenderer": reasons_renderer, "cellClass": "aa-reasons-cell",
             "wrapText": True, "autoHeight": True, "tooltipField": "reasons_text"},
            {"field": "paypal_order_id", "headerName": "PayPal order", "width": 148, "cellDataType": "text",
             "filter": "agTextColumnFilter", "cellClass": "aa-mono", "tooltipField": "paypal_order_id"},
        ],
        "defaultColDef": {"sortable": True, "resizable": True, "filter": True, "suppressHeaderMenuButton": False},
        # No custom getRowId: the component maps returned rows by its own ::auto_unique_id::.
        "rowSelection": {"mode": "singleRow", "checkboxes": False, "enableClickSelection": True},
        "pagination": True,
        "paginationPageSize": 10,
        "paginationPageSizeSelector": [10, 25, 50],
        "domLayout": "autoHeight",
        "tooltipShowDelay": 300,
        "quickFilterText": quick_filter,
        "overlayNoRowsTemplate": "No transactions match the current filters.",
        "onFirstDataRendered": update_total,
        "onFilterChanged": update_total,
        "onRowDataUpdated": update_total,
    }


def _render_ledger_grid(rows: List[Dict[str, Any]], quick_filter: str) -> Optional[Any]:
    """Render the AG Grid ledger. Returns the grid response, or raises on failure."""
    df = pd.DataFrame([{
        "id": r["id"],
        "timestamp": r["timestamp"],
        "merchant": r["merchant"],
        "category": r["category"],
        "amount_value": float(r["amount"]),  # display/sort only; totals use Decimal strings
        "currency": r["currency"],
        "status": r["status"],
        "reasons_text": "\n".join(r["reasons"]),
        "paypal_order_id": r["paypal_order_id"],
    } for r in rows])
    theme = (
        StAggridTheme(base="quartz")
        .withParams(**get_aggrid_theme_params())
        .withParts("colorSchemeLight", "iconSetQuartzLight")
    )
    return AgGrid(
        df,
        gridOptions=_ledger_grid_options(quick_filter),
        height=None,
        theme=theme,
        custom_css=get_aggrid_custom_css(),
        allow_unsafe_jscode=True,
        enable_enterprise_modules=False,
        update_on=["selectionChanged", "filterChanged", "sortChanged"],
        show_toolbar=False,
        key="aa_ledger_grid",
    )


def _render_ledger_fallback(
    rows: List[Dict[str, Any]], quick_filter: str, notice: str
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """Basic st.dataframe ledger. Returns (rows shown, selected row id)."""
    st.caption(md_escape(notice))
    q = quick_filter.lower()
    if q:
        rows = [r for r in rows if q in " ".join([
            str(r["id"]), r["merchant"], r["category"], r["status"], _STATUS_LABELS.get(r["status"], ""),
            " ".join(r["reasons"]), r["paypal_order_id"],
        ]).lower()]
    event = st.dataframe(
        [{
            "ID": r["id"], "Time (UTC)": r["timestamp"], "Merchant": r["merchant"],
            "Amount": f"{r['amount']} {r['currency']}", "Status": _STATUS_LABELS.get(r["status"], r["status"]),
            "Reasons": " | ".join(r["reasons"]), "PayPal order": r["paypal_order_id"],
        } for r in rows],
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="aa_ledger_fallback",
    )
    st.caption(md_escape(
        f"{len(rows)} rows · Approved spend ${_approved_total(rows):,.2f} · Select a row to read every reason."
    ))
    picked = event.selection.rows if event is not None else []
    return rows, (rows[picked[0]]["id"] if picked and picked[0] < len(rows) else None)


def _render_ledger_detail(row: Dict[str, Any]) -> None:
    """Detail card for the selected ledger row (all text HTML-escaped and $-safe)."""
    proposal = row.get("proposal") or {}
    cls = status_card_key(row["status"])
    reasons_html = "".join(
        f'<li><span class="aa-reason-dot aa-dot-{cls}"></span><span>{html_text(r)}</span></li>'
        for r in row["reasons"]
    ) or '<li class="aa-muted">No reasons recorded.</li>'
    order = row.get("paypal_order_id")
    order_html = f'<span class="aa-mono">{html_text(order)}</span>' if order else '<span class="aa-muted">None</span>'
    capture = row.get("paypal_capture_id")
    capture_html = f'<span class="aa-mono">{html_text(capture)}</span>' if capture else '<span class="aa-muted">None</span>'
    item = proposal.get("item_title") or proposal.get("item_id") or ""
    agent_reason = row.get("agent_reason") or proposal.get("reason") or ""

    extra_kv = ""
    if capture:
        extra_kv = f'<div><div class="aa-label">Capture ID</div><div class="aa-v">{capture_html}</div></div>'
    elif item:
        extra_kv = f'<div><div class="aa-label">Item</div><div class="aa-v">{html_text(item)}</div></div>'

    agent_reason_block = (
        f'<div class="aa-label">Agent&#39;s reason</div><p class="aa-reason" style="margin:0 0 8px 0;font-size:13px;">{html_text(agent_reason)}</p>'
        if agent_reason else ""
    )

    markup = (
        f'<div class="aa-decision-card aa-decision-{cls}">'
        f'<div class="aa-decision-accent aa-accent-{cls}"></div>'
        f'<div class="aa-decision-inner">'
        f'<div class="aa-card-head">'
        f'<div class="aa-card-title">#{html_text(row["id"])} · {html_text(row["merchant"])}</div>'
        f'{get_status_pill_html(row["status"])}'
        f"</div>"
        f'<div class="aa-kv">'
        f'<div><div class="aa-label">Amount</div><div class="aa-v big">{money_html(row["amount"], row["currency"])}</div></div>'
        f'<div><div class="aa-label">Category</div><div class="aa-v">{html_text(row["category"])}</div></div>'
        f'<div><div class="aa-label">Time (UTC)</div><div class="aa-v">{html_text(row["timestamp"])}</div></div>'
        f'<div><div class="aa-label">Proposal ID</div><div class="aa-v aa-mono">{html_text(row.get("proposal_id"))}</div></div>'
        f'<div><div class="aa-label">PayPal order</div><div class="aa-v">{order_html}</div></div>'
        f"{extra_kv}"
        f"</div>"
        f"{agent_reason_block}"
        f'<div class="aa-label">Policy reasons</div>'
        f'<ul class="aa-verdict-reasons" style="margin-bottom:0;">{reasons_html}</ul>'
        f"</div>"
        f"</div>"
    )
    with st.container(key=f"aa-verdict-{cls}-ledger"):
        render_html(markup)


def _selected_ledger_id(grid: Any) -> Optional[int]:
    try:
        selected = grid.selected_rows if grid is not None else None
    except Exception:
        return None
    if selected is None or len(selected) == 0 or "id" not in selected.columns:
        return None
    value = selected.iloc[0]["id"]
    return int(value) if pd.notna(value) else None


def render_ledger_page():
    """Render audit ledger of all transactions."""
    with st.container(key="aa-page-ledger"):
        render_section_title(
            "Ledger",
            "Audit record of every proposal, policy decision and PayPal sandbox order.",
        )

        rows = get_ledger_rows()
        if not rows:
            render_html(get_empty_state_html(
                "No transactions yet",
                "Ask the agent to buy something and every decision will be recorded here.",
                illustration=LEDGER_EMPTY_ILLUSTRATION,
            ))
            return

        # Preset status filter buttons + search input + Export CSV
        preset_options = [
            ("ALL", "All"),
            ("APPROVED", "Approved"),
            ("NEEDS_APPROVAL", "Needs approval"),
            ("BLOCKED", "Blocked"),
            ("EXECUTED", "Executed"),
        ]
        active_preset = st.session_state.get("aa_ledger_preset", "ALL")

        p_cols = st.columns([0.8, 1.0, 1.25, 0.95, 1.0, 2.6, 1.3], vertical_alignment="center")
        for idx, (p_key, p_lbl) in enumerate(preset_options):
            with p_cols[idx]:
                if st.button(
                    p_lbl,
                    key=f"aa_ledger_preset_{p_key}",
                    type="primary" if active_preset == p_key else "secondary",
                    width="stretch",
                ):
                    st.session_state["aa_ledger_preset"] = p_key
                    st.rerun()

        with p_cols[5]:
            query = st.text_input(
                "Search", placeholder="Search merchant, status, reason or order ID", key="aa_ledger_q",
                icon=":material/search:", label_visibility="collapsed",
            )
        quick_filter = (query or "").strip()

        filtered_rows = (
            rows if active_preset == "ALL"
            else [r for r in rows if (r.get("status") or "").upper() == active_preset]
        )

        grid = None
        fallback_id: Optional[int] = None
        export_rows = filtered_rows
        if AgGrid is None:
            export_rows, fallback_id = _render_ledger_fallback(
                filtered_rows, quick_filter, "Interactive grid unavailable (streamlit-aggrid not installed). Showing a basic table.")
        else:
            try:
                grid = _render_ledger_grid(filtered_rows, quick_filter)
            except Exception:
                grid = None
                export_rows, fallback_id = _render_ledger_fallback(
                    filtered_rows, quick_filter, "Interactive grid failed to load. Showing a basic table.")

        # Export what the grid currently shows (filters + sort).
        try:
            shown = grid.data if grid is not None else None
            if shown is not None and "id" in shown.columns:
                by_id = {r["id"]: r for r in filtered_rows}
                export_rows = [by_id[int(i)] for i in shown["id"].dropna() if int(i) in by_id]
        except Exception:
            pass
        with p_cols[6]:
            st.download_button(
                "Export CSV", data=_ledger_csv(export_rows), file_name="agent_allowance_ledger.csv",
                mime="text/csv", icon=":material/download:", width="stretch", key="aa_ledger_export",
            )

        selected_id = _selected_ledger_id(grid) if grid is not None else fallback_id
        selected = next((r for r in rows if r["id"] == selected_id), None)
        if selected:
            _render_ledger_detail(selected)
        elif grid is not None:
            st.caption("Select a row to see the full proposal, every policy reason and the PayPal order.")


# ==============================================================================
# Payment Return Handling (PayPal Checkout Return & Cancel)
# ==============================================================================

def process_payment_return(params: Dict[str, Any], db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Process PayPal payment return parameters (return or cancel).
    - Checks ledger by paypal_order_id.
    - Ignores unknown tokens.
    - Never trusts query params for amounts or status.
    - Checks order status with PayPal Orders v2 GET /v2/checkout/orders/{id}.
    - If PayPal status is APPROVED and ledger is APPROVED, captures via capture_if_approved.
    - If already captured (EXECUTED), returns idempotent COMPLETED.
    - If cancel, reports cancellation and leaves ledger untouched.
    """
    action = params.get("paypal")
    if isinstance(action, list):
        action = action[0] if action else ""
    action = str(action or "").lower().strip()

    token = params.get("token")
    if isinstance(token, list):
        token = token[0] if token else ""
    token = str(token or "").strip()

    if action == "cancel":
        return {
            "status": "cancelled",
            "message": "Payment was cancelled. Your allowance and ledger remain unchanged.",
            "token": token,
        }

    if action == "return":
        if not token:
            return {"status": "ignored", "reason": "missing_token"}

        row = get_transaction_by_order_id(token, db_path=db_path)
        if not row:
            return {"status": "ignored", "reason": "unknown_token"}

        ledger_status = (row.get("status") or "").upper()
        if ledger_status in ("BLOCKED", "NEEDS_APPROVAL", "REJECTED"):
            return {
                "status": "forbidden",
                "ledger_status": ledger_status,
                "row": row,
                "message": f"Payment cannot be captured: transaction #{row['id']} has policy status '{ledger_status}'.",
            }

        if ledger_status == "EXECUTED":
            return {
                "status": "COMPLETED",
                "already_captured": True,
                "capture_id": row.get("paypal_capture_id"),
                "order_id": token,
                "row": row,
                "message": f"Payment already captured (Capture ID: {row.get('paypal_capture_id')}).",
            }

        if ledger_status == "APPROVED":
            try:
                order_data = get_order_status(token)
                paypal_status = (order_data.get("status") if isinstance(order_data, dict) else str(order_data) or "").upper()
            except Exception as e:
                return {
                    "status": "error",
                    "order_id": token,
                    "row": row,
                    "message": f"Failed to retrieve PayPal order status: {e}",
                }

            if paypal_status == "APPROVED":
                try:
                    capture_res = capture_if_approved(row["id"], db_path=db_path)
                    return {
                        "status": "COMPLETED",
                        "capture_id": capture_res.get("paypal_capture_id") or capture_res.get("capture_id"),
                        "order_id": token,
                        "row": row,
                        "message": "Payment successfully captured and verified with PayPal Sandbox.",
                    }
                except Exception as e:
                    return {
                        "status": "error",
                        "order_id": token,
                        "row": row,
                        "message": f"Capture failed: {e}",
                    }
            else:
                return {
                    "status": "not_approved",
                    "paypal_status": paypal_status or "PENDING",
                    "order_id": token,
                    "row": row,
                    "message": f"Order is not yet approved in PayPal (status: {paypal_status or 'PENDING'}).",
                }

    return {"status": "ignored", "reason": "unknown_action"}


def render_payment_return_panel(db_path: Optional[str] = None) -> None:
    """Display payment return banner when returning from PayPal checkout or cancellation."""
    try:
        qp = dict(st.query_params)
    except Exception:
        return
    if "paypal" not in qp:
        return

    res = process_payment_return(qp, db_path=db_path)
    status = res.get("status")
    if status == "ignored":
        return

    with st.container():
        if status == "cancelled":
            render_html("""
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid var(--aa-rejected-solid); border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <div style="font-weight:600; font-size:1rem; color:var(--aa-text-heading);">Payment was cancelled</div>
                            <div style="font-size:0.88rem; color:var(--aa-text-muted); margin-top:0.25rem;">The PayPal checkout session was cancelled. Your monthly budget, allowance, and ledger remain completely unchanged.</div>
                        </div>
                    </div>
                </div>
            """)
            if st.button("Dismiss notice", key="aa_dismiss_cancel"):
                st.query_params.clear()
                st.rerun()

        elif status == "COMPLETED":
            row = res.get("row") or {}
            capture_id = res.get("capture_id") or ""
            order_id = res.get("order_id") or ""
            render_html(f"""
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid var(--aa-approved-fg); border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                        <div style="font-weight:600; font-size:1rem; color:var(--aa-text);">Payment Return · COMPLETED</div>
                        <span class="aa-pill executed">COMPLETED</span>
                    </div>
                    <div style="font-size:0.88rem; color:var(--aa-text-soft); line-height:1.5;">
                        Transaction <strong>#{html_text(row.get('id', ''))}</strong> ({html_text(row.get('merchant', ''))}) for <strong>{money_html(row.get('amount', '0.00'), row.get('currency', 'USD'))}</strong> has been successfully captured in PayPal Sandbox.
                    </div>
                    <div style="display:flex; gap:1.5rem; margin-top:0.6rem; font-size:0.82rem; color:var(--aa-text-muted);">
                        <div>Order ID: <span class="aa-mono">{html_text(order_id)}</span></div>
                        <div>Capture ID: <span class="aa-mono">{html_text(capture_id)}</span></div>
                    </div>
                </div>
            """)
            if st.button("Dismiss", key="aa_dismiss_completed"):
                st.query_params.clear()
                st.rerun()

        elif status == "not_approved":
            row = res.get("row") or {}
            paypal_status = res.get("paypal_status", "PENDING")
            order_id = res.get("order_id", "")
            render_html(f"""
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid var(--aa-pending-solid); border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                        <div style="font-weight:600; font-size:1rem; color:var(--aa-text-heading);">Payment Return · Awaiting Buyer Approval</div>
                        <span class="aa-pill pending">{html_text(paypal_status)}</span>
                    </div>
                    <div style="font-size:0.88rem; color:var(--aa-text-soft); line-height:1.5;">
                        PayPal indicates that order <span class="aa-mono">{html_text(order_id)}</span> has not been approved by the buyer yet (current PayPal status: <strong>{html_text(paypal_status)}</strong>).
                    </div>
                </div>
            """)
            c1, c2 = st.columns([1.5, 5])
            with c1:
                if st.button("Check again", key="aa_check_again"):
                    st.rerun()
            with c2:
                if st.button("Dismiss", key="aa_dismiss_not_approved"):
                    st.query_params.clear()
                    st.rerun()

        elif status == "forbidden":
            row = res.get("row") or {}
            msg = res.get("message", "")
            render_html(f"""
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid var(--aa-blocked-fg); border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                        <div style="font-weight:600; font-size:1rem; color:var(--aa-text);">Payment Return · Capture Refused</div>
                        <span class="aa-pill blocked">{html_text(row.get('status', 'BLOCKED'))}</span>
                    </div>
                    <div style="font-size:0.88rem; color:var(--aa-text-soft);">
                        {html_text(msg)} This transaction cannot be captured.
                    </div>
                </div>
            """)
            if st.button("Dismiss", key="aa_dismiss_forbidden"):
                st.query_params.clear()
                st.rerun()

        elif status == "error":
            msg = res.get("message", "An error occurred while processing the payment return.")
            if Config.DEMO_PUBLIC:
                msg = "We could not confirm this payment with PayPal. Please try again."
            render_html(f"""
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid var(--aa-blocked-fg); border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1rem;">
                    <div style="font-weight:600; font-size:1rem; color:var(--aa-text);">Payment Return Error</div>
                    <div style="font-size:0.88rem; color:var(--aa-text-soft); margin-top:0.25rem;">{html_text(msg)}</div>
                </div>
            """)
            if st.button("Dismiss", key="aa_dismiss_error"):
                st.query_params.clear()
                st.rerun()


# ==============================================================================
# Main Application Router
# ==============================================================================

def main():
    """Main application launcher with st.navigation routing and frame elements."""
    st.set_page_config(
        page_title="Agent Allowance · An allowance for your AI agent",
        page_icon=":material/shield:",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    init_db()
    inject_theme_css()
    if LOGO_PATH.exists():
        st.logo(str(LOGO_PATH), size="large")
    flush_toasts()

    # Multipage navigation
    chat_page = st.Page(render_chat_page, title="Chat", icon=":material/chat:", default=True, url_path="")
    approvals_page = st.Page(render_approvals_page, title="Approvals", icon=":material/approval:", url_path="approvals")
    rules_page = st.Page(render_rules_page, title="Rules", icon=":material/tune:", url_path="rules")
    ledger_page = st.Page(render_ledger_page, title="Ledger", icon=":material/receipt_long:", url_path="ledger")
    _PAGES.update(chat=chat_page, approvals=approvals_page, rules=rules_page, ledger=ledger_page)

    pg = st.navigation([chat_page, approvals_page, rules_page, ledger_page])
    is_chat = pg == chat_page

    # Mode toggle first so the header pill shows the mode this run will use.
    active_mode, mode_note = render_sidebar_mode_control()

    # Single-row page header (22px page title on left, neutral mode pill on right)
    # and unified summary strip on EVERY page
    render_header(active_mode, mode_note, show_tagline=is_chat, page_title=pg.title)
    metrics_slot = st.empty()
    with metrics_slot.container():
        render_metrics_banner(compact_strip=True)

    # Render payment return banner if returning from PayPal
    render_payment_return_panel()

    # Persistent sidebar demo tools
    render_sidebar_demo_tools()

    # Execute selected page. A public demo never shows a raw traceback.
    if Config.DEMO_PUBLIC:
        try:
            pg.run()
        except Exception:
            st.error(GENERIC_ERROR, icon=":material/error:")
    else:
        pg.run()

    # Refresh metrics so they reflect anything the page just did (e.g. a new purchase).
    with metrics_slot.container():
        render_metrics_banner(compact_strip=True)


if __name__ == "__main__":
    main()
