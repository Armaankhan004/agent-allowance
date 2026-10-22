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
  - Approvals with toasts, Rules with inline validation, styled Ledger, Demo tools
    with a confirmation dialog for reset.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import streamlit as st

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from agent_allowance.config import Config
from agent_allowance.db import (
    init_db,
    get_all_transactions,
    get_pending_transactions,
    reset_demo_transactions,
)
from agent_allowance.policy_engine import (
    approve_pending,
    reject_pending,
    get_policy_rules,
    update_policy_rule,
    validate_rule_value,
)
from agent_allowance.agent import ShoppingAgent, create_order_for_approved
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
)

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
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(r) for r in raw]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(r) for r in parsed]
        return [str(parsed)]
    except (TypeError, ValueError):
        return [str(raw)]


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

def render_ledger_page():
    """Render audit ledger of all transactions."""
    with st.container(key="aa-page-ledger"):
        render_section_title(
            "Ledger",
            "Audit record of every proposal, policy decision and PayPal sandbox order.",
        )

        transactions = get_all_transactions()
        if not transactions:
            render_html(get_empty_state_html(
                "No transactions yet",
                "Ask the agent to buy something and every decision will be recorded here.",
                icon='<span class="aa-msym">receipt_long</span>',
            ))
            return

        statuses = ["All"] + sorted({(t.get("status") or "").upper() for t in transactions if t.get("status")})
        labels = {s: ("All" if s == "All" else s.replace("_", " ").title()) for s in statuses}
        f1, f2 = st.columns([3, 2], vertical_alignment="bottom")
        with f1:
            choice = st.segmented_control(
                "Status", statuses, default="All", format_func=lambda s: labels[s], key="aa_ledger_status"
            ) or "All"
        with f2:
            query = st.text_input("Search", placeholder="Merchant, category or reason", key="aa_ledger_q")

        q = (query or "").strip().lower()
        rows_html: List[str] = []
        shown = 0
        for t in transactions:
            status = (t.get("status") or "").upper()
            if choice != "All" and status != choice:
                continue
            reasons = _parse_reasons(t.get("reasons")) or (
                [t["evaluation_reason"]] if t.get("evaluation_reason") else []
            )
            haystack = " ".join([str(t.get("merchant", "")), str(t.get("category", "")), " ".join(reasons)]).lower()
            if q and q not in haystack:
                continue
            shown += 1
            reason_html = "<br>".join(html_text(r) for r in reasons) or '<span class="aa-muted">&mdash;</span>'
            order = t.get("paypal_order_id")
            order_html = f'<span class="aa-mono">{html_text(order)}</span>' if order else '<span class="aa-muted">&mdash;</span>'
            rows_html.append(
                f"<tr>"
                f'<td class="aa-nowrap aa-muted">#{t["id"]}</td>'
                f'<td class="aa-nowrap">{html_text(t.get("created_at"))}</td>'
                f'<td><div class="aa-merchant">{html_text(t.get("merchant"))}</div>'
                f'<div class="aa-cat">{html_text(t.get("category"))}</div></td>'
                f'<td class="aa-amount">{money_html(t.get("amount"), t.get("currency"))}</td>'
                f"<td>{get_status_pill_html(status)}</td>"
                f'<td class="aa-reason-cell">{reason_html}</td>'
                f"<td>{order_html}</td>"
                f"</tr>"
            )

        if not rows_html:
            render_html(get_empty_state_html("No matches", "Try a different status or search term.",
                                             icon='<span class="aa-msym">search_off</span>'))
            return

        render_html(
            '<div class="aa-table-wrap"><table class="aa-table"><thead><tr>'
            '<th>ID</th><th>Time (UTC)</th><th>Merchant</th><th class="aa-amount">Amount</th>'
            "<th>Status</th><th>Decision reason</th><th>PayPal order</th>"
            "</tr></thead><tbody>" + "".join(rows_html) + "</tbody></table></div>"
        )
        st.caption(f"Showing {shown} of {len(transactions)} transactions.")


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

    # Persistent sidebar demo tools
    render_sidebar_demo_tools()

    # Execute selected page
    pg.run()

    # Refresh metrics so they reflect anything the page just did (e.g. a new purchase).
    with metrics_slot.container():
        render_metrics_banner()


if __name__ == "__main__":
    main()
