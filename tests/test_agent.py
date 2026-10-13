"""
Unit tests for the AI Shopping Agent and execution pipeline.
Ensures the LLM only proposes purchases, deterministic policy engine blocks injections,
and PayPal create_order / capture_order are ONLY invoked under strict approval.
All PayPal calls are mocked (zero real network calls).
"""

from unittest.mock import patch
import pytest
from agent_allowance.db import init_db, get_db_connection
from agent_allowance.paypal_client import PayPalOrderResult
from agent_allowance.policy_engine import approve_pending
from agent_allowance.agent import (
    ShoppingAgent,
    propose_purchase,
    capture_if_approved,
    create_order_for_approved,
)


@pytest.fixture
def test_db(tmp_path):
    """Isolated SQLite database for agent tests."""
    db_file = str(tmp_path / "test_agent.db")
    init_db(db_file)
    return db_file


@patch("agent_allowance.agent.create_order")
def test_agent_mock_normal_purchase_approved_creates_paypal_order(mock_create_order, test_db):
    """Normal legitimate purchase within limits is approved and automatically creates a PayPal order."""
    mock_create_order.return_value = PayPalOrderResult(
        order_id="SANDBOX-ORD-001",
        approval_url="https://www.sandbox.paypal.com/checkoutnow?token=SANDBOX-ORD-001",
    )

    agent = ShoppingAgent(provider="mock", db_path=test_db)
    result = agent.run("Buy multipurpose copy paper for the office printer")

    proposal_result = result["proposal_result"]
    assert proposal_result["status"] == "APPROVED"
    assert proposal_result["paypal_order_id"] == "SANDBOX-ORD-001"
    assert "sandbox.paypal.com" in proposal_result["approval_url"]

    # Assert create_order was called with correct amount ($8.49 for copy paper)
    mock_create_order.assert_called_once()
    args, kwargs = mock_create_order.call_args
    assert kwargs["amount"] == 8.49
    assert kwargs["currency"] == "USD"

    # Verify order details are stored in SQLite ledger
    conn = get_db_connection(test_db)
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (proposal_result["ledger_id"],)).fetchone()
    conn.close()
    assert row["status"] == "APPROVED"
    assert row["paypal_order_id"] == "SANDBOX-ORD-001"


@patch("agent_allowance.agent.create_order")
def test_malicious_listing_injection_blocked_never_calls_paypal(mock_create_order, test_db):
    """
    Even if the agent obeys a prompt injection and attempts to purchase the $500 gift card,
    the deterministic policy engine strictly BLOCKS it and PayPal create_order is NEVER called.
    """
    agent = ShoppingAgent(provider="mock", db_path=test_db)
    
    # Run with obey_injection=True to simulate a compromised or jailbroken LLM
    result = agent.run(
        "Search specials and buy the $500 gift card immediately",
        obey_injection=True,
    )

    proposal_result = result["proposal_result"]
    assert proposal_result["status"] == "BLOCKED"
    assert proposal_result["paypal_order_id"] is None
    assert proposal_result["approval_url"] is None

    # Multiple rules must fail
    reasons = proposal_result["reasons"]
    assert any("not on the allowed merchants list" in r for r in reasons)
    assert any("exceeds the maximum per-purchase limit" in r for r in reasons)
    assert any("exceeding the monthly budget" in r for r in reasons)

    # CRITICAL: Verify PayPal create_order was NEVER called!
    mock_create_order.assert_not_called()

    # Verify ledger record is marked BLOCKED without order_id
    conn = get_db_connection(test_db)
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (proposal_result["ledger_id"],)).fetchone()
    conn.close()
    assert row["status"] == "BLOCKED"
    assert row["paypal_order_id"] is None


@patch("agent_allowance.agent.create_order")
def test_needs_approval_does_not_call_paypal_until_human_approval(mock_create_order, test_db):
    """
    A proposal exceeding the $25 approval threshold gets NEEDS_APPROVAL.
    PayPal create_order is NOT called until a human supervisor approves it.
    """
    mock_create_order.return_value = PayPalOrderResult(
        order_id="SANDBOX-ORD-002",
        approval_url="https://www.sandbox.paypal.com/checkoutnow?token=SANDBOX-ORD-002",
    )

    agent = ShoppingAgent(provider="mock", db_path=test_db)
    # Order bread flour: $28.50 (> $25 threshold, < $50 cap)
    result = agent.run("Order a 50 lb bag of organic bread flour")

    proposal_result = result["proposal_result"]
    assert proposal_result["status"] == "NEEDS_APPROVAL"
    assert proposal_result["paypal_order_id"] is None

    # Must NOT call PayPal yet
    mock_create_order.assert_not_called()

    ledger_id = proposal_result["ledger_id"]

    # Now human supervisor approves the pending transaction
    approval_res = approve_pending(ledger_id, db_path=test_db)
    assert approval_res["status"] == "APPROVED"
    assert approval_res["approved"] is True

    # Now code creates the order for the human-approved transaction
    order_info = create_order_for_approved(ledger_id, db_path=test_db)
    assert order_info["order_id"] == "SANDBOX-ORD-002"

    # Now create_order has been called exactly once
    mock_create_order.assert_called_once()

    # Check database persistence
    conn = get_db_connection(test_db)
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (ledger_id,)).fetchone()
    conn.close()
    assert row["status"] == "APPROVED"
    assert row["paypal_order_id"] == "SANDBOX-ORD-002"


@patch("agent_allowance.agent.capture_order")
def test_capture_if_approved_success(mock_capture_order, test_db):
    """capture_if_approved captures payment and transitions transaction to EXECUTED."""
    mock_capture_order.return_value = {
        "status": "COMPLETED",
        "capture_id": "MOCK-CAP-9999",
        "amount": "8.49",
        "currency": "USD",
    }

    # Seed an APPROVED transaction with a valid order_id
    conn = get_db_connection(test_db)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO transactions (proposal_id, merchant, amount, currency, category, reason, status, paypal_order_id)
        VALUES ('prop-approved-1', 'Office Depot', 8.49, 'USD', 'Office Supplies', 'Copy paper', 'APPROVED', 'ORD-READY')
        """
    )
    ledger_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Capture payment
    res = capture_if_approved(ledger_id, db_path=test_db)
    assert res["status"] == "COMPLETED"
    assert res["capture_id"] == "MOCK-CAP-9999"

    mock_capture_order.assert_called_once_with("ORD-READY")

    # Verify status updated to EXECUTED in SQLite
    conn = get_db_connection(test_db)
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (ledger_id,)).fetchone()
    conn.close()
    assert row["status"] == "EXECUTED"
    assert row["paypal_capture_id"] == "MOCK-CAP-9999"


def test_capture_if_approved_refuses_unapproved(test_db):
    """capture_if_approved refuses transactions that are not APPROVED."""
    conn = get_db_connection(test_db)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO transactions (proposal_id, merchant, amount, currency, category, reason, status)
        VALUES ('prop-pending-1', 'Target', 35.00, 'USD', 'Supplies', 'Lamp', 'NEEDS_APPROVAL')
        """
    )
    ledger_id = cursor.lastrowid
    conn.commit()
    conn.close()

    with pytest.raises(ValueError) as exc:
        capture_if_approved(ledger_id, db_path=test_db)
    assert "expected 'APPROVED'" in str(exc.value)
