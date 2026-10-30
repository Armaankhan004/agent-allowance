"""
Unit tests for the complete payment loop:
- PayPal return & cancel URL configuration using APP_BASE_URL
- Payment return handling (?paypal=return&token=... and ?paypal=cancel)
- Safe token lookup & query param distrust
- Idempotent capture (cannot capture twice)
- Refusal to capture BLOCKED or NEEDS_APPROVAL rows
- Monthly budget counting both APPROVED and EXECUTED
"""

from decimal import Decimal
from unittest.mock import patch, MagicMock
import pytest

from agent_allowance.config import Config
from agent_allowance.db import (
    init_db,
    get_db_connection,
    get_transaction_by_order_id,
    get_dashboard_metrics,
)
from agent_allowance.paypal_client import PayPalSandboxClient, create_order
from agent_allowance.agent import capture_if_approved
from agent_allowance.policy_engine import get_monthly_approved_spend
from agent_allowance.app import process_payment_return, _approved_total


@pytest.fixture
def test_db(tmp_path):
    """Isolated temporary SQLite database."""
    db_file = str(tmp_path / "test_payment_return.db")
    init_db(db_file)
    return db_file


def _insert_tx(
    db_path: str,
    proposal_id: str,
    status: str,
    amount: float = 25.00,
    paypal_order_id: str = "",
    paypal_capture_id: str = "",
    created_at: str = "2026-10-28 12:00:00",
) -> int:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO transactions (
            proposal_id, merchant, amount, currency, category, reason, status,
            paypal_order_id, paypal_capture_id, created_at
        )
        VALUES (?, 'Corner Office Supply', ?, 'USD', 'Office Supplies', 'Test Order', ?,
                ?, ?, ?)
        """,
        (proposal_id, amount, status, paypal_order_id, paypal_capture_id, created_at),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


# ==============================================================================
# 1. Return URLs using APP_BASE_URL
# ==============================================================================

@patch("agent_allowance.paypal_client.requests.post")
def test_create_order_return_urls_use_app_base_url(mock_post):
    """Verify return_url and cancel_url use Config.APP_BASE_URL instead of example.com."""
    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "token_xyz", "expires_in": 3600}

    order_resp = MagicMock(status_code=201)
    order_resp.json.return_value = {
        "id": "ORD-123",
        "status": "CREATED",
        "links": [{"href": "https://paypal.sandbox/approve?token=ORD-123", "rel": "approve"}],
    }
    mock_post.side_effect = [token_resp, order_resp]

    client = PayPalSandboxClient(client_id="id", client_secret="secret")
    client.create_order(amount=10.00, currency="USD")

    # Second post is the create order call
    _, kwargs = mock_post.call_args_list[1]
    payload = kwargs["json"]
    exp = payload["payment_source"]["paypal"]["experience_context"]

    expected_base = Config.APP_BASE_URL.rstrip("/")
    assert exp["return_url"] == f"{expected_base}?paypal=return"
    assert exp["cancel_url"] == f"{expected_base}?paypal=cancel"
    assert "example.com" not in exp["return_url"]
    assert "example.com" not in exp["cancel_url"]


# ==============================================================================
# 2. Payment Return Handling
# ==============================================================================

@patch("agent_allowance.app.capture_if_approved")
@patch("agent_allowance.app.get_order_status")
def test_payment_return_successful_capture(mock_get_status, mock_capture, test_db):
    """
    When PayPal says APPROVED and the ledger row is APPROVED:
    - Calls capture_if_approved
    - Returns COMPLETED status and capture_id
    - Updates ledger to EXECUTED
    """
    row_id = _insert_tx(test_db, "p-success", "APPROVED", 35.00, paypal_order_id="ORD-APPROVED-99")

    mock_get_status.return_value = {"id": "ORD-APPROVED-99", "status": "APPROVED"}
    mock_capture.return_value = {
        "ledger_id": row_id,
        "paypal_order_id": "ORD-APPROVED-99",
        "paypal_capture_id": "CAP-REAL-123",
        "status": "COMPLETED",
    }

    params = {"paypal": "return", "token": "ORD-APPROVED-99"}
    res = process_payment_return(params, db_path=test_db)

    assert res["status"] == "COMPLETED"
    assert res["capture_id"] == "CAP-REAL-123"
    mock_get_status.assert_called_once_with("ORD-APPROVED-99")
    mock_capture.assert_called_once_with(row_id, db_path=test_db)


@patch("agent_allowance.app.capture_if_approved")
@patch("agent_allowance.app.get_order_status")
def test_payment_return_unknown_token_is_ignored(mock_get_status, mock_capture, test_db):
    """Unknown order tokens must be ignored; never call PayPal or capture."""
    params = {"paypal": "return", "token": "ORD-UNKNOWN-DOES-NOT-EXIST"}
    res = process_payment_return(params, db_path=test_db)

    assert res["status"] == "ignored"
    assert res["reason"] == "unknown_token"
    mock_get_status.assert_not_called()
    mock_capture.assert_not_called()


@patch("agent_allowance.app.capture_if_approved")
@patch("agent_allowance.app.get_order_status")
def test_payment_return_order_not_yet_approved(mock_get_status, mock_capture, test_db):
    """When PayPal indicates buyer has not approved yet, do not capture and leave APPROVED."""
    row_id = _insert_tx(test_db, "p-pending", "APPROVED", 20.00, paypal_order_id="ORD-NOT-READY")

    mock_get_status.return_value = {"id": "ORD-NOT-READY", "status": "CREATED"}

    params = {"paypal": "return", "token": "ORD-NOT-READY"}
    res = process_payment_return(params, db_path=test_db)

    assert res["status"] == "not_approved"
    assert res["paypal_status"] == "CREATED"
    mock_capture.assert_not_called()

    # DB remains APPROVED
    tx = get_transaction_by_order_id("ORD-NOT-READY", db_path=test_db)
    assert tx["status"] == "APPROVED"


def test_payment_return_cancel_leaves_ledger_unchanged(test_db):
    """?paypal=cancel displays neutral message and leaves ledger unchanged."""
    row_id = _insert_tx(test_db, "p-cancel", "APPROVED", 15.00, paypal_order_id="ORD-CANCELLED")

    params = {"paypal": "cancel", "token": "ORD-CANCELLED"}
    res = process_payment_return(params, db_path=test_db)

    assert res["status"] == "cancelled"
    assert "cancelled" in res["message"].lower()

    # Ledger row is still APPROVED and unchanged
    tx = get_transaction_by_order_id("ORD-CANCELLED", db_path=test_db)
    assert tx["status"] == "APPROVED"


@patch("agent_allowance.agent.capture_order")
def test_double_capture_attempt_is_idempotent(mock_capture_order, test_db):
    """A captured order (EXECUTED) cannot be captured twice; must be idempotent."""
    row_id = _insert_tx(
        test_db,
        "p-executed",
        "EXECUTED",
        40.00,
        paypal_order_id="ORD-ALREADY-CAPTURED",
        paypal_capture_id="CAP-EXISTING-777",
    )

    # 1. Direct call to capture_if_approved on EXECUTED row
    capture_res = capture_if_approved(row_id, db_path=test_db)
    assert capture_res["status"] == "COMPLETED"
    assert capture_res["paypal_capture_id"] == "CAP-EXISTING-777"
    assert capture_res.get("already_captured") is True
    # PayPal capture API was never invoked!
    mock_capture_order.assert_not_called()

    # 2. Return handler on EXECUTED row
    params = {"paypal": "return", "token": "ORD-ALREADY-CAPTURED"}
    ret_res = process_payment_return(params, db_path=test_db)
    assert ret_res["status"] == "COMPLETED"
    assert ret_res["capture_id"] == "CAP-EXISTING-777"
    assert ret_res.get("already_captured") is True
    mock_capture_order.assert_not_called()


@patch("agent_allowance.app.capture_if_approved")
@patch("agent_allowance.app.get_order_status")
def test_payment_return_blocked_or_needs_approval_refuses_capture(mock_get_status, mock_capture, test_db):
    """A token that belongs to a BLOCKED or NEEDS_APPROVAL row must NEVER capture."""
    _insert_tx(test_db, "p-blocked", "BLOCKED", 500.00, paypal_order_id="ORD-BLOCKED")
    _insert_tx(test_db, "p-needs", "NEEDS_APPROVAL", 75.00, paypal_order_id="ORD-NEEDS-APPR")

    # 1. BLOCKED token
    res_blocked = process_payment_return({"paypal": "return", "token": "ORD-BLOCKED"}, db_path=test_db)
    assert res_blocked["status"] == "forbidden"
    assert res_blocked["ledger_status"] == "BLOCKED"
    mock_capture.assert_not_called()
    mock_get_status.assert_not_called()

    # 2. NEEDS_APPROVAL token
    res_needs = process_payment_return({"paypal": "return", "token": "ORD-NEEDS-APPR"}, db_path=test_db)
    assert res_needs["status"] == "forbidden"
    assert res_needs["ledger_status"] == "NEEDS_APPROVAL"
    mock_capture.assert_not_called()
    mock_get_status.assert_not_called()

    # 3. Direct capture_if_approved on unapproved row raises ValueError
    row_blocked = get_transaction_by_order_id("ORD-BLOCKED", db_path=test_db)
    with pytest.raises(ValueError) as exc:
        capture_if_approved(row_blocked["id"], db_path=test_db)
    assert "expected 'APPROVED'" in str(exc.value)


# ==============================================================================
# 3. Monthly Budget & Metrics Count APPROVED and EXECUTED
# ==============================================================================

def test_monthly_budget_counts_approved_and_executed(test_db):
    """Verify policy engine, db metrics, and app totals count both APPROVED and EXECUTED."""
    # Insert 1 APPROVED ($20.00) and 1 EXECUTED ($30.00) in current UTC month
    _insert_tx(test_db, "p-b1", "APPROVED", 20.00, "ORD-1")
    _insert_tx(test_db, "p-b2", "EXECUTED", 30.00, "ORD-2", "CAP-2")
    _insert_tx(test_db, "p-b3", "BLOCKED", 100.00, "ORD-3")
    _insert_tx(test_db, "p-b4", "NEEDS_APPROVAL", 50.00, "ORD-4")

    # Policy engine monthly spend
    spend = get_monthly_approved_spend(db_path=test_db)
    assert spend == Decimal("50.00")

    # Dashboard metrics
    metrics = get_dashboard_metrics(db_path=test_db)
    assert metrics["approved_spend"] == Decimal("50.00")
    assert metrics["budget_remaining"] == Decimal("150.00")  # 200 - 50

    # App _approved_total
    rows = [
        {"amount": "20.00", "status": "APPROVED"},
        {"amount": "30.00", "status": "EXECUTED"},
        {"amount": "100.00", "status": "BLOCKED"},
    ]
    assert _approved_total(rows) == Decimal("50.00")
