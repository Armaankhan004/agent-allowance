"""
Test suite for the deterministic policy engine.
Verifies all rule evaluations, input validations, edge cases, and human escalation workflows
using isolated temporary SQLite databases.
"""

from decimal import Decimal
import pytest
from agent_allowance.db import init_db, get_db_connection
from agent_allowance.policy_engine import (
    evaluate_proposal,
    approve_pending,
    reject_pending,
    get_monthly_approved_spend,
)


@pytest.fixture
def test_db(tmp_path):
    """Fixture providing an isolated SQLite database initialized with standard rules."""
    db_file = str(tmp_path / "test_policy.db")
    init_db(db_file)
    return db_file


def test_normal_approval(test_db):
    """A valid proposal under limits should be automatically APPROVED."""
    proposal = {
        "merchant": "Amazon",
        "amount": "15.00",
        "currency": "USD",
        "category": "Office Supplies",
        "reason": "Printer paper and ink",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)

    assert decision.status == "APPROVED"
    assert decision["status"] == "APPROVED"
    assert len(decision.reasons) > 0
    assert "approved automatically" in decision.reasons[0].lower()
    assert decision.ledger_id is not None

    # Check ledger directly
    conn = get_db_connection(test_db)
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (decision.ledger_id,)).fetchone()
    conn.close()
    assert row["status"] == "APPROVED"
    assert row["merchant"] == "Amazon"
    assert row["amount"] == 15.0


def test_over_per_purchase_cap(test_db):
    """A purchase exceeding the $50 max cap must be BLOCKED."""
    proposal = {
        "merchant": "Amazon",
        "amount": "65.00",  # default cap is 50.00
        "currency": "USD",
        "category": "Hardware",
        "reason": "External SSD drive",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)

    assert decision.status == "BLOCKED"
    assert any("exceeds the maximum per-purchase limit" in r for r in decision.reasons)


def test_over_monthly_budget_cumulative(test_db):
    """Cumulative spend exceeding $200 monthly budget must trigger BLOCKED."""
    # Seed 4 approved purchases of $45 each = $180.00 total
    for i in range(4):
        p = {
            "merchant": "Best Buy",
            "amount": "20.00",  # under 25 threshold to auto-approve
            "currency": "USD",
            "category": "Tech",
            "reason": f"Cables {i}",
        }
        dec = evaluate_proposal(p, db_path=test_db)
        assert dec.status == "APPROVED"

    current_spend = get_monthly_approved_spend(db_path=test_db)
    assert current_spend == Decimal("80.00")

    # Add another large approved transaction: $110 (set in db directly to simulate high spend)
    conn = get_db_connection(test_db)
    conn.execute(
        """
        INSERT INTO transactions (proposal_id, merchant, amount, currency, category, reason, status)
        VALUES ('prop-bulk', 'Best Buy', 110.00, 'USD', 'Tech', 'Monitor', 'APPROVED')
        """
    )
    conn.commit()
    conn.close()

    assert get_monthly_approved_spend(db_path=test_db) == Decimal("190.00")

    # Next purchase of $15 would bring total to $205 > $200 budget
    proposal = {
        "merchant": "Target",
        "amount": "15.00",
        "currency": "USD",
        "category": "Supplies",
        "reason": "Pens and notebooks",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)

    assert decision.status == "BLOCKED"
    assert any("exceeding the monthly budget" in r for r in decision.reasons)


def test_merchant_not_on_allowlist(test_db):
    """Merchants not on the allowlist must be BLOCKED."""
    proposal = {
        "merchant": "UnknownShadyStore",
        "amount": "10.00",
        "currency": "USD",
        "category": "Books",
        "reason": "Technical manual",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)

    assert decision.status == "BLOCKED"
    assert any("not on the allowed merchants list" in r for r in decision.reasons)


