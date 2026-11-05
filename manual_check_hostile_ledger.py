"""
Manual check script for hostile text in the Agent Allowance ledger.

This script inserts two test transactions with hostile text (one BLOCKED for the
Ledger, one NEEDS_APPROVAL for the Approvals page):
- Merchant: <img src=x onerror=alert(1)>
- Category and PayPal order id: </div><div>
- Reasons: <script>alert(1)</script>, </div><div>, =HYPERLINK("http://example.com")
  and a 600-character unbroken string
- Agent reason: <script>alert(1)</script> plus the long unbroken string

It verifies that:
1. get_ledger_rows() retrieves literal strings.
2. The detail card, verdict card and pipeline HTML escaping (html_text) neutralizes tags.
3. The CSV export (_ledger_csv) escapes formula injection with a single quote prefix.
4. Explains how to inspect and verify the literal rendering in the browser.

Usage: python manual_check_hostile_ledger.py [db_path]
"""

import json
import sys
import uuid
from agent_allowance.db import init_db, get_db_connection, get_ledger_rows
from agent_allowance.app import _ledger_csv, _csv_safe
from agent_allowance.ui_theme import get_pipeline_html, get_verdict_html, html_text, pipeline_states

HOSTILE_MERCHANT = "<img src=x onerror=alert(1)>"
BREAKOUT = "</div><div>"
LONG_UNBROKEN = "A" * 600
HOSTILE_REASONS = ["<script>alert(1)</script>", BREAKOUT, '=HYPERLINK("http://example.com")', LONG_UNBROKEN]
HOSTILE_AGENT_REASON = "<script>alert(1)</script> " + LONG_UNBROKEN


def insert_hostile_row(db_path: str, status: str, proposal_id: str) -> int:
    proposal_id = f"{proposal_id}-{uuid.uuid4().hex[:8]}"  # unique so the script can be re-run
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO transactions (
            proposal_id, merchant, amount, currency, category, reason, status,
            reasons, evaluation_reason, raw_proposal, paypal_order_id, created_at
        )
        VALUES (?, ?, 15.00, 'USD', ?, ?, ?, ?, NULL, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            proposal_id,
            HOSTILE_MERCHANT,
            BREAKOUT,
            HOSTILE_AGENT_REASON,
            status,
            json.dumps(HOSTILE_REASONS),
            json.dumps({"merchant": HOSTILE_MERCHANT, "amount": 15.00, "reason": HOSTILE_AGENT_REASON}),
            BREAKOUT if status == "BLOCKED" else "",
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def run_manual_check(db_path: str = "agent_allowance.db"):
    print("=" * 60)
    print("Agent Allowance: Manual Check for Hostile Text in Ledger")
    print("=" * 60)

    init_db(db_path)
    hostile_merchant = HOSTILE_MERCHANT
    hostile_reasons = HOSTILE_REASONS
    inserted_id = insert_hostile_row(db_path, "BLOCKED", "prop-manual-hostile-check")
    pending_id = insert_hostile_row(db_path, "NEEDS_APPROVAL", "prop-manual-hostile-pending")

    print(f"\n[1] Inserted hostile BLOCKED row #{inserted_id} and NEEDS_APPROVAL row #{pending_id} into '{db_path}'.")

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
    esc_reason2 = html_text(row["reasons"][2])
    print(f"    Merchant HTML: {esc_merchant}")
    print(f"    Reason 0 HTML: {esc_reason0}")
    print(f"    Reason 1 HTML: {esc_reason1}")
    print(f"    Reason 2 HTML: {esc_reason2}")
    print(f"    Reason 3:      {len(row['reasons'][3])} unbroken characters")
    assert "<img" not in esc_merchant and "&lt;img" in esc_merchant
    assert "<script" not in esc_reason0 and "&lt;script" in esc_reason0
    assert esc_reason1 == "&lt;/div&gt;&lt;div&gt;"
    assert "<a " not in esc_reason2
    assert row["reasons"][3] == LONG_UNBROKEN
    print("    -> No unescaped HTML tags or executable script tags: OK")

    # Verify the chat verdict card and pipeline builders with the same hostile text
    print("\n[3b] Verdict card and pipeline strip escaping:")
    verdict = get_verdict_html(
        status="BLOCKED", title="Blocked by your rules", merchant=row["merchant"], amount=15.0,
        currency="USD", category=BREAKOUT, reason=HOSTILE_AGENT_REASON, policy_reasons=row["reasons"],
        next_step="No payment was made.",
    )
    pipeline = get_pipeline_html(pipeline_states("decided", status="BLOCKED"), f"{row['merchant']} · $15.00")
    for name, markup in (("verdict", verdict), ("pipeline", pipeline)):
        assert "<img" not in markup and "<script" not in markup, name
        assert markup.count("<div") == markup.count("</div>"), f"{name}: unbalanced <div>"
    print("    -> Verdict card and pipeline contain no live tags and balanced <div>s: OK")

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
    print("   - Reasons column: '</div><div>' shows as text and the 600-character string wraps.")
    print(f"4. Click on row #{inserted_id} to open the detail card:")
    print(f"   - The card title shows '#{inserted_id} · <img src=x onerror=alert(1)>' as text.")
    print("   - Policy reasons list displays each hostile item literally; no clickable link.")
    print("   - The long string wraps inside the card; nothing spills past its border.")
    print(f"5. Open the 'Approvals' tab and find request #{pending_id}:")
    print("   - Merchant, category ('</div><div>'), reasons and agent reason show as literal text.")
    print("   - The Approve and Reject buttons stay below the card, not pushed outside it.")
    print("6. Click 'Export CSV':")
    print("   - Open the downloaded CSV in a spreadsheet editor or text editor.")
    print("   - Formula is prefixed with apostrophe ('=HYPERLINK...) and does not execute.")
    print("=" * 60)


if __name__ == "__main__":
    run_manual_check(sys.argv[1] if len(sys.argv) > 1 else "agent_allowance.db")
