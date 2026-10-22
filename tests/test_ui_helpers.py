"""
Unit tests for non-UI helper functions supporting the Streamlit dashboard:
- Rule validation and updating (validate_rule_value, update_policy_rule, get_policy_rules)
- Ledger queries and demo data reset (reset_demo_transactions, get_all_transactions, get_pending_transactions)
"""

import pytest
from decimal import Decimal
from agent_allowance.db import (
    init_db,
    get_db_connection,
    reset_demo_transactions,
    get_all_transactions,
    get_pending_transactions,
    get_dashboard_metrics,
)
from agent_allowance.policy_engine import (
    validate_rule_value,
    get_policy_rules,
    update_policy_rule,
)
from agent_allowance.app import get_chat_outcome_label_and_state



@pytest.fixture
def test_db(tmp_path):
    """Isolated SQLite database fixture."""
    db_file = str(tmp_path / "test_ui_helpers.db")
    init_db(db_file)
    return db_file


# ==============================================================================
# Rule Validation Tests
# ==============================================================================

def test_validate_rule_value_numeric_rules_success():
    """Valid positive decimal amounts should pass validation."""
    for rt in ("max_per_purchase", "monthly_budget", "approval_threshold"):
        is_valid, err = validate_rule_value(rt, "50.00")
        assert is_valid is True
        assert err is None

        is_valid, err = validate_rule_value(rt, "123.45")
        assert is_valid is True
        assert err is None


def test_validate_rule_value_numeric_rules_reject_non_positive_or_invalid():
    """Zero, negative, empty, or non-numeric strings must fail validation."""
    for rt in ("max_per_purchase", "monthly_budget", "approval_threshold"):
        # Zero
        is_valid, err = validate_rule_value(rt, "0.00")
        assert is_valid is False
        assert "must be greater than $0.00" in err

        # Negative
        is_valid, err = validate_rule_value(rt, "-15.00")
        assert is_valid is False
        assert "must be greater than $0.00" in err

        # Empty
        is_valid, err = validate_rule_value(rt, "   ")
        assert is_valid is False
        assert "cannot be empty" in err

        # Non-numeric
        is_valid, err = validate_rule_value(rt, "one hundred")
        assert is_valid is False
        assert "not a valid decimal number" in err


def test_validate_rule_value_allowed_merchants():
    """Allowlist must contain at least one valid merchant."""
    # Valid
    is_valid, err = validate_rule_value("allowed_merchants", "Golden Grain Mills, EcoPack Supplies")
    assert is_valid is True
    assert err is None

    # Single merchant
    is_valid, err = validate_rule_value("allowed_merchants", "Corner Office Supply")
    assert is_valid is True
    assert err is None

    # Empty string or commas only
    is_valid, err = validate_rule_value("allowed_merchants", "")
    assert is_valid is False

    is_valid, err = validate_rule_value("allowed_merchants", "  ,  ,  ")
    assert is_valid is False
    assert "at least one valid merchant" in err


# ==============================================================================
# Rule Query and Mutation Tests
# ==============================================================================

def test_get_policy_rules(test_db):
    """Retrieve active rules as a dictionary indexed by rule_type."""
    rules = get_policy_rules(db_path=test_db)
    assert "max_per_purchase" in rules
    assert "monthly_budget" in rules
    assert "allowed_merchants" in rules
    assert "approval_threshold" in rules
    assert rules["max_per_purchase"]["condition_value"] == "50.00"


def test_update_policy_rule_success(test_db):
    """Successfully validate and update a policy rule."""
    res = update_policy_rule("max_per_purchase", "75.00", db_path=test_db)
    assert res["rule_type"] == "max_per_purchase"
    assert res["new_value"] == "75.00"

    rules = get_policy_rules(db_path=test_db)
    assert rules["max_per_purchase"]["condition_value"] == "75.00"


def test_update_policy_rule_invalid_raises_error(test_db):
    """Updating with invalid values raises ValueError without updating db."""
    with pytest.raises(ValueError) as exc:
        update_policy_rule("max_per_purchase", "-10.00", db_path=test_db)
    assert "greater than $0.00" in str(exc.value)

    # Verify db was not changed
    rules = get_policy_rules(db_path=test_db)
    assert rules["max_per_purchase"]["condition_value"] == "50.00"


# ==============================================================================
# Ledger & Reset Demo Transactions Tests
# ==============================================================================

def test_reset_demo_transactions_clears_ledger_preserves_rules(test_db):
    """
    reset_demo_transactions must delete all transactions and audit_logs,
    but MUST NOT delete rules.
    """
    conn = get_db_connection(test_db)
    cursor = conn.cursor()

    # Seed transactions
    cursor.execute(
        """
        INSERT INTO transactions (proposal_id, merchant, amount, currency, category, reason, status)
        VALUES ('prop-1', 'Golden Grain Mills', 20.00, 'USD', 'Baking', 'Flour', 'APPROVED'),
               ('prop-2', 'Corner Office Supply', 10.00, 'USD', 'Office', 'Paper', 'APPROVED')
        """
    )
    # Seed audit log
    cursor.execute(
        "INSERT INTO audit_logs (proposal_id, step, result, details) VALUES ('prop-1', 'CHECK', 'OK', 'Passed')"
    )
    conn.commit()
    conn.close()

    # Verify transactions exist
    assert len(get_all_transactions(db_path=test_db)) == 2

    # Reset demo transactions
    deleted_count = reset_demo_transactions(db_path=test_db)
    assert deleted_count == 2

    # Transactions and audit logs should be empty
    assert len(get_all_transactions(db_path=test_db)) == 0

    conn = get_db_connection(test_db)
    audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    conn.close()
    assert audit_count == 0

    # CRITICAL: Rules must NOT be deleted!
    rules = get_policy_rules(db_path=test_db)
    assert len(rules) >= 4
    assert "max_per_purchase" in rules


