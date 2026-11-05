"""
Tests for LLM quota resilience: mode default, per-session cap, 429 parsing,
per-run search cache and public-demo error text.
All Gemini calls are mocked (zero real network calls).
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from google.genai import errors, types

from agent_allowance import catalog
from agent_allowance.agent import ShoppingAgent
from agent_allowance.config import Config
from agent_allowance.db import init_db
from agent_allowance.llm_guard import (
    GENERIC_ERROR,
    effective_mode,
    parse_bool,
    parse_llm_error,
    parse_session_cap,
    record_requests,
    requests_remaining,
    resolve_default_mode,
)

SECRET_KEY = "AIzaSySuperSecretKey123456789"

QUOTA_PAYLOAD = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota. Please retry in 37.5s. key=" + SECRET_KEY,
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"},
        ],
    }
}


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_resilience.db")
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
# Mode toggle default
# ==============================================================================

@pytest.mark.parametrize(
    "default_mode, api_key, provider, expected",
    [
        ("gemini", "", "gemini", "mock"),       # no key always means Mock
        ("gemini", "   ", "gemini", "mock"),
        (None, "", None, "mock"),
        ("gemini", "k", "mock", "gemini"),      # DEFAULT_MODE wins over LLM_PROVIDER
        ("mock", "k", "gemini", "mock"),
        ("GEMINI", "k", None, "gemini"),        # case-insensitive
        ("", "k", "gemini", "gemini"),          # unset falls back to LLM_PROVIDER
        ("bogus", "k", "gemini", "gemini"),     # invalid falls back too
        ("", "k", "openai", "mock"),            # unknown provider -> Mock
    ],
)
def test_resolve_default_mode(default_mode, api_key, provider, expected):
    assert resolve_default_mode(default_mode, api_key, provider) == expected


@pytest.mark.parametrize("raw, expected", [(None, 10), ("", 10), ("abc", 10), ("3", 3), (" 25 ", 25), ("-4", 0)])
def test_parse_session_cap(raw, expected):
    assert parse_session_cap(raw, default=10) == expected


@pytest.mark.parametrize("raw, expected", [("true", True), ("1", True), ("YES", True), ("false", False), (None, False), ("", False)])
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected


# ==============================================================================
# Per-session cap
# ==============================================================================

def test_session_cap_counts_down_and_forces_mock():
    state = {}
    assert requests_remaining(state, 3) == 3
    assert effective_mode("gemini", state, 3) == "gemini"

    record_requests(state, 2)
    assert requests_remaining(state, 3) == 1
    assert effective_mode("gemini", state, 3) == "gemini"

    record_requests(state, 1)
    assert requests_remaining(state, 3) == 0
    assert effective_mode("gemini", state, 3) == "mock"

    record_requests(state, 5)  # never goes negative
    assert requests_remaining(state, 3) == 0


def test_mock_mode_is_never_capped(test_db):
    state = {}
    record_requests(state, 10)
    assert effective_mode("mock", state, 10) == "mock"

    agent = ShoppingAgent(provider="mock", db_path=test_db)
    with patch("agent_allowance.agent.create_order"):
        result = agent.run("Buy copy paper", max_llm_requests=0)
    assert result["llm_requests"] == 0
    assert result["proposal_result"] is not None


def test_zero_cap_with_key_pins_session_to_mock():
    assert effective_mode("gemini", {}, 0) == "mock"


@patch("agent_allowance.agent.genai.Client")
def test_agent_stops_when_request_budget_is_spent(mock_client_cls, test_db):
    client = mock_client_cls.return_value
    client.models.generate_content.return_value = _fc_response("search_products", {"query": "paper"})

    agent = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db)
    result = agent.run("Need paper", max_llm_requests=2)

    assert client.models.generate_content.call_count == 2
    assert result["llm_requests"] == 2
    assert result["error_kind"] == "session_cap"
    assert "Mock mode" in result["error_message"]


@patch("agent_allowance.agent.genai.Client")
def test_agent_with_zero_budget_makes_no_request(mock_client_cls, test_db):
    agent = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db)
    result = agent.run("Need paper", max_llm_requests=0)
    mock_client_cls.return_value.models.generate_content.assert_not_called()
    assert result["llm_requests"] == 0
    assert result["error_kind"] == "session_cap"


@patch("agent_allowance.agent.create_order")
@patch("agent_allowance.agent.genai.Client")
def test_cap_hit_after_proposal_keeps_the_decision(mock_client_cls, mock_create_order, test_db):
    """If the budget runs out after a proposal, the decision is kept and no error is shown."""
    from agent_allowance.paypal_client import PayPalOrderResult
    mock_create_order.return_value = PayPalOrderResult(order_id="ORD-1", approval_url="https://sandbox/ORD-1")
    client = mock_client_cls.return_value
    client.models.generate_content.side_effect = [
        _fc_response("propose_purchase", {
            "merchant": "Corner Office Supply", "amount": 8.49, "currency": "USD",
            "category": "Office Supplies", "reason": "Copy paper",
        }),
    ]
    agent = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db)
    result = agent.run("Buy paper", max_llm_requests=1)

    assert result["llm_requests"] == 1
    assert result["error"] is None
    assert result["error_kind"] == "session_cap"
    assert result["proposal_result"]["status"] == "APPROVED"


# ==============================================================================
# 429 parsing
# ==============================================================================

def test_parse_structured_client_error_uses_retry_info():
    err = errors.ClientError(429, QUOTA_PAYLOAD)
    parsed = parse_llm_error(err)
    assert parsed["kind"] == "quota"
    assert parsed["retry_after_seconds"] == 37.0
    assert "37 seconds" in parsed["message"]
    assert "Mock mode" in parsed["message"]
    assert SECRET_KEY not in parsed["message"]
    assert "RESOURCE_EXHAUSTED" not in parsed["message"]
    assert "{" not in parsed["message"]


def test_parse_plain_string_with_retry_in_text():
    parsed = parse_llm_error(Exception("429 Too Many Requests. Please retry in 12.4s."))
    assert parsed["kind"] == "quota"
    assert parsed["retry_after_seconds"] == 12.4
    assert "12 seconds" in parsed["message"]


def test_parse_quota_without_retry_time():
    parsed = parse_llm_error(Exception("RESOURCE_EXHAUSTED: quota exceeded"))
    assert parsed["kind"] == "quota"
    assert parsed["retry_after_seconds"] is None
    assert "Try again later" in parsed["message"]


def test_parse_daily_quota_and_minutes():
    parsed = parse_llm_error(Exception(
        "429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel 'retryDelay': '3600s'"
    ))
    assert parsed["message"].startswith("Gemini's daily quota is used up.")
    assert "60 minutes" in parsed["message"]


def test_parse_non_quota_error_is_generic():
    parsed = parse_llm_error(Exception(f"403 PERMISSION_DENIED for key {SECRET_KEY}"))
    assert parsed["kind"] == "error"
    assert parsed["message"] == GENERIC_ERROR
    # A number that merely contains 429 is not a quota error.
    assert parse_llm_error(Exception("order 14290 failed"))["kind"] == "error"


@patch("agent_allowance.agent.time.sleep")
@patch("agent_allowance.agent.genai.Client")
def test_agent_429_is_not_retried_and_returns_friendly_message(mock_client_cls, mock_sleep, test_db):
    client = mock_client_cls.return_value
    client.models.generate_content.side_effect = errors.ClientError(429, QUOTA_PAYLOAD)

    agent = ShoppingAgent(provider="gemini", api_key=SECRET_KEY, db_path=test_db)
    result = agent.run("Buy paper")

    assert client.models.generate_content.call_count == 1
    mock_sleep.assert_not_called()
    assert result["error_kind"] == "quota"
    assert result["retry_after_seconds"] == 37.0
    assert result["llm_requests"] == 1
    for field in ("error", "error_message", "agent_response"):
        assert SECRET_KEY not in result[field]
    assert "RESOURCE_EXHAUSTED" not in result["agent_response"]
    assert "Switch" not in result["agent_response"]  # button text is UI-only
    assert "Mock mode" in result["error_message"]


@patch("agent_allowance.agent.time.sleep")
@patch("agent_allowance.agent.genai.Client")
def test_agent_503_is_retried_once_and_counts_both_requests(mock_client_cls, mock_sleep, test_db):
    client = mock_client_cls.return_value
    client.models.generate_content.side_effect = [Exception("503 UNAVAILABLE"), _text_response("Nothing to buy.")]

    agent = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db)
    result = agent.run("Hello")

    assert client.models.generate_content.call_count == 2
    assert result["llm_requests"] == 2
    assert result["agent_response"] == "Nothing to buy."


# ==============================================================================
# Per-run search cache
# ==============================================================================

@patch("agent_allowance.agent.genai.Client")
def test_identical_searches_within_a_run_are_cached(mock_client_cls, test_db):
    client = mock_client_cls.return_value
    client.models.generate_content.side_effect = [
        _fc_response("search_products", {"query": "paper"}),
        _fc_response("search_products", {"query": "  Paper "}),  # same after normalising
        _fc_response("search_products", {"query": "flour"}),
        _text_response("Done."),
    ]

    with patch("agent_allowance.agent.search_products", wraps=catalog.search_products) as spy:
        agent = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db)
        result = agent.run("Find paper")

    assert [c.args[0] for c in spy.call_args_list] == ["paper", "flour"]
    searches = [tc for tc in result["tool_calls"] if tc["tool"] == "search_products"]
    assert [tc["cached"] for tc in searches] == [False, True, False]
    assert searches[0]["results_count"] == searches[1]["results_count"]

    # The cached tool output still carries the untrusted-data notice, plus a nudge.
    third_call_contents = str(client.models.generate_content.call_args_list[2].kwargs["contents"])
    assert "Do not search again" in third_call_contents
    assert "UNTRUSTED" in third_call_contents


@patch("agent_allowance.agent.genai.Client")
def test_search_cache_does_not_leak_across_runs(mock_client_cls, test_db):
    client = mock_client_cls.return_value
    client.models.generate_content.side_effect = [
        _fc_response("search_products", {"query": "paper"}), _text_response("a"),
        _fc_response("search_products", {"query": "paper"}), _text_response("b"),
    ]
    with patch("agent_allowance.agent.search_products", wraps=catalog.search_products) as spy:
        agent = ShoppingAgent(provider="gemini", api_key="k", db_path=test_db)
        agent.run("one")
        agent.run("two")
    assert spy.call_count == 2


# ==============================================================================
# UI error text & demo safety
# ==============================================================================

def test_friendly_agent_error_never_shows_raw_quota_blob(monkeypatch):
    from agent_allowance.app import friendly_agent_error
    monkeypatch.setattr(Config, "DEMO_PUBLIC", False)
    parsed = parse_llm_error(errors.ClientError(429, QUOTA_PAYLOAD))
    result = {"error": f"Gemini API request failed: {QUOTA_PAYLOAD}", "error_kind": "quota",
              "error_message": parsed["message"]}
    assert friendly_agent_error(result) == parsed["message"]


def test_public_demo_hides_raw_exceptions(monkeypatch):
    from agent_allowance.app import friendly_agent_error, safe_error_text
    monkeypatch.setattr(Config, "DEMO_PUBLIC", True)
    raw = "Traceback (most recent call last): sqlite3.OperationalError: disk I/O error"
    assert safe_error_text(raw) == GENERIC_ERROR
    assert friendly_agent_error({"error": raw, "error_kind": "error"}) == GENERIC_ERROR

    monkeypatch.setattr(Config, "DEMO_PUBLIC", False)
    monkeypatch.setattr(Config, "LLM_API_KEY", SECRET_KEY)
    assert SECRET_KEY not in safe_error_text(f"boom {SECRET_KEY}")


def test_outcome_label_for_quota_error():
    from agent_allowance.app import get_chat_outcome_label_and_state
    assert get_chat_outcome_label_and_state({"error": "x", "error_kind": "quota"}) == ("Gemini unavailable", "error")


def test_shipped_streamlit_config_has_no_run_on_save():
    config = (Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml").read_text()
    assert "runOnSave" not in config
