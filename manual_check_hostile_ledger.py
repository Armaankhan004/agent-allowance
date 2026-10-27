"""
Manual check script for hostile text in the Agent Allowance ledger.

This script inserts a test transaction with hostile text:
- Merchant: <img src=x onerror=alert(1)>
- Reasons: <script>alert(1)</script> and =HYPERLINK("http://example.com")

It verifies that:
1. get_ledger_rows() retrieves literal strings.
2. The detail card HTML escaping (html_text) neutralizes tags.
3. The CSV export (_ledger_csv) escapes formula injection with a single quote prefix.
4. Explains how to inspect and verify the literal rendering in the browser.
"""

import json
import sys
from agent_allowance.db import init_db, get_db_connection, get_ledger_rows
from agent_allowance.app import _ledger_csv, _csv_safe
from agent_allowance.ui_theme import html_text


def run_manual_check(db_path: str = "agent_allowance.db"):
    print("=" * 60)
    print("Agent Allowance: Manual Check for Hostile Text in Ledger")
    print("=" * 60)

    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    hostile_merchant = "<img src=x onerror=alert(1)>"
    hostile_reasons = ["<script>alert(1)</script>", '=HYPERLINK("http://example.com")']

    cursor.execute(
        """
        INSERT INTO transactions (
            proposal_id, merchant, amount, currency, category, reason, status,
            reasons, evaluation_reason, raw_proposal, paypal_order_id, created_at
        )
        VALUES (?, ?, 15.00, 'USD', 'Office Supplies', 'Hostile text manual check', 'BLOCKED',
                ?, NULL, ?, '', CURRENT_TIMESTAMP)
        """,
        (
            "prop-manual-hostile-check",
            hostile_merchant,
            json.dumps(hostile_reasons),
            json.dumps({"merchant": hostile_merchant, "amount": 15.00, "reason": "Hostile text manual check"}),
        ),
    )
    conn.commit()
    inserted_id = cursor.lastrowid
    conn.close()

    print(f"\n[1] Inserted hostile ledger row #{inserted_id} into '{db_path}'.")

    # Verify query
    rows = get_ledger_rows(db_path=db_path)
    row = next((r for r in rows if r["id"] == inserted_id), None)
    if not row:
        print("ERROR: Row not found in get_ledger_rows()")
        sys.exit(1)

    print("\n[2] Database query verification:")
    print(f"    Merchant (raw): {row['merchant']}")
    print(f"    Reasons (raw):  {row['reasons']}")
    assert row["merchant"] == hostile_merchant
    assert row["reasons"] == hostile_reasons
    print("    -> Stored and retrieved faithfully without corruption: OK")

    # Verify Detail Card HTML Escaping
    print("\n[3] Detail card HTML escaping (html_text):")
    esc_merchant = html_text(row["merchant"])
    esc_reason0 = html_text(row["reasons"][0])
    esc_reason1 = html_text(row["reasons"][1])
    print(f"    Merchant HTML: {esc_merchant}")
    print(f"    Reason 0 HTML: {esc_reason0}")
    print(f"    Reason 1 HTML: {esc_reason1}")
    assert "<img" not in esc_merchant and "&lt;img" in esc_merchant
    assert "<script" not in esc_reason0 and "&lt;script" in esc_reason0
    assert "<a " not in esc_reason1
    print("    -> No unescaped HTML tags or executable script tags: OK")

    # Verify CSV Export
    print("\n[4] CSV Export formula defusing:")
    csv_bytes = _ledger_csv([row])
    csv_text = csv_bytes.decode("utf-8")
    print(f"    CSV output:\n    {csv_text.strip()}")
    assert hostile_merchant in csv_text
    assert "<script>alert(1)</script>" in csv_text
    assert "'=HYPERLINK" in csv_text
    print("    -> Formula trigger is prefixed with single quote ('=HYPERLINK...): OK")

    print("\n" + "=" * 60)
    print("MANUAL BROWSER VERIFICATION CHECKLIST:")
    print("1. Open the running Streamlit app (e.g. http://localhost:8501).")
    print("2. Navigate to the 'Ledger' tab.")
    print(f"3. Locate row #{inserted_id}:")
    print("   - Merchant column: Displays '<img src=x onerror=alert(1)>' as literal text.")
    print("   - Reasons column: Displays literal '<script>alert(1)</script>' and '=HYPERLINK(...)'.")
    print("   - Confirm no JavaScript alert dialog or console error occurs.")
    print("4. Click on row #{inserted_id} to open the detail card:")
    print("   - The card title shows '#{inserted_id} · <img src=x onerror=alert(1)>' as text.")
    print("   - Policy reasons list displays each hostile item literally; no clickable link.")
    print("5. Click 'Export CSV':")
    print("   - Open the downloaded CSV in a spreadsheet editor or text editor.")
    print("   - Formula is prefixed with apostrophe ('=HYPERLINK...) and does not execute.")
    print("=" * 60)


if __name__ == "__main__":
    run_manual_check()
