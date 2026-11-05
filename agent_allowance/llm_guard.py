"""
LLM resilience helpers for Agent Allowance (pure Python, no Streamlit imports).

- resolve_default_mode(): pick Mock or Gemini at startup from DEFAULT_MODE and the key.
- parse_llm_error(): turn a 429 / RESOURCE_EXHAUSTED blob into one short message.
- Per-session request cap helpers that work on any dict-like state (st.session_state).
"""

import re
from typing import Any, Dict, MutableMapping, Optional

MODE_MOCK = "mock"
MODE_GEMINI = "gemini"
MODES = (MODE_MOCK, MODE_GEMINI)

_USED_KEY = "aa_llm_requests_used"

GENERIC_ERROR = "Something went wrong. Please try again, or switch to Mock mode."


# ==============================================================================
# Mode selection
# ==============================================================================

def resolve_default_mode(
    default_mode: Optional[str], api_key: Optional[str], provider: Optional[str] = None
) -> str:
    """
    Startup mode for a new session.
    No LLM key always means Mock. Otherwise DEFAULT_MODE wins; if it is unset or
    invalid, fall back to LLM_PROVIDER (gemini) and finally Mock.
    """
    if not (api_key or "").strip():
        return MODE_MOCK
    for candidate in (default_mode, provider):
        value = (candidate or "").strip().lower()
        if value in MODES:
            return value
    return MODE_MOCK


def parse_session_cap(raw: Optional[str], default: int = 10) -> int:
    """LLM_SESSION_CAP as a non-negative int; bad values fall back to the default."""
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def parse_bool(raw: Optional[str]) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


# ==============================================================================
# Per-session request cap
# ==============================================================================

def requests_used(state: MutableMapping[str, Any]) -> int:
    return int(state.get(_USED_KEY, 0) or 0)


def requests_remaining(state: MutableMapping[str, Any], cap: int) -> int:
    return max(0, cap - requests_used(state))


def record_requests(state: MutableMapping[str, Any], count: int) -> None:
    """Add real LLM requests made during a run. Mock runs record 0."""
    if count > 0:
        state[_USED_KEY] = requests_used(state) + int(count)


def effective_mode(selected: str, state: MutableMapping[str, Any], cap: int) -> str:
    """The mode a run will actually use: Gemini falls back to Mock once the cap is spent."""
    if selected == MODE_GEMINI and requests_remaining(state, cap) > 0:
        return MODE_GEMINI
    return MODE_MOCK


# ==============================================================================
# Error parsing
# ==============================================================================

_RETRY_PATTERNS = (
    re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", re.IGNORECASE),
    re.compile(r"retry in\s+(\d+(?:\.\d+)?)\s*s", re.IGNORECASE),
)


def _retry_seconds_from_details(details: Any) -> Optional[float]:
    """Read google.rpc.RetryInfo.retryDelay from a structured error payload."""
    if not isinstance(details, dict):
        return None
    items = (details.get("error") or {}).get("details") or []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and "RetryInfo" in str(item.get("@type", "")):
            match = re.match(r"(\d+(?:\.\d+)?)s", str(item.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def is_quota_error(err: Any) -> bool:
    if getattr(err, "code", None) == 429:
        return True
    text = str(err)
    return "RESOURCE_EXHAUSTED" in text or re.search(r"\b429\b", text) is not None


def _format_wait(seconds: float) -> str:
    seconds = max(1, int(round(seconds)))
    if seconds < 90:
        return f"{seconds} seconds"
    minutes = round(seconds / 60)
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def parse_llm_error(err: Any) -> Dict[str, Any]:
    """
    Classify an LLM exception. Returns {"kind", "message", "retry_after_seconds"}.
    The message is short and user-facing: it never contains the raw payload or a key.
    """
    if not is_quota_error(err):
        return {"kind": "error", "message": GENERIC_ERROR, "retry_after_seconds": None}

    text = str(err)
    retry = _retry_seconds_from_details(getattr(err, "details", None))
    if retry is None:
        for pattern in _RETRY_PATTERNS:
            match = pattern.search(text)
            if match:
                retry = float(match.group(1))
                break

    if "PerDay" in text or "per day" in text.lower():
        message = "Gemini's daily quota is used up."
    else:
        message = "Gemini is rate-limited right now."
    if retry is not None:
        message += f" Try again in about {_format_wait(retry)}, or switch to Mock mode."
    else:
        message += " Try again later, or switch to Mock mode."
    return {"kind": "quota", "message": message, "retry_after_seconds": retry}


def redact(text: str, secret: Optional[str]) -> str:
    if secret and secret in text:
        return text.replace(secret, "[REDACTED_API_KEY]")
    return text