def test_amount_over_approval_threshold(test_db):
    """Purchases above $25 but below $50 cap must receive NEEDS_APPROVAL."""
    proposal = {
        "merchant": "Target",
        "amount": "35.00",  # above 25.00 threshold, below 50.00 cap
        "currency": "USD",
        "category": "Office Supplies",
        "reason": "Desk lamp",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)

    assert decision.status == "NEEDS_APPROVAL"
    assert any("exceeds the human approval threshold" in r for r in decision.reasons)


def test_multiple_rules_failing_at_once_all_reasons_listed(test_db):
    """All violated rules must be collected and listed in reasons, not just the first one."""
    proposal = {
        "merchant": "UnapprovedMerchantX",  # Rule 1 fail: unallowed merchant
        "amount": "95.00",                  # Rule 2 fail: over $50 cap
        "currency": "EUR",                  # Rule 3 fail: invalid currency
        "category": "Gadgets",
        "reason": "Wireless earbuds",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)

    assert decision.status == "BLOCKED"
    # Verify every failure is present in reasons
    assert any("not on the allowed merchants list" in r for r in decision.reasons)
    assert any("exceeds the maximum per-purchase limit" in r for r in decision.reasons)
    assert any("unsupported currency" in r.lower() for r in decision.reasons)
    assert len(decision.reasons) >= 3


def test_invalid_inputs(test_db):
    """Reject missing fields, non-numeric amounts, zero and negative amounts."""
    # 1. Missing fields
    dec1 = evaluate_proposal({"amount": "10.00", "currency": "USD"}, db_path=test_db)
    assert dec1.status == "BLOCKED"
    assert any("missing required field: 'merchant'" in r.lower() for r in dec1.reasons)
    assert any("missing required field: 'category'" in r.lower() for r in dec1.reasons)
    assert any("missing required field: 'reason'" in r.lower() for r in dec1.reasons)

    # 2. Negative amount
    dec2 = evaluate_proposal(
        {"merchant": "Amazon", "amount": "-15.00", "currency": "USD", "category": "Tech", "reason": "Refund"},
        db_path=test_db,
    )
    assert dec2.status == "BLOCKED"
    assert any("must be positive" in r.lower() for r in dec2.reasons)

    # 3. Zero amount
    dec3 = evaluate_proposal(
        {"merchant": "Amazon", "amount": "0.00", "currency": "USD", "category": "Tech", "reason": "Freebie"},
        db_path=test_db,
    )
    assert dec3.status == "BLOCKED"
    assert any("must be positive" in r.lower() for r in dec3.reasons)

    # 4. Non-numeric amount
    dec4 = evaluate_proposal(
        {"merchant": "Amazon", "amount": "fifty_dollars", "currency": "USD", "category": "Tech", "reason": "Item"},
        db_path=test_db,
    )
    assert dec4.status == "BLOCKED"
    assert any("invalid or non-numeric amount" in r.lower() for r in dec4.reasons)


def test_case_insensitive_merchant_matching(test_db):
    """Merchant matching against allowlist must be case-insensitive."""
    merchants = ["aMaZoN", "TARGET", "bEsT bUy", "walmart", "GITHUB"]
    for m in merchants:
        proposal = {
            "merchant": m,
            "amount": "10.00",
            "currency": "USD",
            "category": "Software/Items",
            "reason": "Test item",
        }
        decision = evaluate_proposal(proposal, db_path=test_db)
        assert decision.status == "APPROVED", f"Failed for merchant {m}: {decision.reasons}"


def test_human_approval_flow_and_monthly_budget(test_db):
    """
    NEEDS_APPROVAL spend must NOT count toward monthly budget until approved by a human.
    approve_pending must transition status and update monthly spend.
    """
    proposal = {
        "merchant": "Target",
        "amount": "30.00",
        "currency": "USD",
        "category": "Supplies",
        "reason": "Office chair cushion",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)
    assert decision.status == "NEEDS_APPROVAL"

    # Crucial: Needs approval spend does NOT yet count toward monthly approved spend
    spend_before = get_monthly_approved_spend(db_path=test_db)
    assert spend_before == Decimal("0.00")

    # Approve transaction
    approved_record = approve_pending(decision.ledger_id, db_path=test_db)
    assert approved_record["status"] == "APPROVED"

    # Spend now counts toward monthly budget
    spend_after = get_monthly_approved_spend(db_path=test_db)
    assert spend_after == Decimal("30.00")


