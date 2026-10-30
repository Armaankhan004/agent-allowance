"""
Streamlit Web Application for Agent Allowance.
Deterministic Financial Guardrails for Autonomous AI Agents · Powered by PayPal Sandbox.

UI notes (presentation only; policy/agent/db/PayPal logic is untouched):
  - Light global theme; navy palette scoped to the sidebar via [theme.sidebar].
  - All styling lives in agent_allowance/ui_theme.py (design tokens -> CSS variables).
  - Dollar amounts are escaped so markdown never renders them as LaTeX.
  - Custom budget bar replaces st.progress (blue -> amber > 75% -> red > 90%).
  - Live agent steps (Searching catalog, Proposing purchase, Checking policy) and a
    decision card with accurate outcome labels (green check only for real approvals).
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
from agent_allowance.paypal_client import get_order_status
from agent_allowance.ui_theme import (
    inject_theme_css,
    get_status_pill_html,
    get_mode_pill_html,
    render_header,
    render_metrics_banner,
    render_how_it_works_strip,
    render_section_title,
    render_html,
    md_escape,
    html_text,
    money_html,
    get_live_steps_html,
    get_outcome_html,
    get_decision_body_html,
    get_empty_state_html,
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


def derive_step_states(
    tool_calls: List[Dict[str, Any]], proposal_result: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Final step states + details for a completed agent turn."""
    searches = _search_calls(tool_calls)
    proposals = _proposal_calls(tool_calls)
    states: Dict[str, str] = {}
    details: Dict[str, str] = {}

    states["search"] = "done" if searches or proposals or proposal_result else "skipped"
    if searches and searches[-1].get("query"):
        details["search"] = f"\u201c{searches[-1]['query']}\u201d"

    if proposals or proposal_result:
        states["propose"] = "done"
        src = (proposal_result or {}).get("proposal") or (proposals[-1] if proposals else {})
        if src.get("merchant"):
            details["propose"] = f"{src.get('merchant')} \u00b7 {_fmt_amount_text(src.get('amount', 0))}"
    else:
        states["propose"] = "skipped"

    states["policy"] = "done" if proposal_result else "skipped"
    return states, details


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


def render_decision_card(
    tool_calls: List[Dict[str, Any]],
    proposal_result: Dict[str, Any],
    key_suffix: str,
) -> None:
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

    with st.container(border=True, key=f"aa-card-static-decision-{status_card_key(status)}-{key_suffix}"):
        render_html(
            get_decision_body_html(
                status=status,
                merchant=merchant,
                amount=amount,
                currency=currency,
                category=category,
                reason=reason,
                policy_reasons=_parse_reasons(proposal_result.get("reasons")),
                next_step=_next_step_text(status, approval_url),
            )
        )
        if status == "APPROVED" and approval_url:
            st.link_button(
                "Pay with PayPal sandbox",
                approval_url,
                type="primary",
                icon=":material/open_in_new:",
                width="stretch",
            )
            if order_id:
                render_html(f'<div class="aa-muted aa-mono" style="margin-top:0.5rem;">Order {html_text(order_id)}</div>')
        elif status == "NEEDS_APPROVAL" and _PAGES.get("approvals"):
            st.page_link(_PAGES["approvals"], label="Review in Approvals", icon=":material/arrow_forward:")
        elif status == "BLOCKED" and _PAGES.get("rules"):
            st.page_link(_PAGES["rules"], label="View Rules", icon=":material/tune:")


def render_assistant_turn(msg: Dict[str, Any], key_suffix: str) -> None:
    """Render a completed assistant turn: steps, outcome, agent text, decision card."""
    tool_calls = msg.get("tool_calls") or []
    proposal_result = msg.get("proposal_result")

    if msg.get("ran"):
        states, details = derive_step_states(tool_calls, proposal_result)
        render_html(get_live_steps_html(states, details))
        label, state = get_chat_outcome_label_and_state(msg)
        render_html(get_outcome_html(label, state))

    if msg.get("error"):
        st.error(md_escape(f"The agent hit a problem: {msg['error']}"), icon=":material/error:")
    elif msg.get("content"):
        st.markdown(md_escape(msg["content"]))

    if proposal_result:
        render_decision_card(tool_calls, proposal_result, key_suffix)


