"""
Tests for the sandbox-only PayPal guard and the friendly missing-credentials state.
All network calls are mocked; any real HTTP attempt fails the test.
"""

from unittest.mock import patch

import pytest

from agent_allowance import paypal_client, paypal_guard
from agent_allowance.agent import create_order_for_approved, propose_purchase
from agent_allowance.config import Config
from agent_allowance.db import get_db_connection, init_db
from agent_allowance.paypal_client import PayPalConfigError

SECRET = "EFakeSandboxSecretValue_9f8e7d6c5b4a"


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_guard.db")
    init_db(db_file)
    return db_file


@pytest.fixture
def no_network():
    with patch("agent_allowance.paypal_client.requests.post", side_effect=AssertionError("network")) as post, \
         patch("agent_allowance.paypal_client.requests.get", side_effect=AssertionError("network")) as get:
        yield post, get


@pytest.fixture
def live_mode(monkeypatch):
    monkeypatch.setattr(Config, "PAYPAL_MODE", "live")
    monkeypatch.setattr(Config, "PAYPAL_CLIENT_ID", "client-id")
    monkeypatch.setattr(Config, "PAYPAL_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(paypal_client, "_default_client", None)


def test_sandbox_mode_ignores_case_and_whitespace(monkeypatch):
    monkeypatch.setattr(Config, "PAYPAL_MODE", " Sandbox ")
    assert Config.paypal_mode_is_sandbox()


@pytest.mark.parametrize("mode", ["live", "production", "", "sandbox2", "sand box"])
def test_non_sandbox_modes_refuse(monkeypatch, mode, no_network):
    monkeypatch.setattr(Config, "PAYPAL_MODE", mode)
    with pytest.raises(PayPalConfigError):
        paypal_guard.create_order(5.0)
    with pytest.raises(PayPalConfigError):
        paypal_guard.capture_order("ORDER-1")
    with pytest.raises(PayPalConfigError):
        paypal_guard.get_order_status("ORDER-1")
    post, get = no_network
    post.assert_not_called()
    get.assert_not_called()


def test_sandbox_mode_passes_through(monkeypatch):
    monkeypatch.setattr(Config, "PAYPAL_MODE", "sandbox")
    with patch("agent_allowance.paypal_guard.paypal_client.create_order", return_value="ok") as inner:
        assert paypal_guard.create_order(5.0) == "ok"
        inner.assert_called_once()


def test_wrong_mode_status_and_message(live_mode):
    status = paypal_guard.paypal_status()
    assert status["status"] == paypal_guard.STATUS_WRONG_MODE
    assert "PAYPAL_MODE" in status["message"]
    assert "sandbox" in status["message"]
    assert SECRET not in status["message"]


def test_wrong_mode_message_sanitises_the_value(monkeypatch):
    monkeypatch.setattr(Config, "PAYPAL_MODE", "<script>alert(1)</script>")
    msg = paypal_guard.wrong_mode_message()
    assert "<" not in msg and ">" not in msg


def test_missing_credentials_status_is_friendly(monkeypatch):
    monkeypatch.setattr(Config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setattr(Config, "PAYPAL_CLIENT_ID", "")
    monkeypatch.setattr(Config, "PAYPAL_CLIENT_SECRET", "")
    status = paypal_guard.paypal_status()
    assert status["status"] == paypal_guard.STATUS_MISSING_CREDENTIALS
    assert "PAYPAL_CLIENT_ID" in status["message"]


def test_ok_status(monkeypatch):
    monkeypatch.setattr(Config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setattr(Config, "PAYPAL_CLIENT_ID", "id")
    monkeypatch.setattr(Config, "PAYPAL_CLIENT_SECRET", SECRET)
    assert paypal_guard.paypal_status() == {"status": "ok", "message": None}


def test_auto_approved_purchase_makes_no_paypal_call_in_live_mode(live_mode, no_network, test_db):
    res = propose_purchase("CodeHaven", 5.00, "USD", "Software", "Test", db_path=test_db)
    assert res["status"] == "APPROVED"
    assert res["paypal_order_id"] is None
    assert res["approval_url"] is None
    post, _ = no_network
    post.assert_not_called()

    conn = get_db_connection(test_db)
    audit = conn.execute(
        "SELECT result, details FROM audit_logs WHERE step = 'PAYPAL_ORDER_CREATION_FAILED'"
    ).fetchall()
    conn.close()
    assert len(audit) == 1
    assert "PAYPAL_MODE" in audit[0]["details"]
    assert SECRET not in audit[0]["details"]


def test_human_approved_order_is_refused_in_live_mode(live_mode, no_network, test_db):
    res = propose_purchase("CodeHaven", 5.00, "USD", "Software", "Test", db_path=test_db)
    with pytest.raises(PayPalConfigError):
        create_order_for_approved(res["ledger_id"], db_path=test_db)


def test_payment_return_does_not_call_paypal_in_live_mode(live_mode, no_network, test_db):
    from agent_allowance.app import process_payment_return

    res = propose_purchase("CodeHaven", 5.00, "USD", "Software", "Test", db_path=test_db)
    conn = get_db_connection(test_db)
    conn.execute("UPDATE transactions SET paypal_order_id = 'ORDER-XYZ' WHERE id = ?", (res["ledger_id"],))
    conn.commit()
    conn.close()

    out = process_payment_return({"paypal": "return", "token": "ORDER-XYZ"}, db_path=test_db)
    assert out["status"] == "error"
    assert SECRET not in out["message"]
    post, get = no_network
    post.assert_not_called()
    get.assert_not_called()


def test_missing_credentials_do_not_crash_a_purchase(monkeypatch, no_network, test_db):
    monkeypatch.setattr(Config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setattr(Config, "PAYPAL_CLIENT_ID", "")
    monkeypatch.setattr(Config, "PAYPAL_CLIENT_SECRET", "")
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(paypal_client, "_default_client", None)

    res = propose_purchase("CodeHaven", 5.00, "USD", "Software", "Test", db_path=test_db)
    assert res["status"] == "APPROVED"
    assert res["paypal_order_id"] is None
