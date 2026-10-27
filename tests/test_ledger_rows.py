"""
Tests for get_ledger_rows(), the display-ready ledger query behind the Ledger grid.
"""

import json

import pytest

from agent_allowance.db import init_db, get_db_connection, get_ledger_rows, parse_reasons
from agent_allowance.app import _ledger_csv, _csv_safe
from agent_allowance.ui_theme import html_text


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_ledger_rows.db")
    init_db(db_file)
    return db_file


def _insert(db_path, proposal_id, status, amount=10.0, reasons=None, evaluation_reason=None,
            raw_proposal=None, paypal_order_id=None, created_at="2026-10-01 12:00:00",
            merchant="Beacon Mart"):
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO transactions (proposal_id, merchant, amount, currency, category, reason, status,
                                  reasons, evaluation_reason, raw_proposal, paypal_order_id, created_at)
        VALUES (?, ?, ?, 'USD', 'Groceries', 'Weekly restock', ?, ?, ?, ?, ?, ?)
        """,
        (proposal_id, merchant, amount, status, reasons, evaluation_reason, raw_proposal, paypal_order_id, created_at),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def test_empty_ledger_returns_empty_list(test_db):
    assert get_ledger_rows(db_path=test_db) == []


def test_rows_are_newest_first(test_db):
    first = _insert(test_db, "p-1", "APPROVED")
    second = _insert(test_db, "p-2", "BLOCKED")
    third = _insert(test_db, "p-3", "NEEDS_APPROVAL")
    assert [r["id"] for r in get_ledger_rows(db_path=test_db)] == [third, second, first]


def test_row_shape_and_amount_formatting(test_db):
    _insert(
        test_db, "p-1", "approved", amount=8.1,
        reasons=json.dumps(["Within all allowance limits and approved automatically."]),
        raw_proposal=json.dumps({"merchant": "Beacon Mart", "amount": 8.1, "reason": "Weekly restock"}),
        paypal_order_id="ORDER-123",
    )
    row = get_ledger_rows(db_path=test_db)[0]
    assert set(row) >= {"id", "timestamp", "merchant", "amount", "currency", "category",
                        "status", "reasons", "paypal_order_id"}
    assert row["amount"] == "8.10"
    assert row["status"] == "APPROVED"
    assert row["timestamp"] == "2026-10-01 12:00:00"
    assert row["paypal_order_id"] == "ORDER-123"
    assert row["proposal"]["reason"] == "Weekly restock"


def test_reasons_parsed_from_json_list(test_db):
    reasons = ["Amount $60.00 exceeds the maximum per-purchase limit of $50.00.", "Merchant not allowed."]
    _insert(test_db, "p-1", "BLOCKED", reasons=json.dumps(reasons))
    assert get_ledger_rows(db_path=test_db)[0]["reasons"] == reasons


def test_reasons_fall_back_to_evaluation_summary(test_db):
    _insert(test_db, "p-1", "BLOCKED", reasons=None, evaluation_reason="Rule A failed; Rule B failed")
    assert get_ledger_rows(db_path=test_db)[0]["reasons"] == ["Rule A failed", "Rule B failed"]


def test_missing_reasons_and_bad_proposal_json(test_db):
    _insert(test_db, "p-1", "REJECTED", reasons=None, raw_proposal="{not json")
    row = get_ledger_rows(db_path=test_db)[0]
    assert row["reasons"] == []
    assert row["proposal"] == {}
    assert row["paypal_order_id"] == ""


def test_parse_reasons_variants():
    assert parse_reasons(None) == []
    assert parse_reasons("") == []
    assert parse_reasons(["a", 1]) == ["a", "1"]
    assert parse_reasons('["x", "y"]') == ["x", "y"]
    assert parse_reasons('"single"') == ["single"]
    assert parse_reasons("plain text reason") == ["plain text reason"]


def test_hostile_text_in_ledger(test_db):
    """
    Verify hostile text in ledger (XSS payloads and spreadsheet formula injections)
    is handled safely:
    - Merchant: <img src=x onerror=alert(1)>
    - Reasons: <script>alert(1)</script> and =HYPERLINK("http://example.com")

    Checks:
    1. get_ledger_rows preserves literal strings without corrupting them.
    2. Detail card formatting (html_text) escapes HTML tags so browser renders literal text
       without script execution (<script> -> &lt;script&gt;, <img> -> &lt;img&gt;).
    3. Formula injection in reasons is rendered as non-executable text (no <a> tag).
    4. Exported CSV prefixes formula triggers with a single quote ('=HYPERLINK(...))
       to prevent spreadsheet formula execution when opened in Excel/Google Sheets,
       while keeping the text literal.
    """
    hostile_merchant = "<img src=x onerror=alert(1)>"
    hostile_reasons = ["<script>alert(1)</script>", '=HYPERLINK("http://example.com")']

    row_id = _insert(
        test_db,
        proposal_id="prop-hostile-1",
        status="BLOCKED",
        merchant=hostile_merchant,
        reasons=json.dumps(hostile_reasons),
        raw_proposal=json.dumps({"merchant": hostile_merchant, "amount": 10.0, "reason": "hostile test"}),
    )

    rows = get_ledger_rows(db_path=test_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == row_id

    # 1. Row data retains the exact strings
    assert row["merchant"] == hostile_merchant
    assert row["reasons"] == hostile_reasons

    # 2. Detail card HTML escaping
    escaped_merchant = html_text(row["merchant"])
    assert escaped_merchant == "&lt;img src=x onerror=alert(1)&gt;"
    assert "<img" not in escaped_merchant

    escaped_script = html_text(row["reasons"][0])
    assert escaped_script == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert "<script" not in escaped_script

    escaped_formula = html_text(row["reasons"][1])
    assert escaped_formula == '=HYPERLINK(&quot;http://example.com&quot;)'
    assert "<a " not in escaped_formula

    # 3. CSV export safety (defuses formula injection and keeps tags literal)
    csv_bytes = _ledger_csv([row])
    csv_text = csv_bytes.decode("utf-8")

    assert hostile_merchant in csv_text
    assert "<script>alert(1)</script>" in csv_text
    # Formula must be defused with leading single quote
    assert "'=HYPERLINK" in csv_text
    # Must not contain unquoted clickable formula trigger
    assert '=HYPERLINK("http://example.com")' not in csv_text or "'=HYPERLINK(\"http://example.com\")" in csv_text

    # Also verify formula defusing when formula is the first reason
    row_formula_first = dict(row)
    row_formula_first["reasons"] = ['=HYPERLINK("http://example.com")', "<script>alert(1)</script>"]
    csv_text_ff = _ledger_csv([row_formula_first]).decode("utf-8")
    assert "'=HYPERLINK" in csv_text_ff

    # And with leading whitespace before formula trigger
    assert _csv_safe("   =SUM(1,2)") == "'   =SUM(1,2)"
    assert _csv_safe("\t=HYPERLINK(1)") == "'\t=HYPERLINK(1)"

