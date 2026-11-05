"""
Hostile-text tests for every HTML snippet rendered with st.markdown(unsafe_allow_html=True).

Text from the database, catalog, LLM or user input (merchant, category, agent reason,
policy reasons, order ids, error text) must come out as literal text: no live tags,
no event-handler attributes, and no stray closing tags that break the card layout.
"""

from html.parser import HTMLParser
from typing import Dict, List

import pytest

from agent_allowance import app
from agent_allowance.ui_theme import (
    get_empty_state_html,
    get_mode_pill_html,
    get_pipeline_html,
    get_quota_notice_html,
    get_status_pill_html,
    get_verdict_html,
    html_text,
    money_html,
    pipeline_states,
)

IMG = "<img src=x onerror=alert(1)>"
SCRIPT = "<script>alert(1)</script>"
BREAKOUT = "</div><div>"
LONG = "A" * 600
HOSTILE = [IMG, SCRIPT, BREAKOUT, LONG]

VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "circle", "path", "rect", "ellipse", "stop"}


class _Inspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: List[str] = []
        self.handlers: List[str] = []
        self.depth: Dict[str, int] = {}
        self.text: List[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.handlers += [name for name, _ in attrs if name.startswith("on")]
        if tag not in VOID_TAGS:
            self.depth[tag] = self.depth.get(tag, 0) + 1

    def handle_startendtag(self, tag, attrs):
        self.tags.append(tag)
        self.handlers += [name for name, _ in attrs if name.startswith("on")]

    def handle_endtag(self, tag):
        if tag not in VOID_TAGS:
            self.depth[tag] = self.depth.get(tag, 0) - 1

    def handle_data(self, data):
        self.text.append(data)


def assert_inert(markup: str, *expected_literals: str) -> None:
    """No injected elements or handlers, balanced containers, hostile text present verbatim."""
    p = _Inspector()
    p.feed(markup)
    p.close()
    assert "img" not in p.tags and "script" not in p.tags, p.tags
    assert p.handlers == []
    unbalanced = {t: d for t, d in p.depth.items() if d != 0}
    assert unbalanced == {}, f"unbalanced tags: {unbalanced}"
    text = "".join(p.text)
    for literal in expected_literals:
        assert literal in text, f"missing literal text: {literal[:40]!r}"


# ------------------------------------------------------------------------------
# ui_theme builders
# ------------------------------------------------------------------------------

@pytest.mark.parametrize("value", HOSTILE)
def test_html_text_neutralizes(value):
    out = html_text(value)
    assert "<" not in out and ">" not in out
    assert_inert(f"<div>{out}</div>", value)


def test_html_text_escapes_quotes_for_attributes():
    out = html_text('" onmouseover="alert(1)')
    assert '"' not in out
    assert_inert(f'<div title="{out}">x</div>')


@pytest.mark.parametrize("status", ["APPROVED", "NEEDS_APPROVAL", "BLOCKED"])
def test_verdict_card_escapes_every_field(status):
    markup = get_verdict_html(
        status=status, title=SCRIPT, merchant=IMG, amount=12.5, currency="USD",
        category=BREAKOUT, reason=SCRIPT + LONG, policy_reasons=[SCRIPT, BREAKOUT, LONG],
        next_step=BREAKOUT,
    )
    assert_inert(markup, IMG, SCRIPT, BREAKOUT, LONG)


def test_pipeline_escapes_detail_and_captions():
    states = pipeline_states("decided", status="BLOCKED")
    states["rules"] = ("blocked", BREAKOUT)
    markup = get_pipeline_html(states, agent_detail=f"{IMG} · $9.00 {LONG}", compact=True)
    assert_inert(markup, IMG, BREAKOUT, LONG)
    assert "$" not in markup  # dollars become &#36; so markdown never starts LaTeX


def test_status_pill_escapes_unknown_status():
    assert_inert(get_status_pill_html(IMG), "<Img Src=X Onerror=Alert(1)>")


def test_money_html_escapes_non_numeric_amount_and_currency():
    markup = money_html(IMG, currency=SCRIPT)
    assert_inert(f"<div>{markup}</div>", IMG, SCRIPT)


def test_other_builders_escape_text():
    assert_inert(get_quota_notice_html(SCRIPT + BREAKOUT), SCRIPT, BREAKOUT)
    assert_inert(get_empty_state_html(IMG, BREAKOUT), IMG, BREAKOUT)
    assert_inert(get_mode_pill_html("mock", note=BREAKOUT), BREAKOUT)


# ------------------------------------------------------------------------------
# app.py call sites (capture what each one hands to render_html)
# ------------------------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    out: List[str] = []
    monkeypatch.setattr(app, "render_html", lambda markup, container=None: out.append(markup))
    return out


def _hostile_turn(status: str, order_id: str = "") -> Dict:
    proposal = {"merchant": IMG, "amount": 9.99, "currency": "USD", "category": BREAKOUT,
                "reason": SCRIPT + " " + LONG}
    return {
        "role": "assistant", "ran": True,
        "content": f"Proposal evaluated. Reasons: {SCRIPT}; {BREAKOUT}; ![x](https://example.com/x.png)",
        "tool_calls": [{"name": "propose_purchase", "args": proposal}],
        "proposal_result": {"status": status, "proposal": proposal,
                            "reasons": [SCRIPT, BREAKOUT, LONG], "paypal_order_id": order_id},
    }


@pytest.mark.parametrize("status", ["BLOCKED", "NEEDS_APPROVAL", "APPROVED"])
def test_chat_turn_renders_hostile_text_literally(captured, status):
    app.render_assistant_turn(_hostile_turn(status, order_id=BREAKOUT), key_suffix="t")
    markup = "".join(captured)
    for chunk in captured:
        assert_inert(chunk)
    assert_inert(markup, IMG, SCRIPT, BREAKOUT, LONG)
    # When a decision card is shown, raw duplicate agent text ("Proposal evaluated...") is suppressed.
    assert "Proposal evaluated." not in markup


def test_chat_turn_without_card_escapes_and_never_clips_agent_text(captured):
    turn = {
        "role": "assistant",
        "ran": True,
        "content": f"No purchase proposed: {IMG} {SCRIPT} {BREAKOUT} {LONG} ![x](https://example.com/x.png)",
        "tool_calls": [],
        "proposal_result": None,
    }
    app.render_assistant_turn(turn, key_suffix="no_card")
    markup = "".join(captured)
    for chunk in captured:
        assert_inert(chunk)
    assert_inert(markup, IMG, SCRIPT, BREAKOUT, LONG)
    # Agent text is escaped plain text, so a markdown image never becomes a remote request, and LONG is never clipped.
    assert "![x](https://example.com/x.png)" in markup



def test_user_bubble_escapes_input(captured):
    app.render_user_bubble(IMG + BREAKOUT)
    assert_inert(captured[0], IMG + BREAKOUT)


def test_ledger_detail_escapes_every_field(captured):
    row = {
        "id": 7, "merchant": IMG, "amount": "12.00", "currency": "USD", "category": BREAKOUT,
        "timestamp": SCRIPT, "status": "BLOCKED", "reasons": [SCRIPT, BREAKOUT, LONG],
        "proposal_id": LONG, "paypal_order_id": BREAKOUT, "paypal_capture_id": SCRIPT,
        "agent_reason": IMG, "proposal": {"item_title": SCRIPT},
    }
    app._render_ledger_detail(row)
    # Header ("Selected: #id · merchant") and body render as separate chunks.
    assert len(captured) >= 2
    for chunk in captured:
        assert_inert(chunk)
    assert_inert("".join(captured), IMG, SCRIPT, BREAKOUT, LONG)
    assert "Selected: #7 · " in captured[0]


def test_approvals_card_escapes_every_field(captured, monkeypatch):
    item = {"id": 3, "merchant": IMG, "amount": 40.0, "currency": "USD", "category": BREAKOUT,
            "created_at": SCRIPT, "reason": LONG, "reasons": '["%s", "%s"]' % (SCRIPT, BREAKOUT)}
    monkeypatch.setattr(app, "get_pending_transactions", lambda: [item])
    app.render_approvals_page()
    card = next(m for m in captured if "aa-needs-you" in m)
    assert_inert(card, IMG, SCRIPT, BREAKOUT, LONG)
