"""
PayPal safety gate for Agent Allowance.

Wraps the module-level PayPal client functions so that no PayPal call is made
unless PAYPAL_MODE is exactly "sandbox". The PayPal client itself is unchanged.

Messages here are user-facing and never include credentials.
"""

from typing import Any, Dict, Optional

from . import paypal_client
from .config import Config
from .paypal_client import PayPalConfigError, PayPalOrderResult

STATUS_OK = "ok"
STATUS_WRONG_MODE = "wrong_mode"
STATUS_MISSING_CREDENTIALS = "missing_credentials"


def wrong_mode_message() -> str:
    # Show only a short, sanitized version of the configured value.
    shown = "".join(ch for ch in Config.PAYPAL_MODE if ch.isalnum() or ch in "-_")[:20] or "(empty)"
    return (
        f"PayPal is disabled: PAYPAL_MODE is '{shown}', but this app only talks to the PayPal sandbox. "
        "Set PAYPAL_MODE=sandbox to enable sandbox payments."
    )


MISSING_CREDENTIALS_MESSAGE = (
    "PayPal sandbox credentials are not configured, so purchases are evaluated by the "
    "policy engine but no sandbox checkout is created. Set PAYPAL_CLIENT_ID and "
    "PAYPAL_CLIENT_SECRET to enable sandbox payments."
)


def paypal_status() -> Dict[str, Optional[str]]:
    """{"status": ok | wrong_mode | missing_credentials, "message": user-facing text or None}."""
    if not Config.paypal_mode_is_sandbox():
        return {"status": STATUS_WRONG_MODE, "message": wrong_mode_message()}
    if not Config.validate_paypal_config():
        return {"status": STATUS_MISSING_CREDENTIALS, "message": MISSING_CREDENTIALS_MESSAGE}
    return {"status": STATUS_OK, "message": None}


def ensure_sandbox() -> None:
    """Raise before any network call when PAYPAL_MODE is not sandbox."""
    if not Config.paypal_mode_is_sandbox():
        raise PayPalConfigError(wrong_mode_message())


def create_order(
    amount: float,
    currency: str = "USD",
    description: str = "Agent Allowance Purchase",
    return_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
) -> PayPalOrderResult:
    ensure_sandbox()
    return paypal_client.create_order(
        amount=amount,
        currency=currency,
        description=description,
        return_url=return_url,
        cancel_url=cancel_url,
    )


def capture_order(order_id: str) -> Dict[str, Any]:
    ensure_sandbox()
    return paypal_client.capture_order(order_id)


def get_order_status(order_id: str) -> Dict[str, Any]:
    ensure_sandbox()
    return paypal_client.get_order_status(order_id)
