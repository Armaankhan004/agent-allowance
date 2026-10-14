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
        VALUES ('prop-approved-1', 'Corner Office Supply', 8.49, 'USD', 'Office Supplies', 'Copy paper', 'APPROVED', 'ORD-READY')
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
        VALUES ('prop-pending-1', 'Beacon Mart', 35.00, 'USD', 'Supplies', 'Lamp', 'NEEDS_APPROVAL')
        """
    )
    ledger_id = cursor.lastrowid
    conn.commit()
    conn.close()

    with pytest.raises(ValueError) as exc:
        capture_if_approved(ledger_id, db_path=test_db)
    assert "expected 'APPROVED'" in str(exc.value)


# ==============================================================================
# Gemini Provider Tests (Mocked Network Calls - Zero Real Network)
# ==============================================================================


def test_gemini_missing_api_key_returns_clear_error(test_db):
    """
    If LLM_PROVIDER=gemini and the API key is missing, the agent must return a clear error message.
    It must NOT crash and must NOT silently switch to mock mode.
    """
    agent = ShoppingAgent(provider="gemini", api_key="", db_path=test_db)
    result = agent.run("Buy paper")

    assert "error" in result
    assert "missing" in result["error"].lower() or "not configured" in result["error"].lower()
    assert result["proposal_result"] is None
    assert len(result["tool_calls"]) == 0
    assert "Error:" in result["agent_response"]


@patch("agent_allowance.agent.create_order")
@patch("agent_allowance.agent.genai.Client")
def test_gemini_mocked_network_tool_loop_approved(mock_genai_client_cls, mock_create_order, test_db):
    """
    Verify Gemini provider tool-call loop:
    1. Turn 1: Model calls search_products('paper')
    2. Tool result contains sanitized catalog with untrusted data notices
    3. Turn 2: Model calls propose_purchase for Corner Office Supply
    4. Deterministic policy engine approves purchase
    5. Turn 3: Model returns final response text
    6. Ensures zero real network calls.
    """
    from google.genai import types

    mock_create_order.return_value = PayPalOrderResult(
        order_id="SANDBOX-GEMINI-001",
        approval_url="https://www.sandbox.paypal.com/checkoutnow?token=SANDBOX-GEMINI-001",
    )

    # Turn 1: search_products call
    fc_search = types.FunctionCall(name="search_products", args={"query": "paper"})
    cand1 = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc_search)]))
    resp1 = types.GenerateContentResponse(candidates=[cand1])

    # Turn 2: propose_purchase call
    fc_propose = types.FunctionCall(
        name="propose_purchase",
        args={
            "merchant": "Corner Office Supply",
            "amount": 8.49,
            "currency": "USD",
            "category": "Office Supplies",
            "reason": "Replenishing copy paper",
        },
    )
    cand2 = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc_propose)]))
    resp2 = types.GenerateContentResponse(candidates=[cand2])

    # Turn 3: final text response
    part_text = types.Part.from_text(text="I have proposed the purchase of copy paper and it was auto-approved.")
    cand3 = types.Candidate(content=types.Content(role="model", parts=[part_text]))
    resp3 = types.GenerateContentResponse(candidates=[cand3])

    # Configure client mock
    mock_client_instance = mock_genai_client_cls.return_value
    mock_client_instance.models.generate_content.side_effect = [resp1, resp2, resp3]

    tools_called = []
    policies_evaluated = []

    def on_tool(name, args):
        tools_called.append((name, args))

    def on_policy(decision):
        policies_evaluated.append(decision)

    agent = ShoppingAgent(provider="gemini", api_key="test-gemini-key", db_path=test_db)
    result = agent.run(
        "Please order copy paper",
        on_tool_call=on_tool,
        on_policy_decision=on_policy,
    )

    # Verify tool calls executed
    assert len(result["tool_calls"]) == 2
    assert result["tool_calls"][0]["tool"] == "search_products"
    assert result["tool_calls"][1]["tool"] == "propose_purchase"
    assert len(tools_called) == 2
    assert len(policies_evaluated) == 1

    # Verify policy evaluation result
    assert result["proposal_result"]["status"] == "APPROVED"
    assert result["proposal_result"]["paypal_order_id"] == "SANDBOX-GEMINI-001"
    assert "auto-approved" in result["agent_response"]

    # Verify that contents passed to generate_content contained untrusted catalog label
    call_args_list = mock_client_instance.models.generate_content.call_args_list
    assert len(call_args_list) == 3
    # Turn 2 input contents should contain function response with untrusted warning
    turn2_contents = call_args_list[1].kwargs["contents"]
    turn2_tool_response = str(turn2_contents)
    assert "UNTRUSTED" in turn2_tool_response


@patch("agent_allowance.agent.genai.Client")
def test_gemini_api_error_returns_clear_error_and_never_leaks_key(mock_genai_client_cls, test_db):
    """
    If the Gemini API call fails, return a clear error message, never crash, never silently fall back
    to mock mode, and never leak the API key in the response or error message.
    """
    secret_key = "AIzaSySuperSecretKey123456789"
    mock_client_instance = mock_genai_client_cls.return_value
    mock_client_instance.models.generate_content.side_effect = Exception(
        f"API Error 403 Forbidden with auth token {secret_key}"
    )

    agent = ShoppingAgent(provider="gemini", api_key=secret_key, db_path=test_db)
    result = agent.run("Order coffee")

    assert "error" in result
    assert "Gemini API request failed" in result["error"]
    assert secret_key not in result["error"]
    assert secret_key not in result["agent_response"]
    assert "[REDACTED_API_KEY]" in result["error"]
    # Not silent mock mode
    assert result["proposal_result"] is None


@patch("agent_allowance.agent.genai.Client")
def test_gemini_max_iteration_limit(mock_genai_client_cls, test_db):
    """
    Verify the tool loop enforces a max-iteration limit (6) so the agent cannot loop indefinitely.
    """
    from google.genai import types

    fc = types.FunctionCall(name="search_products", args={"query": "paper"})
    cand = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))
    loop_response = types.GenerateContentResponse(candidates=[cand])

    mock_client_instance = mock_genai_client_cls.return_value
    # Infinite loop response simulation
    mock_client_instance.models.generate_content.return_value = loop_response

    agent = ShoppingAgent(provider="gemini", api_key="dummy-key", db_path=test_db)
    result = agent.run("Need infinite paper")

    # Should hit exactly 6 iterations and exit gracefully
    assert mock_client_instance.models.generate_content.call_count == 6
    assert len(result["tool_calls"]) == 6
    assert "Completed 6 iterations" in result["thought"]

