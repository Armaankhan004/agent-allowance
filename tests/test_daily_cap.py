"""
Tests for the global daily LLM cap (shared by all visitors, stored in SQLite).
All Gemini calls are mocked: zero real network calls.
"""

import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from google.genai import types

from agent_allowance.agent import ShoppingAgent
from agent_allowance.config import Config
from agent_allowance.db import init_db
from agent_allowance.demo_seed import reset_and_reseed
from agent_allowance.llm_quota import (
    daily_remaining,
    daily_used,
    make_request_gate,
    try_claim_request,
    utc_day,
)


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_daily_cap.db")
    init_db(db_file)
    return db_file


def _fc_response(name, args):
    fc = types.FunctionCall(name=name, args=args)
    cand = types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))
    return types.GenerateContentResponse(candidates=[cand])


def _text_response(text):
    cand = types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]))
    return types.GenerateContentResponse(candidates=[cand])


# ==============================================================================
# Counter
# ==============================================================================

def test_claims_stop_at_cap(test_db):
    assert [try_claim_request(3, db_path=test_db) for _ in range(5)] == [True, True, True, False, False]
    assert daily_used(test_db) == 3
    assert daily_remaining(3, test_db) == 0


def test_zero_cap_never_claims(test_db):
    assert try_claim_request(0, db_path=test_db) is False
    assert daily_used(test_db) == 0


def test_cap_resets_at_midnight_utc(test_db):
    before = datetime(2026, 11, 6, 23, 59, 30, tzinfo=timezone.utc)
    after = datetime(2026, 11, 7, 0, 0, 5, tzinfo=timezone.utc)
    assert try_claim_request(1, db_path=test_db, now=before) is True
    assert try_claim_request(1, db_path=test_db, now=before) is False
    assert daily_remaining(1, test_db, now=after) == 1
    assert try_claim_request(1, db_path=test_db, now=after) is True


def test_utc_day_converts_from_other_timezones():
    from datetime import timedelta
    tz = timezone(timedelta(hours=-8))
    # 20:00 on Nov 6 in UTC-8 is 04:00 on Nov 7 in UTC.
    assert utc_day(datetime(2026, 11, 6, 20, 0, tzinfo=tz)) == "2026-11-07"


def test_concurrent_claims_never_exceed_cap(test_db):
    results = []
    lock = threading.Lock()

    def worker():
        ok = try_claim_request(5, db_path=test_db)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 5
    assert daily_used(test_db) == 5


def test_reset_demo_data_does_not_reset_the_daily_cap(test_db):
    try_claim_request(10, db_path=test_db)
    try_claim_request(10, db_path=test_db)
    reset_and_reseed(test_db)
    assert daily_used(test_db) == 2


# ==============================================================================
# Agent integration (mocked Gemini)
# ==============================================================================

@patch("agent_allowance.agent.genai.Client")
def test_agent_stops_when_shared_daily_cap_is_spent(mock_client_cls, test_db):
    client = mock_client_cls.return_value
    client.models.generate_content.return_value = _fc_response("search_products", {"query": "paper"})

    agent = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db)
    result = agent.run("Need paper", llm_request_gate=make_request_gate(2, db_path=test_db))

    assert client.models.generate_content.call_count == 2
    assert result["llm_requests"] == 2
    assert result["error_kind"] == "daily_cap"
    assert "Demo mode" in result["error_message"]
    assert "midnight UTC" in result["error_message"]
    assert daily_used(test_db) == 2


@patch("agent_allowance.agent.genai.Client")
def test_daily_cap_is_shared_across_sessions(mock_client_cls, test_db):
    client = mock_client_cls.return_value
    client.models.generate_content.return_value = _text_response("Done.")
    gate = make_request_gate(3, db_path=test_db)

    # Three "visitors", each with their own agent and a fresh session budget.
    for _ in range(3):
        res = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db).run(
            "hello", max_llm_requests=10, llm_request_gate=gate
        )
        assert res["llm_requests"] == 1

    client.models.generate_content.reset_mock()
    fourth = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db).run(
        "hello", max_llm_requests=10, llm_request_gate=gate
    )
    client.models.generate_content.assert_not_called()
    assert fourth["error_kind"] == "daily_cap"
    assert fourth["llm_requests"] == 0


@patch("agent_allowance.agent.genai.Client")
def test_daily_cap_hit_after_proposal_keeps_the_decision(mock_client_cls, test_db):
    client = mock_client_cls.return_value
    client.models.generate_content.side_effect = [
        _fc_response("propose_purchase", {
            "merchant": "Golden Grain Mills", "amount": 28.50, "currency": "USD",
            "category": "Baking Ingredients", "reason": "Flour",
        }),
    ]
    agent = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db)
    result = agent.run("flour", llm_request_gate=make_request_gate(1, db_path=test_db))

    assert result["error"] is None
    assert result["error_kind"] == "daily_cap"
    assert result["proposal_result"]["status"] == "NEEDS_APPROVAL"


@patch("agent_allowance.agent.genai.Client")
def test_demo_mode_is_never_capped(mock_client_cls, test_db):
    # Spend the whole daily cap first.
    while try_claim_request(2, db_path=test_db):
        pass

    def exploding_gate():
        raise AssertionError("Demo mode must not consult the daily cap")

    agent = ShoppingAgent(provider="mock", db_path=test_db)
    for _ in range(5):
        result = agent.run("we're low on flour", llm_request_gate=exploding_gate)
        assert result["llm_requests"] == 0
        assert result["proposal_result"]["status"] == "NEEDS_APPROVAL"
    mock_client_cls.assert_not_called()
    assert daily_used(test_db) == 2


def test_app_forces_demo_mode_when_daily_cap_is_spent(monkeypatch, test_db):
    """Sidebar control pins every session to Demo mode and explains why."""
    from agent_allowance import app as app_module

    monkeypatch.setattr(Config, "LLM_API_KEY", "k")
    monkeypatch.setattr(Config, "LLM_DAILY_CAP", 1)
    monkeypatch.setattr(app_module, "global_daily_remaining", lambda: 0)

    state = {"aa_mode": "gemini"}
    rendered = []

    class FakeSidebar:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(app_module.st, "session_state", state)
    monkeypatch.setattr(app_module.st, "sidebar", FakeSidebar())
    monkeypatch.setattr(app_module.st, "radio", lambda *a, **k: None)
    monkeypatch.setattr(app_module.st, "caption", lambda *a, **k: None)
    monkeypatch.setattr(app_module.st, "divider", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "render_html", lambda html, **k: rendered.append(html))

    active, note = app_module.render_sidebar_mode_control()
    assert active == "mock"
    assert state["aa_mode"] == "mock"
    assert note == "Daily Gemini limit reached"
    assert any("midnight UTC" in h for h in rendered)