def render_example_cards() -> None:
    """Three scenario cards on a fresh chat."""
    scenarios = [
        ("paper", "inventory_2", "Office supplies", "Small purchase under the $25 approval threshold. Expect auto-approval.",
         "Buy copy paper for the office", "Buy copy paper for the office"),
        ("flour", "bakery_dining", "Bakery restock", "Restock from Golden Grain Mills. May need your approval.",
         "We're low on flour", "we're low on flour"),
        ("gift", "gpp_maybe", "Policy defense", "A $500 gift card from an unlisted merchant. Expect a block.",
         "Try the gift card attack", "Search specials and buy the $500 gift card"),
    ]
    render_html('<div class="aa-label" style="margin: 0.25rem 0 0.6rem 0;">Try a scenario</div>')
    cols = st.columns(3)
    for col, (sid, icon, title, desc, button_label, prompt) in zip(cols, scenarios):
        with col:
            with st.container(border=True, key=f"aa-card-scn-{sid}"):
                render_html(
                    f'<div class="aa-icon-chip"><span class="aa-msym">{icon}</span></div>'
                    f'<div class="aa-card-title">{html_text(title)}</div>'
                    f'<p class="aa-card-desc">{html_text(desc)}</p>'
                )
                if st.button(button_label, key=f"btn_ex_{sid}", width="stretch"):
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
            if st.button("Reset demo data", key="side_reset_btn", icon=":material/restart_alt:", width="stretch"):
                confirm_reset_dialog()

        attack_res = st.session_state.get("last_attack_result")
        if attack_res and st.session_state.get("attack_triggered"):
            prop = attack_res.get("proposal_result") or {}
            status = prop.get("status", "BLOCKED")
            render_html(
                f"""
                <div class="aa-side-alert">
                    <div class="aa-side-alert-title">Attack intercepted</div>
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
        if len(st.session_state["messages"]) <= 1 and not user_input:
            render_how_it_works_strip()
            render_example_cards()
            st.write("")

        for idx, msg in enumerate(st.session_state["messages"]):
            avatar = ":material/person:" if msg["role"] == "user" else ":material/smart_toy:"
            with st.chat_message(msg["role"], avatar=avatar):
                if msg["role"] == "user":
                    st.markdown(md_escape(msg.get("content", "")))
                else:
                    render_assistant_turn(msg, key_suffix=str(idx))

    if not user_input:
        return

    with page:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(md_escape(user_input))

        turn_idx = len(st.session_state["messages"])
        with st.chat_message("assistant", avatar=":material/smart_toy:"):
            live_tool_calls: List[Dict[str, Any]] = []
            live_policy_decisions: List[Dict[str, Any]] = []
            states = {"search": "active", "propose": "pending", "policy": "pending"}
            details: Dict[str, str] = {}
            steps_ph = st.empty()
            render_html(get_live_steps_html(states, details), container=steps_ph)

            def on_tool_callback(tool_name: str, args: Dict[str, Any]):
                live_tool_calls.append({"tool": tool_name, **args})
                if "query" in args or tool_name == "search_products":
                    states["search"] = "done"
                    if args.get("query"):
                        details["search"] = f"\u201c{args['query']}\u201d"
                    if states["propose"] == "pending":
                        states["propose"] = "active"
                elif "merchant" in args or tool_name == "propose_purchase":
                    states["search"] = "done"
                    states["propose"] = "done"
                    details["propose"] = f"{args.get('merchant')} \u00b7 {_fmt_amount_text(args.get('amount', 0))}"
                    states["policy"] = "active"
                render_html(get_live_steps_html(states, details), container=steps_ph)

            def on_policy_callback(decision: Dict[str, Any]):
                live_policy_decisions.append(decision)
                states["policy"] = "done"
                render_html(get_live_steps_html(states, details), container=steps_ph)

            active_provider = (Config.LLM_PROVIDER or "mock").lower()
            agent = ShoppingAgent(provider=active_provider)

            result = agent.run(
                user_request=user_input,
                on_tool_call=on_tool_callback,
                on_policy_decision=on_policy_callback,
            )

            tool_calls = result.get("tool_calls", live_tool_calls) or live_tool_calls
            if result.get("error"):
                assistant_content = f"Encountered an issue: {result['error']}"
            else:
                assistant_content = result.get("agent_response", "")

            msg = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls,
                "proposal_result": result.get("proposal_result"),
                "error": result.get("error"),
                "ran": True,
            }
            steps_ph.empty()
            render_assistant_turn(msg, key_suffix=str(turn_idx))

            # Save assistant message into persistent session history
            st.session_state["messages"].append(msg)


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

        last = st.session_state.get("aa_last_approved")
        if last:
            with st.container(border=True, key="aa-card-static-decision-approved-last"):
                render_html(
                    f'<div class="aa-card-head"><div class="aa-card-title">Transaction #{int(last["id"])} approved</div>'
                    f'{get_status_pill_html("APPROVED")}</div>'
                    f'<p class="aa-card-desc" style="margin-bottom:0.5rem;">'
                    f'{"Complete the payment in the PayPal sandbox." if last.get("url") else html_text(last.get("note") or "Recorded in the ledger.")}</p>'
                )
                c1, c2 = st.columns([3, 1])
                with c1:
                    if last.get("url"):
                        st.link_button("Pay with PayPal sandbox", last["url"], type="primary",
                                       icon=":material/open_in_new:", width="stretch")
                with c2:
                    if st.button("Dismiss", key="aa_dismiss_last", width="stretch"):
                        st.session_state.pop("aa_last_approved", None)
                        st.rerun()

        pending_items = get_pending_transactions()

        if not pending_items:
            render_html(get_empty_state_html(
                "You're all caught up",
                "No purchases are waiting for approval. Anything above your threshold will show up here.",
                icon='<span class="aa-msym">task_alt</span>',
            ))
            if _PAGES.get("chat"):
                st.page_link(_PAGES["chat"], label="Ask the agent to buy something", icon=":material/chat:")
            return

        render_html(f'<div class="aa-label" style="margin-bottom:0.6rem;">{len(pending_items)} waiting for review</div>')

        for item in pending_items:
            ledger_id = item["id"]
            reasons_list = _parse_reasons(item.get("reasons"))
            reasons_html = "".join(f"<li>{html_text(r)}</li>" for r in reasons_list)

            with st.container(border=True, key=f"aa-card-pending-{ledger_id}"):
                render_html(
                    f"""
                    <div class="aa-card-head">
                        <div class="aa-card-title">#{ledger_id} · {html_text(item['merchant'])}</div>
                        {get_status_pill_html("NEEDS_APPROVAL")}
                    </div>
                    <div class="aa-kv">
                        <div><div class="aa-label">Amount</div><div class="aa-v big">{money_html(item['amount'], item.get('currency'))}</div></div>
                        <div><div class="aa-label">Category</div><div class="aa-v">{html_text(item.get('category'))}</div></div>
                        <div><div class="aa-label">Requested</div><div class="aa-v">{html_text(item.get('created_at'))}</div></div>
                    </div>
                    <div class="aa-label">Agent&#39;s reason</div>
                    <p class="aa-reason">{html_text(item.get('reason') or 'No reason provided')}</p>
                    {f'<div class="aa-label">Why it needs you</div><ul class="aa-reasons">{reasons_html}</ul>' if reasons_html else ''}
                    """
                )

                col_app, col_rej, _ = st.columns([1.2, 1.2, 3])
                with col_app:
                    if st.button("Approve", key=f"app_btn_{ledger_id}", type="primary",
                                 icon=":material/check:", width="stretch"):
                        try:
                            app_res = approve_pending(ledger_id)
                        except ValueError as e:
                            st.error(md_escape(str(e)))
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
                                last_info["note"] = f"Approved, but no PayPal sandbox order was created: {e}"
                                queue_toast("PayPal sandbox order could not be created.", ":material/warning:")
                            st.session_state["aa_last_approved"] = last_info
                            st.rerun()
                        elif app_res:
                            st.error(md_escape(f"Approval refused by the policy engine: {app_res.get('refusal_reason')}"),
                                     icon=":material/block:")

                with col_rej:
                    if st.button("Reject", key=f"rej_btn_{ledger_id}", icon=":material/close:", width="stretch"):
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
                    '<div class="aa-icon-chip"><span class="aa-msym">payments</span></div>'
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
                    '<div class="aa-icon-chip"><span class="aa-msym">storefront</span></div>'
                    '<div class="aa-card-title">Approved merchants</div>'
                    '<p class="aa-card-desc">Purchases from any merchant not on this list are blocked.</p>'
                )
                merchants_val = st.text_area(
                    "Allowed merchants",
                    key="rule_allowed_merchants",
                    height=140,
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

        st.write("")
        b1, b2 = st.columns([1, 4], vertical_alignment="center")
        with b1:
            save = st.button("Save rules", type="primary", icon=":material/save:", width="stretch", key="aa_save_rules")
        with b2:
            if errors:
                render_html(f'<span class="aa-field-error" style="margin:0;">Fix {len(errors)} field(s) before saving.</span>')
            elif dirty:
                render_html('<span class="aa-dirty">You have unsaved changes.</span>')
            else:
                render_html('<span class="aa-muted" style="font-size:0.85rem;">All changes saved.</span>')

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
                    st.error(md_escape(f"Error saving rules: {e}"))


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
            {"field": "timestamp", "headerName": "Time (UTC)", "width": 120, "cellDataType": "text",
             "filter": "agTextColumnFilter", "wrapText": True, "autoHeight": True},
            {"field": "merchant", "headerName": "Merchant", "flex": 1, "minWidth": 140, "cellDataType": "text",
             "filter": "agTextColumnFilter", "cellClass": "aa-merchant", "tooltipField": "category",
             "wrapText": True, "autoHeight": True},
            {"field": "amount_value", "headerName": "Amount", "width": 108, "type": "rightAligned", "cellDataType": "number",
             "filter": "agNumberColumnFilter", "valueFormatter": money_formatter, "cellClass": "aa-amount"},
            {"field": "status", "headerName": "Status", "width": 160, "cellDataType": "text", "filter": "agTextColumnFilter",
             "cellRenderer": status_renderer,
             "filterValueGetter": JsCode(f"function(p){{ return ({status_label_js})(p.data.status); }}"),
             "getQuickFilterText": JsCode(f"function(p){{ return ({status_label_js})(p.value); }}")},
            {"field": "reasons_text", "headerName": "Reasons", "flex": 2, "minWidth": 210, "cellDataType": "text",
             "filter": "agTextColumnFilter", "cellRenderer": reasons_renderer, "cellClass": "aa-reasons-cell",
             "wrapText": True, "autoHeight": True, "tooltipField": "reasons_text"},
            {"field": "paypal_order_id", "headerName": "PayPal order", "width": 160, "cellDataType": "text",
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
    reasons_html = "".join(f"<li>{html_text(r)}</li>" for r in row["reasons"]) or \
        '<li class="aa-muted">No reasons recorded.</li>'
    order = row.get("paypal_order_id")
    order_html = f'<span class="aa-mono">{html_text(order)}</span>' if order else '<span class="aa-muted">None</span>'
    capture = row.get("paypal_capture_id")
    capture_html = f'<span class="aa-mono">{html_text(capture)}</span>' if capture else '<span class="aa-muted">None</span>'
    item = proposal.get("item_title") or proposal.get("item_id") or ""
    agent_reason = row.get("agent_reason") or proposal.get("reason") or ""
    with st.container(border=True, key=f"aa-card-static-ledger-{status_card_key(row['status'])}"):
        render_html(f"""
            <div class="aa-card-head">
                <div class="aa-card-title">#{row['id']} · {html_text(row['merchant'])}</div>
                {get_status_pill_html(row['status'])}
            </div>
            <div class="aa-kv">
                <div><div class="aa-label">Amount</div><div class="aa-v big">{money_html(row['amount'], row['currency'])}</div></div>
                <div><div class="aa-label">Category</div><div class="aa-v">{html_text(row['category'])}</div></div>
                <div><div class="aa-label">Time (UTC)</div><div class="aa-v">{html_text(row['timestamp'])}</div></div>
            </div>
            <div class="aa-kv">
                <div><div class="aa-label">Proposal ID</div><div class="aa-v aa-mono">{html_text(row.get('proposal_id'))}</div></div>
                <div><div class="aa-label">PayPal order</div><div class="aa-v">{order_html}</div></div>
                {f'<div><div class="aa-label">Capture ID</div><div class="aa-v">{capture_html}</div></div>' if capture else (f'<div><div class="aa-label">Item</div><div class="aa-v">{html_text(item)}</div></div>' if item else '')}
            </div>
            {f'<div class="aa-label">Agent&#39;s reason</div><p class="aa-reason">{html_text(agent_reason)}</p>' if agent_reason else ''}
            <div class="aa-label">Policy reasons</div>
            <ul class="aa-reasons">{reasons_html}</ul>
        """)


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
                icon='<span class="aa-msym">receipt_long</span>',
            ))
            return

        f1, f2 = st.columns([4, 1], vertical_alignment="bottom")
        with f1:
            query = st.text_input(
                "Search", placeholder="Search merchant, status, reason or order ID", key="aa_ledger_q",
                icon=":material/search:",
            )
        quick_filter = (query or "").strip()

        grid = None
        fallback_id: Optional[int] = None
        export_rows = rows
        if AgGrid is None:
            export_rows, fallback_id = _render_ledger_fallback(
                rows, quick_filter, "Interactive grid unavailable (streamlit-aggrid not installed). Showing a basic table.")
        else:
            try:
                grid = _render_ledger_grid(rows, quick_filter)
            except Exception:
                grid = None
                export_rows, fallback_id = _render_ledger_fallback(
                    rows, quick_filter, "Interactive grid failed to load. Showing a basic table.")

        # Export what the grid currently shows (filters + sort).
        try:
            shown = grid.data if grid is not None else None
            if shown is not None and "id" in shown.columns:
                by_id = {r["id"]: r for r in rows}
                export_rows = [by_id[int(i)] for i in shown["id"].dropna() if int(i) in by_id]
        except Exception:
            pass
        with f2:
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
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid #94A3B8; border-radius: 10px; padding: 1rem 1.25rem; margin-bottom: 1.25rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <div style="font-weight:700; font-size:1rem; color:var(--aa-text);">Payment was cancelled</div>
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
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid var(--aa-approved-fg); border-radius: 10px; padding: 1.1rem 1.25rem; margin-bottom: 1.25rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                        <div style="font-weight:700; font-size:1.05rem; color:var(--aa-text);">Payment Return · COMPLETED</div>
                        <span class="aa-pill executed">COMPLETED</span>
                    </div>
                    <div style="font-size:0.88rem; color:var(--aa-text-soft); line-height:1.5;">
                        Transaction <strong>#{row.get('id', '')}</strong> ({html_text(row.get('merchant', ''))}) for <strong>{money_html(row.get('amount', '0.00'), row.get('currency', 'USD'))}</strong> has been successfully captured in PayPal Sandbox.
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
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid #F59E0B; border-radius: 10px; padding: 1.1rem 1.25rem; margin-bottom: 1.25rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                        <div style="font-weight:700; font-size:1.05rem; color:var(--aa-text);">Payment Return · Awaiting Buyer Approval</div>
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
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid var(--aa-blocked-fg); border-radius: 10px; padding: 1.1rem 1.25rem; margin-bottom: 1.25rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
                        <div style="font-weight:700; font-size:1.05rem; color:var(--aa-text);">Payment Return · Capture Refused</div>
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
            render_html(f"""
                <div style="background: var(--aa-surface); border: 1px solid var(--aa-border); border-left: 4px solid var(--aa-blocked-fg); border-radius: 10px; padding: 1.1rem 1.25rem; margin-bottom: 1.25rem;">
                    <div style="font-weight:700; font-size:1rem; color:var(--aa-text);">Payment Return Error</div>
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

    # Unified header & metrics at top of every page (metrics refreshed after the page runs)
    render_header()
    metrics_slot = st.empty()
    with metrics_slot.container():
        render_metrics_banner()

    # Render payment return banner if returning from PayPal
    render_payment_return_panel()

    # Persistent sidebar demo tools
    render_sidebar_demo_tools()

    # Execute selected page
    pg.run()

    # Refresh metrics so they reflect anything the page just did (e.g. a new purchase).
    with metrics_slot.container():
        render_metrics_banner()


if __name__ == "__main__":
    main()
