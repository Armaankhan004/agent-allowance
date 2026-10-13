"""
Unit tests for PayPal Sandbox client using mocked responses.
Ensures Orders v2 payload formats, link parsing, error handling, and security guarantees.
"""

from unittest.mock import patch, MagicMock
import pytest
from agent_allowance.paypal_client import (
    PayPalSandboxClient,
    PayPalConfigError,
    PayPalAPIError,
    PayPalOrderResult,
)


@pytest.fixture
def mock_client():
    return PayPalSandboxClient(
        client_id="fake_sandbox_client_id",
        client_secret="fake_sandbox_client_secret",
        base_url="https://api-m.sandbox.paypal.com",
    )


def test_missing_credentials_raises_config_error():
    """Verify that empty credentials raise PayPalConfigError before network call."""
    client = PayPalSandboxClient(client_id="", client_secret="")
    with pytest.raises(PayPalConfigError) as exc_info:
        client.get_access_token()
    assert "Missing PayPal Sandbox credentials" in str(exc_info.value)


@patch("agent_allowance.paypal_client.requests.post")
def test_get_access_token_success(mock_post, mock_client):
    """Verify OAuth2 token retrieval and caching."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": "mock_access_token_12345",
        "expires_in": 3600,
    }
    mock_post.return_value = mock_resp

    token = mock_client.get_access_token()
    assert token == "mock_access_token_12345"

    # Second call should use cache without invoking requests.post again
    cached_token = mock_client.get_access_token()
    assert cached_token == "mock_access_token_12345"
    assert mock_post.call_count == 1


@patch("agent_allowance.paypal_client.requests.post")
def test_create_order_success(mock_post, mock_client):
    """Verify order creation and extraction of order_id and approval_url."""
    # Mock token response
    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {"access_token": "token_abc", "expires_in": 3600}

    # Mock create order response
    order_resp = MagicMock()
    order_resp.status_code = 201
    order_resp.json.return_value = {
        "id": "ORD-12345",
        "status": "CREATED",
        "links": [
            {
                "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/ORD-12345",
                "rel": "self",
                "method": "GET",
            },
            {
                "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORD-12345",
                "rel": "approve",
                "method": "GET",
            },
        ],
    }

    mock_post.side_effect = [token_resp, order_resp]

    result = mock_client.create_order(amount=5.00, currency="USD", description="Test Order")

    assert isinstance(result, PayPalOrderResult)
    assert result.order_id == "ORD-12345"
    assert result.approval_url == "https://www.sandbox.paypal.com/checkoutnow?token=ORD-12345"

    # Verify tuple unpacking works
    oid, aurl = result
    assert oid == "ORD-12345"
    assert aurl == "https://www.sandbox.paypal.com/checkoutnow?token=ORD-12345"


@patch("agent_allowance.paypal_client.requests.post")
def test_capture_order_success(mock_post, mock_client):
    """Verify capture of an approved order."""
    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {"access_token": "token_abc", "expires_in": 3600}

    capture_resp = MagicMock()
    capture_resp.status_code = 201
    capture_resp.json.return_value = {
        "id": "ORD-12345",
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": "CAP-998877",
                            "status": "COMPLETED",
                            "amount": {"currency_code": "USD", "value": "5.00"},
                        }
                    ]
                }
            }
        ],
    }

    mock_post.side_effect = [token_resp, capture_resp]

    result = mock_client.capture_order("ORD-12345")
    assert result["order_id"] == "ORD-12345"
    assert result["status"] == "COMPLETED"
    assert result["capture_id"] == "CAP-998877"
    assert result["amount"] == "5.00"
    assert result["currency"] == "USD"


@patch("agent_allowance.paypal_client.requests.post")
def test_api_error_handling_sanitizes_message(mock_post, mock_client):
    """Verify that API errors present human-readable messages and don't expose secrets."""
    token_resp = MagicMock()
    token_resp.status_code = 401
    token_resp.json.return_value = {
        "error": "invalid_client",
        "error_description": "Client Authentication failed",
    }
    mock_post.return_value = token_resp

    with pytest.raises(PayPalAPIError) as exc_info:
        mock_client.get_access_token()

    assert "HTTP 401" in str(exc_info.value)
    assert "Client Authentication failed" in str(exc_info.value)
    assert "fake_sandbox_client_secret" not in str(exc_info.value)
