#!/usr/bin/env python3
"""
Test script for PayPal Sandbox Order creation & capture.
Creates a $5.00 USD order, outputs the buyer approval link, and optionally captures it.
"""

import sys
from agent_allowance.config import Config
from agent_allowance.paypal_client import (
    create_order,
    capture_order,
    PayPalConfigError,
    PayPalAPIError,
)


def main():
    print("=" * 65)
    print("🛡️  Agent Allowance — PayPal Sandbox Order Verification")
    print("=" * 65)

    # 1. Check configuration
    if not Config.validate_paypal_config():
        print("\n❌ Error: Missing PayPal Sandbox Credentials in .env")
        print("\nTo fix this:")
        print("  1. Copy .env.example to .env:")
        print("     cp .env.example .env")
        print("  2. Open developer.paypal.com -> Dashboard -> Apps & Credentials")
        print("  3. Create or select a Sandbox REST API App")
        print("  4. Copy the Client ID & Secret into your .env file:")
        print("     PAYPAL_CLIENT_ID=...")
        print("     PAYPAL_CLIENT_SECRET=...")
        print("=" * 65)
        sys.exit(1)

    print("\n✅ Sandbox credentials detected.")
    print(f"   Mode: {Config.PAYPAL_MODE.upper()}")
    print(f"   Endpoint: {Config.PAYPAL_API_BASE_URL}")

    # 2. Create a $5.00 Sandbox Order
    print("\n🚀 Creating a $5.00 USD test order via PayPal Orders v2 API...")
    try:
        order = create_order(
            amount=5.00,
            currency="USD",
            description="Agent Allowance Sandbox Verification Purchase",
        )
    except PayPalConfigError as e:
        print(f"\n❌ Configuration Error: {e}")
        sys.exit(1)
    except PayPalAPIError as e:
        print(f"\n❌ PayPal API Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        sys.exit(1)

    print("\n🎉 Order successfully created in PayPal Sandbox!")
    print(f"   Order ID:     {order.order_id}")
    print(f"   Approval URL: {order.approval_url}")

    # 3. Step-by-step buyer approval instructions
    print("\n" + "-" * 65)
    print("📋 HOW TO APPROVE THIS IN PAYPAL SANDBOX:")
    print("  1. Copy and open the Approval URL above in your browser.")
    print("  2. Log in using your PayPal Sandbox PERSONAL buyer account:")
    print("     - Find it at: https://developer.paypal.com/dashboard/accounts")
    print("     - Look for the account with Type: 'Personal'")
    print("     - View its System Generated Email and Password")
    print("  3. Click 'Complete Purchase' / 'Pay Now' in the PayPal Sandbox checkout.")
    print("-" * 65)

    # 4. Optional interactive capture
    print("\nWould you like to capture this order once approved?")
    try:
        user_input = input("Press [Enter] to capture payment, or type 'q' to quit: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")
        sys.exit(0)

    if user_input == "q":
        print("\nExiting without capture. Order ID remains in 'APPROVED' state once you approve.")
        return

    print(f"\n⚡ Attempting to capture order {order.order_id}...")
    try:
        capture_result = capture_order(order.order_id)
        print("🎉 Capture Successful!")
        print(f"   Status:       {capture_result['status']}")
        print(f"   Capture ID:   {capture_result['capture_id']}")
        print(f"   Amount:       {capture_result['amount']} {capture_result['currency']}")
    except PayPalAPIError as e:
        print(f"\n⚠️  Capture failed: {e}")
        print("   (Note: You must approve the order in the browser BEFORE capturing!)")
    except Exception as e:
        print(f"\n⚠️  Capture error: {e}")

    print("\n" + "=" * 65)


if __name__ == "__main__":
    main()
