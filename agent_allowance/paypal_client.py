"""
PayPal Sandbox Client for Agent Allowance.
Interacts with the PayPal Orders v2 REST API strictly in the SANDBOX environment.
Supports creating orders (getting buyer approval URLs), querying order status, and capturing payments.
"""

import os
import time
import uuid
from typing import Dict, Any, NamedTuple, Optional
import requests
from requests.auth import HTTPBasicAuth
from .config import Config


class PayPalOrderResult(NamedTuple):
    """Structured result from order creation."""
    order_id: str
    approval_url: str


class PayPalError(Exception):
    """Base exception for PayPal client errors without exposing secrets."""
    pass


class PayPalConfigError(PayPalError):
    """Raised when required credentials or configurations are missing."""
    pass


class PayPalAPIError(PayPalError):
    """Raised when the PayPal REST API returns an error response."""
    def __init__(self, message: str, status_code: Optional[int] = None, error_details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_details = error_details or {}


class PayPalSandboxClient:
    """Client for interacting with PayPal Orders v2 in the Sandbox environment."""

    SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.client_id = (
            client_id
            if client_id is not None
            else (Config.PAYPAL_CLIENT_ID or os.getenv("PAYPAL_CLIENT_ID", ""))
        )
        self.client_secret = (
            client_secret
            if client_secret is not None
            else (Config.PAYPAL_CLIENT_SECRET or os.getenv("PAYPAL_CLIENT_SECRET", ""))
        )
        self.base_url = (
            base_url
            if base_url is not None
            else (Config.PAYPAL_API_BASE_URL or self.SANDBOX_BASE_URL)
        )

        # Token caching
        self._access_token: Optional[str] = None
        self._token_expiry_timestamp: float = 0.0

    def _validate_credentials(self) -> None:
        """Ensure client credentials exist before making API calls."""
        if not self.client_id or not self.client_secret:
            raise PayPalConfigError(
                "Missing PayPal Sandbox credentials. Please set PAYPAL_CLIENT_ID and "
                "PAYPAL_CLIENT_SECRET in your .env file."
            )

    def get_access_token(self, force_refresh: bool = False) -> str:
        """
        Obtain an OAuth 2.0 Bearer token via client_credentials grant.
        Caches the token until near expiry.
        """
        self._validate_credentials()

        # Return cached token if valid (with 60-second safety buffer)
        now = time.time()
        if not force_refresh and self._access_token and now < (self._token_expiry_timestamp - 60):
            return self._access_token

        url = f"{self.base_url}/v1/oauth2/token"
        headers = {
            "Accept": "application/json",
            "Accept-Language": "en_US",
        }
        data = {"grant_type": "client_credentials"}

        try:
            response = requests.post(
                url,
                auth=HTTPBasicAuth(self.client_id, self.client_secret),
                headers=headers,
                data=data,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise PayPalAPIError(f"Network error connecting to PayPal token endpoint: {exc}")

        if response.status_code != 200:
            err_msg = self._sanitize_error(response)
            raise PayPalAPIError(
                f"PayPal authentication failed [HTTP {response.status_code}]: {err_msg}",
                status_code=response.status_code,
            )

        token_data = response.json()
        self._access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 3600)
        self._token_expiry_timestamp = now + int(expires_in)

        if not self._access_token:
            raise PayPalAPIError("PayPal returned a 200 OK response but no access_token was present.")

        return self._access_token

    def create_order(
        self,
        amount: float,
        currency: str = "USD",
        description: str = "Agent Allowance Purchase",
    ) -> PayPalOrderResult:
        """
        Create a new PayPal order using the Orders v2 API.
        
        Args:
            amount: Amount to charge (e.g., 5.00)
            currency: Three-letter ISO-4217 currency code (default: 'USD')
            description: Narrative description for the purchase unit

        Returns:
            PayPalOrderResult: NamedTuple with (order_id, approval_url)
        """
        if amount <= 0:
            raise ValueError("Order amount must be greater than zero.")

        currency = currency.upper().strip()
        access_token = self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders"

        request_id = str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "PayPal-Request-Id": request_id,
        }

        # Format amount to two decimal places
        formatted_amount = f"{amount:.2f}"

        # Standard PayPal Orders v2 payload
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "description": description[:127],
                    "amount": {
                        "currency_code": currency,
                        "value": formatted_amount,
                    },
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                        "brand_name": "Agent Allowance",
                        "locale": "en-US",
                        "landing_page": "LOGIN",
                        "user_action": "PAY_NOW",
                        "return_url": "https://example.com/return",
                        "cancel_url": "https://example.com/cancel",
                    }
                }
            },
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
        except requests.RequestException as exc:
            raise PayPalAPIError(f"Network error creating PayPal order: {exc}")

        if response.status_code not in (200, 201):
            err_msg = self._sanitize_error(response)
            raise PayPalAPIError(
                f"Failed to create PayPal order [HTTP {response.status_code}]: {err_msg}",
                status_code=response.status_code,
            )

        data = response.json()
        order_id = data.get("id")
        if not order_id:
            raise PayPalAPIError("PayPal response missing order ID.")

        # Find the buyer approval link
        approval_url = None
        for link in data.get("links", []):
            if link.get("rel") in ("approve", "payer-action"):
                approval_url = link.get("href")
                break

        if not approval_url:
            raise PayPalAPIError(f"PayPal order created ({order_id}) but no approval URL was provided.")

        return PayPalOrderResult(order_id=order_id, approval_url=approval_url)

    def capture_order(self, order_id: str) -> Dict[str, Any]:
        """
        Capture payment for an approved order.
        
        Args:
            order_id: The PayPal order ID to capture

        Returns:
            Dict containing capture status, capture_id, amount, and currency.
        """
        if not order_id:
            raise ValueError("Order ID cannot be empty.")

        access_token = self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{order_id}/capture"

        request_id = str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "PayPal-Request-Id": request_id,
        }

        try:
            response = requests.post(url, headers=headers, json={}, timeout=15)
        except requests.RequestException as exc:
            raise PayPalAPIError(f"Network error capturing PayPal order: {exc}")

        if response.status_code not in (200, 201):
            err_msg = self._sanitize_error(response)
            raise PayPalAPIError(
                f"Failed to capture PayPal order {order_id} [HTTP {response.status_code}]: {err_msg}",
                status_code=response.status_code,
            )

        data = response.json()
        status = data.get("status")

        # Extract capture ID from purchase_units
        capture_id = None
        captured_amount = None
        captured_currency = None

        purchase_units = data.get("purchase_units", [])
        if purchase_units:
            payments = purchase_units[0].get("payments", {})
            captures = payments.get("captures", [])
            if captures:
                first_capture = captures[0]
                capture_id = first_capture.get("id")
                amount_info = first_capture.get("amount", {})
                captured_amount = amount_info.get("value")
                captured_currency = amount_info.get("currency_code")

        return {
            "order_id": order_id,
            "status": status,
            "capture_id": capture_id,
            "amount": captured_amount,
            "currency": captured_currency,
            "raw_response": data,
        }

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Retrieve the current details and status of an order."""
        if not order_id:
            raise ValueError("Order ID cannot be empty.")

        access_token = self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{order_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

        try:
            response = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise PayPalAPIError(f"Network error fetching PayPal order: {exc}")

        if response.status_code != 200:
            err_msg = self._sanitize_error(response)
            raise PayPalAPIError(
                f"Failed to fetch PayPal order {order_id} [HTTP {response.status_code}]: {err_msg}",
                status_code=response.status_code,
            )

        return response.json()

    @staticmethod
    def _sanitize_error(response: requests.Response) -> str:
        """Extract readable error message without leaking headers or secrets."""
        try:
            body = response.json()
            # PayPal typically returns 'message', 'error_description', or 'details'
            message = body.get("message") or body.get("error_description") or body.get("error")
            details = body.get("details")
            if details and isinstance(details, list):
                detail_msgs = [d.get("description", str(d)) for d in details if isinstance(d, dict)]
                if detail_msgs:
                    return f"{message or 'API Error'}: {'; '.join(detail_msgs)}"
            if message:
                return str(message)
            return response.text[:200]
        except Exception:
            return response.text[:200] if response.text else "Unknown error"


# ==============================================================================
# Functional module-level interface
# ==============================================================================

_default_client: Optional[PayPalSandboxClient] = None


def get_paypal_client() -> PayPalSandboxClient:
    """Get or create singleton instance of PayPalSandboxClient."""
    global _default_client
    if _default_client is None:
        _default_client = PayPalSandboxClient()
    return _default_client


def create_order(
    amount: float,
    currency: str = "USD",
    description: str = "Agent Allowance Purchase",
) -> PayPalOrderResult:
    """
    Convenience function to create a sandbox order and return (order_id, approval_url).
    """
    client = get_paypal_client()
    return client.create_order(amount=amount, currency=currency, description=description)


def capture_order(order_id: str) -> Dict[str, Any]:
    """
    Convenience function to capture an approved sandbox order.
    """
    client = get_paypal_client()
    return client.capture_order(order_id=order_id)