def test_get_pending_transactions(test_db):
    """Retrieve only transactions with status NEEDS_APPROVAL."""
    conn = get_db_connection(test_db)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO transactions (proposal_id, merchant, amount, currency, category, reason, status)
        VALUES ('prop-appr', 'Golden Grain Mills', 10.00, 'USD', 'Baking', 'Flour', 'APPROVED'),
               ('prop-pend', 'Corner Office Supply', 35.00, 'USD', 'Office', 'Paper', 'NEEDS_APPROVAL'),
               ('prop-block', 'Apex Gift Cards', 500.00, 'USD', 'Finance', 'Card', 'BLOCKED')
        """
    )
    conn.commit()
    conn.close()

    pending = get_pending_transactions(db_path=test_db)
    assert len(pending) == 1
    assert pending[0]["proposal_id"] == "prop-pend"
    assert pending[0]["status"] == "NEEDS_APPROVAL"


# ==============================================================================
# Chat UI Outcome Label & Status State Tests
# ==============================================================================

def test_get_chat_outcome_label_and_state():
    """
    Verify Chat UI outcome labels and state mapping:
    - Purchase approved -> "Purchase approved", state="complete" (green check)
    - Needs approval -> "Needs your approval", state="error" (no green check)
    - Blocked -> "Blocked by policy", state="error" (no green check)
    - No purchase proposed -> "No purchase proposed", state="error" (no green check)
    - Error -> "Agent error", state="error" (no green check)
    """
    # 1. Approved purchase -> green check ("complete")
    label, state = get_chat_outcome_label_and_state({
        "proposal_result": {"status": "APPROVED", "amount": 8.49}
    })
    assert label == "Purchase approved"
    assert state == "complete"

    # 2. Needs approval -> no green check ("error")
    label, state = get_chat_outcome_label_and_state({
        "proposal_result": {"status": "NEEDS_APPROVAL", "amount": 35.00}
    })
    assert label == "Needs your approval"
    assert state != "complete"
    assert state == "error"

    # 3. Blocked by policy -> no green check ("error")
    label, state = get_chat_outcome_label_and_state({
        "proposal_result": {"status": "BLOCKED", "amount": 500.00}
    })
    assert label == "Blocked by policy"
    assert state != "complete"
    assert state == "error"

    # 4. No purchase proposed -> no green check ("error")
    label, state = get_chat_outcome_label_and_state({
        "proposal_result": None,
        "agent_response": "Hello, how can I help?"
    })
    assert label == "No purchase proposed"
    assert state != "complete"
    assert state == "error"

    # 5. Agent error -> no green check ("error")
    label, state = get_chat_outcome_label_and_state({
        "error": "LLM API connection timeout",
        "proposal_result": None
    })
    assert label == "Agent error"
    assert state != "complete"
    assert state == "error"


def test_get_dashboard_metrics(test_db):
    """
    Verify get_dashboard_metrics correctly calculates:
    - approved_spend, monthly_budget, budget_remaining, budget_used_pct,
    - pending_count, blocked_count.
    """
    # Baseline on fresh database
    m_fresh = get_dashboard_metrics(db_path=test_db)
    assert m_fresh["approved_spend"] == Decimal("0.00")
    assert m_fresh["monthly_budget"] == Decimal("200.00")
    assert m_fresh["budget_remaining"] == Decimal("200.00")
    assert m_fresh["budget_used_pct"] == 0.0
    assert m_fresh["pending_count"] == 0
    assert m_fresh["blocked_count"] == 0

    # Insert test transactions
    conn = get_db_connection(test_db)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO transactions (proposal_id, merchant, amount, currency, category, reason, status, created_at)
        VALUES ('prop-appr-1', 'Golden Grain Mills', 30.00, 'USD', 'Baking', 'Flour', 'APPROVED', CURRENT_TIMESTAMP),
               ('prop-appr-2', 'Corner Office Supply', 15.00, 'USD', 'Office', 'Paper', 'APPROVED', CURRENT_TIMESTAMP),
               ('prop-pend-1', 'EcoPack Supplies', 35.00, 'USD', 'Packaging', 'Boxes', 'NEEDS_APPROVAL', CURRENT_TIMESTAMP),
               ('prop-pend-2', 'EcoPack Supplies', 40.00, 'USD', 'Packaging', 'Boxes', 'NEEDS_APPROVAL', CURRENT_TIMESTAMP),
               ('prop-block-1', 'Apex Gift Cards', 500.00, 'USD', 'Finance', 'Card', 'BLOCKED', CURRENT_TIMESTAMP)
        """
    )
    conn.commit()
    conn.close()

    m = get_dashboard_metrics(db_path=test_db)
    assert m["approved_spend"] == Decimal("45.00")
    assert m["monthly_budget"] == Decimal("200.00")
    assert m["budget_remaining"] == Decimal("155.00")
    assert abs(m["budget_used_pct"] - 0.225) < 1e-4
    assert m["pending_count"] == 2
    assert m["blocked_count"] == 1


