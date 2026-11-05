"""
Presentation helpers behind the Ledger page: compact reasons, honest totals labels,
grid options (row-only selection, fixed row height), sidebar caption and the
st.dataframe fallback.
"""

from decimal import Decimal

import pytest

from agent_allowance import app
from agent_allowance.app import (
    gemini_caps_caption,
    ledger_totals_label,
    reason_cell_text,
    reason_summary,
)
from agent_allowance.ui_theme import TOKENS, contrast_ratio, get_aggrid_custom_css, get_summary_strip_html


def _row(i, status, amount="10.00", currency="USD", reasons=None):
    return {
        "id": i, "timestamp": "2026-11-05 08:00:00", "merchant": f"M{i}", "category": "Office Supplies",
        "amount": amount, "currency": currency, "status": status, "reasons": reasons or [],
        "paypal_order_id": "", "proposal_id": f"prop-{i}", "proposal": {},
    }


# --- First reason + count -----------------------------------------------------

@pytest.mark.parametrize("reasons, expected", [
    ([], ("", 0)),
    (None, ("", 0)),
    (["Only one."], ("Only one.", 0)),
    (["First.", "Second.", "Third."], ("First.", 2)),
    (["  ", "Real first.", "", "Second."], ("Real first.", 1)),  # blanks are ignored
])
def test_reason_summary(reasons, expected):
    assert reason_summary(reasons) == expected


def test_reason_cell_text_for_fallback():
    assert reason_cell_text(["A.", "B.", "C."]) == "A. (+2 more)"
    assert reason_cell_text(["A."]) == "A."
    assert reason_cell_text([]) == ""


# --- Filtered totals label ------------------------------------------------------

def test_totals_label_counts_all_rows_but_sums_only_approved_and_executed():
    rows = [
        _row(1, "APPROVED", "28.50"),
        _row(2, "EXECUTED", "8.49"),
        _row(3, "BLOCKED", "500.00"),
        _row(4, "NEEDS_APPROVAL", "28.50"),
        _row(5, "REJECTED", "12.00"),
    ]
    assert ledger_totals_label(rows) == "5 rows · $36.99 approved and executed (all months)"


def test_totals_label_reflects_the_filtered_subset():
    rows = [_row(1, "APPROVED", "28.50"), _row(2, "BLOCKED", "500.00"), _row(3, "BLOCKED", "500.00")]
    blocked_only = [r for r in rows if r["status"] == "BLOCKED"]
    assert ledger_totals_label(blocked_only) == "2 rows · $0.00 approved and executed (all months)"


def test_totals_label_singular_empty_and_thousands():
    assert ledger_totals_label([]) == "0 rows · $0.00 approved and executed (all months)"
    assert ledger_totals_label([_row(1, "APPROVED", "1234.5")]) == \
        "1 row · $1,234.50 approved and executed (all months)"


def test_totals_label_non_usd_and_mixed_currencies():
    assert ledger_totals_label([_row(1, "APPROVED", "5.00", "EUR")]) == \
        "1 row · 5.00 EUR approved and executed (all months)"
    mixed = [_row(1, "APPROVED", "5.00", "EUR"), _row(2, "APPROVED", "5.00", "USD")]
    assert ledger_totals_label(mixed) == "2 rows · mixed currencies (all months)"


def test_totals_label_uses_exact_decimal_sum():
    rows = [_row(i, "APPROVED", "0.10") for i in range(3)]
    assert app._approved_total(rows) == Decimal("0.30")
    assert "$0.30" in ledger_totals_label(rows)


# --- Grid options -------------------------------------------------------------

@pytest.mark.skipif(app.JsCode is None, reason="streamlit-aggrid not installed")
def test_grid_options_row_only_selection_and_compact_rows():
    opts = app._ledger_grid_options("")
    assert opts["suppressCellFocus"] is True
    assert opts["rowHeight"] == 44
    assert opts["rowSelection"]["mode"] == "singleRow"
    by_field = {c["field"]: c for c in opts["columnDefs"]}
    assert not any(c.get("autoHeight") or c.get("wrapText") for c in opts["columnDefs"])
    # Status pills and the PayPal order column stay.
    assert "cellRenderer" in by_field["status"]
    assert "paypal_order_id" in by_field
    assert "tooltipValueGetter" in by_field["reasons_text"]


def test_grid_css_has_no_cell_focus_box_and_accented_selection():
    css = get_aggrid_custom_css()
    focus_rule = next(v for k, v in css.items() if ".ag-cell-focus" in k and ".ag-row-selected" not in k)
    assert "transparent" in focus_rule["border-color"] and "none" in focus_rule["outline"]
    accent = css[".ag-row-selected .ag-cell[col-id='id']"]["box-shadow"]
    assert "3px" in accent and TOKENS["plum"] in accent
    assert css[".ag-row-selected .aa-merchant"]["font-weight"].startswith("500")


def test_tinted_rows_keep_text_contrast():
    for bg in (TOKENS["grid-selected-bg"], TOKENS["grid-hover-bg"]):
        assert contrast_ratio(TOKENS["ink"], bg) >= 4.5
        # Muted text is stepped up to secondary inside tinted rows.
        assert contrast_ratio(TOKENS["text-secondary"], bg) >= 4.5


# --- Labels -------------------------------------------------------------------

def test_summary_strip_is_labelled_this_month():
    html = get_summary_strip_html({"approved_spend": Decimal("28.50"), "monthly_budget": Decimal("200.00"),
                                   "budget_used_pct": 0.1425, "pending_count": 3, "blocked_count": 2})
    assert ">This month<" in html
    assert ">Spent<" not in html


def test_gemini_caption_mentions_session_and_daily_caps():
    text = gemini_caps_caption(10)
    assert "10 Gemini requests per session" in text
    assert "daily quota" in text
    assert "unlimited" not in text
    assert "1 Gemini request per session" in gemini_caps_caption(1)


# --- st.dataframe fallback ----------------------------------------------------

def test_fallback_table_renders_compact_reasons_and_totals():
    from streamlit.testing.v1 import AppTest

    def script():
        from agent_allowance import app as a
        rows = [
            {"id": 2, "timestamp": "2026-11-05 08:53:01", "merchant": "Apex Gift Cards", "category": "Gifts",
             "amount": "500.00", "currency": "USD", "status": "BLOCKED", "reasons": ["One.", "Two.", "Three."],
             "paypal_order_id": "", "proposal_id": "p2", "proposal": {}},
            {"id": 1, "timestamp": "2026-11-05 07:00:00", "merchant": "Corner Office Supply", "category": "Office",
             "amount": "8.49", "currency": "USD", "status": "APPROVED", "reasons": ["Within limits."],
             "paypal_order_id": "ORDER1", "proposal_id": "p1", "proposal": {}},
        ]
        a.AgGrid = None
        a.get_ledger_rows = lambda: rows
        a.render_ledger_page()

    at = AppTest.from_function(script).run(timeout=30)
    assert not at.exception
    captions = " ".join(c.value for c in at.caption).replace("\\$", "$")  # md_escape'd dollars
    assert "2 rows · $8.49 approved and executed (all months)" in captions
    df = at.dataframe[0].value
    assert list(df["Reasons"]) == ["One. (+2 more)", "Within limits."]
    assert "Select a row to see the full decision." in " ".join(m.value for m in at.markdown)