def test_human_rejection_flow(test_db):
    """reject_pending must transition status to REJECTED and never count toward monthly budget."""
    proposal = {
        "merchant": "Target",
        "amount": "40.00",
        "currency": "USD",
        "category": "Supplies",
        "reason": "Coffee machine",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)
    assert decision.status == "NEEDS_APPROVAL"

    rejected_record = reject_pending(decision.ledger_id, db_path=test_db, rejection_reason="Too expensive for today.")
    assert rejected_record["status"] == "REJECTED"

    # Monthly spend remains 0
    assert get_monthly_approved_spend(db_path=test_db) == Decimal("0.00")


def test_approve_pending_refuses_when_monthly_budget_exceeded(test_db):
    """
    approve_pending must re-check the monthly budget. If approving would exceed the
    monthly budget, it must refuse approval and retain NEEDS_APPROVAL status.
    """
    # 1. Create a proposal that needs human approval ($30.00)
    proposal = {
        "merchant": "Target",
        "amount": "30.00",
        "currency": "USD",
        "category": "Supplies",
        "reason": "Office supplies pack",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)
    assert decision.status == "NEEDS_APPROVAL"

    # 2. In the meantime, other transactions spend $185.00 of the $200.00 budget
    conn = get_db_connection(test_db)
    conn.execute(
        """
        INSERT INTO transactions (proposal_id, merchant, amount, currency, category, reason, status)
        VALUES ('prop-fill-budget', 'Amazon', 185.00, 'USD', 'Supplies', 'Bulk paper', 'APPROVED')
        """
    )
    conn.commit()
    conn.close()

    # 3. Attempt to approve the $30.00 item ($185 + $30 = $215 > $200)
    result = approve_pending(decision.ledger_id, db_path=test_db)

    assert result["approved"] is False
    assert result["status"] == "NEEDS_APPROVAL"
    assert "Approval refused" in result["refusal_reason"]
    assert "exceeding the monthly budget" in result["refusal_reason"]

    # 4. Verify DB transaction status is still NEEDS_APPROVAL
    conn = get_db_connection(test_db)
    row = conn.execute("SELECT status FROM transactions WHERE id = ?", (decision.ledger_id,)).fetchone()
    conn.close()
    assert row["status"] == "NEEDS_APPROVAL"


def test_amount_exactly_equal_to_max_per_purchase_is_allowed(test_db):
    """
    An amount exactly equal to max_per_purchase ($50.00) must be allowed
    and NOT blocked by the per-purchase cap.
    """
    # Test with default rules where max_per_purchase is 50.00 and approval_threshold is 25.00
    proposal = {
        "merchant": "Target",
        "amount": "50.00",
        "currency": "USD",
        "category": "Hardware",
        "reason": "Desk organizer",
    }
    decision = evaluate_proposal(proposal, db_path=test_db)

    # It requires human approval (since 50.00 > 25.00), but is NOT BLOCKED by the 50.00 cap!
    assert decision.status == "NEEDS_APPROVAL"
    assert not any("exceeds the maximum per-purchase limit" in r for r in decision.reasons)

    # Now adjust approval_threshold to 50.00 in rules to confirm exact boundary auto-approval
    conn = get_db_connection(test_db)
    conn.execute(
        "UPDATE rules SET condition_value = '50.00' WHERE rule_type = 'approval_threshold'"
    )
    conn.commit()
    conn.close()

    proposal_exact = {
        "merchant": "Amazon",
        "amount": "50.00",
        "currency": "USD",
        "category": "Hardware",
        "reason": "Exact boundary item",
    }
    decision_exact = evaluate_proposal(proposal_exact, db_path=test_db)
    # Exactly equal to threshold and cap is fully APPROVED
    assert decision_exact.status == "APPROVED"
    assert not any("exceeds" in r for r in decision_exact.reasons)

