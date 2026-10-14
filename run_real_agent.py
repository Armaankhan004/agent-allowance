#!/usr/bin/env python3
"""
CLI Runner for Agent Allowance using Real LLM (Gemini).

Demonstrates:
  1. Native tool calling with Gemini (search_products, propose_purchase).
  2. Untrusted catalog text safety boundaries.
  3. Real-time streaming output of every tool call and policy decision as it happens.
  4. Safe credential handling (never exposes API keys).
"""

import sys
import os
from typing import Dict, Any

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from agent_allowance.config import Config
from agent_allowance.db import init_db
from agent_allowance.agent import ShoppingAgent


def print_banner(title: str):
    print("\n" + "=" * 70)
    print(f"🛡️  {title}")
    print("=" * 70)


def print_tool_call(tool_name: str, args: Dict[str, Any]):
    print("\n" + "─" * 70)
    print(f"🔧 [TOOL CALL] {tool_name}")
    for k, v in args.items():
        if isinstance(v, (int, float)):
            print(f"   • {k}: {v}")
        else:
            # Wrap long strings neatly
            val_str = str(v)
            if len(val_str) > 100:
                val_str = val_str[:97] + "..."
            print(f"   • {k}: {val_str}")
    print("─" * 70)


def print_policy_decision(decision: Dict[str, Any]):
    status = decision.get("status", "UNKNOWN")
    ledger_id = decision.get("ledger_id")
    reasons = decision.get("reasons", [])
    order_id = decision.get("paypal_order_id")
    approval_url = decision.get("approval_url")

    icon = "✅" if status == "APPROVED" else ("⚠️" if status == "NEEDS_APPROVAL" else "🛑")

    print("\n" + "╔" + "═" * 68 + "╗")
    print(f"║ ⚖️  [POLICY DECISION] Status: {status} {icon}".ljust(69) + "║")
    print("╠" + "═" * 68 + "╣")
    if ledger_id:
        print(f"║   Ledger Record ID: {ledger_id}".ljust(69) + "║")
    print("║   Evaluation Reasons:".ljust(69) + "║")
    for r in reasons:
        print(f"║     • {r}".ljust(69) + "║")

    if status == "APPROVED" and order_id:
        print("║".ljust(69) + "║")
        print(f"║   💳 PayPal Sandbox Order: {order_id}".ljust(69) + "║")
        if approval_url:
            print(f"║   🔗 Approval URL: {approval_url[:48]}...".ljust(69) + "║")
    elif status == "NEEDS_APPROVAL":
        print("║".ljust(69) + "║")
        print("║   👤 Escalated: Awaiting human supervisor approval in UI/CLI.".ljust(69) + "║")
    elif status == "BLOCKED":
        print("║".ljust(69) + "║")
        print("║   🛡️  Blocked by Deterministic Rule Engine before payment execution.".ljust(69) + "║")
    print("╚" + "═" * 68 + "╝")


def main():
    init_db()

    print_banner("Agent Allowance — Real LLM (Gemini) Shopping Agent")

    # Determine user request
    if len(sys.argv) > 1:
        user_request = " ".join(sys.argv[1:]).strip()
    else:
        # Check if running interactively
        default_prompt = "we're low on flour"
        if sys.stdin.isatty():
            try:
                user_input = input(
                    f"\nEnter shopping request [Press Enter for default: '{default_prompt}']: "
                ).strip()
                user_request = user_input if user_input else default_prompt
            except (KeyboardInterrupt, EOFError):
                print("\nExiting.")
                sys.exit(0)
        else:
            user_request = default_prompt

    # Verify API key without ever printing it
    api_key = Config.LLM_API_KEY or os.getenv("GEMINI_API_KEY", "")
    has_key = bool(api_key and api_key != "your_api_key_here" and api_key != "your_gemini_api_key_here")

    model_name = (
        Config.LLM_MODEL
        if Config.LLM_MODEL and Config.LLM_MODEL != "mock-agent"
        else "gemini-2.5-flash"
    )

    print(f"\n📋 User Request: \"{user_request}\"")
    print(f"🤖 LLM Provider: gemini")
    print(f"🧠 Model:        {model_name}")
    print(f"🔑 API Key:      {'[CONFIGURED - REDACTED]' if has_key else '[MISSING ⚠️]'}")

    if not has_key:
        print("\n❌ Warning: No valid Gemini API key found in .env (LLM_API_KEY or GEMINI_API_KEY).")
        print("   The agent will fall back to a clear error message as required by policy.")
        print("   To run with live Gemini:")
        print("     1. Add your key to .env: LLM_API_KEY=AIzaSy...")
        print("     2. Or run: LLM_API_KEY=AIzaSy... python run_real_agent.py \"...\"\n")

    agent = ShoppingAgent(
        provider="gemini",
        api_key=api_key if has_key else "",
        model=model_name,
    )

    print("\n⏳ Executing agent loop with native tool calling...")
    print("   (Live tool calls and policy evaluations will stream below)")

    result = agent.run(
        user_request=user_request,
        on_tool_call=print_tool_call,
        on_policy_decision=print_policy_decision,
    )

    # Print summary
    print("\n" + "=" * 70)
    print("🤖 [FINAL AGENT RESPONSE]")
    print("=" * 70)
    if "error" in result and result["error"]:
        print(f"⚠️  {result['error']}")
    else:
        print(result.get("agent_response", "No response text generated."))

    print("\n📊 Run Statistics:")
    print(f"   • Tool Calls Executed: {len(result.get('tool_calls', []))}")
    if result.get("proposal_result"):
        prop = result["proposal_result"]
        print(f"   • Policy Decision:     {prop.get('status')}")
        print(f"   • Ledger ID:           {prop.get('ledger_id')}")
        if prop.get("paypal_order_id"):
            print(f"   • PayPal Order ID:     {prop.get('paypal_order_id')}")
    else:
        print("   • Policy Decision:     None (no purchase proposed)")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
