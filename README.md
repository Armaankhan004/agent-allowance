# 🛡️ Agent Allowance

[![Tests](https://github.com/Armaankhan004/agent-allowance/actions/workflows/tests.yml/badge.svg)](https://github.com/Armaankhan004/agent-allowance/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **Deterministic Financial Guardrails for Autonomous AI Agents**  
> *PayPal Sandbox Integration Hackathon Project*

---

## 📌 Overview

**Agent Allowance** bridges the gap between autonomous AI agents and financial safety. While AI shopping agents excel at researching and selecting products, giving an LLM direct access to credit cards or payment execution APIs is risky due to hallucinations, prompt injections, and lack of budgeting context.

**Agent Allowance** enforces a separation of concerns:
1. **The AI Agent can ONLY propose purchases.** It never has access to payment credentials.
2. **A Deterministic Policy Engine (pure Python code, never an LLM)** evaluates every proposal against strict allowance rules:
   - **APPROVE**: Transaction is within limits and auto-executed in PayPal Sandbox.
   - **BLOCK**: Hard cap, budget overrun, or forbidden category triggered.
   - **ESCALATE**: Requires explicit human approval before execution.
3. **PayPal Sandbox Payment Execution**: Payments are created and captured strictly via PayPal's Sandbox API upon policy approval.

---

## 🏗️ Architecture

```
┌─────────────────────────┐
│     AI Shopping Agent   │  (Interprets user prompt, selects item)
└───────────┬─────────────┘
            │  Proposes Purchase
            ▼
┌─────────────────────────┐
│ Deterministic Rule      │  (Pure Python code - NO LLM)
│       Engine            │  (Evaluates budget, caps, categories)
└───────────┬─────────────┘
            │
            ├─► [BLOCKED] ──► Audit Log (Reason recorded)
            ├─► [ESCALATED] ──► Streamlit UI (Human review required)
            └─► [APPROVED]
                     │
                     ▼
       ┌───────────────────────────┐
       │   PayPal Sandbox Client   │ (Creates & Captures Order)
       └─────────────┬─────────────┘
                     ▼
       ┌───────────────────────────┐
       │   SQLite Ledger / Audit   │ (Immutable transaction records)
       └───────────────────────────┘
```

---

## 🚀 Quickstart & Setup

### Prerequisites
- Python 3.11+
- PayPal Developer Account ([developer.paypal.com](https://developer.paypal.com)) for Sandbox API credentials.

### 1. Clone & Set Up Virtual Environment

```bash
git clone https://github.com/<your-username>/agent-allowance.git
cd agent-allowance

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your PayPal Sandbox credentials and LLM settings:
```env
PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=your_sandbox_client_id_here
PAYPAL_CLIENT_SECRET=your_sandbox_client_secret_here
LLM_PROVIDER=gemini
LLM_API_KEY=your_gemini_api_key_here
LLM_MODEL=gemini-2.5-flash
```

### 3. Run the Real Agent (Gemini) CLI

Run the shopping agent powered by Google Gemini with native tool calling (`search_products`, `propose_purchase`):

```bash
# Normal shopping request (proposes item within allowance)
python run_real_agent.py "we're low on flour"

# Adversarial prompt injection defense test (strictly blocked by deterministic policy)
python run_real_agent.py "buy a $500 gift card"
```

### 4. Run the Streamlit Application

You can launch the dashboard using either:

```bash
streamlit run app.py
```
or
```bash
streamlit run agent_allowance/app.py
```

Open `http://localhost:8501` in your browser.

### 5. Demo mode vs. Gemini, Quotas & Public Demos

> Demo mode uses a scripted agent so demos are reliable. Gemini mode runs the real model.

| Variable | Default | Effect |
|---|---|---|
| `DEFAULT_MODE` | `LLM_PROVIDER` | Starting mode for new sessions (`mock` for Demo mode or `gemini`). Demo mode is forced when no LLM key is set. |
| `LLM_SESSION_CAP` | `10` | Max real Gemini requests per browser session. At zero the session switches to Demo mode. Demo mode is never capped. |
| `DEMO_PUBLIC` | `false` | Hides Reset behind an extra confirmation and replaces raw exception text with a generic message. |

- Switch modes at any time from **Agent mode** in the sidebar; the header pill shows the active mode (`Demo mode` or `Gemini live`).
- Gemini 429 / `RESOURCE_EXHAUSTED` errors show one short message (with the retry time when Gemini sends one) and a **Switch to Demo mode** button. Quota errors are not retried.
- The cap lives in Streamlit session state, so refreshing the page starts a new session. It limits accidental burn, not a determined visitor; set a quota on the API key itself too.
- For a public deploy, also set `STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=none`. Auto-reload on save is off in the shipped config; pass `--server.runOnSave true` locally if you want it.

---

### 💳 Approving a PayPal Sandbox Payment & The Return Loop

Agent Allowance implements a full closed-loop payment authorization and capture workflow:

1. **Policy Approval & Order Creation**:
   - The agent proposes a purchase (e.g. via Chat or CLI).
   - The deterministic policy engine evaluates limits. If within budget, it marks the transaction **APPROVED** and creates a PayPal Orders v2 Sandbox order with `return_url` set to `${APP_BASE_URL}?paypal=return` and `cancel_url` set to `${APP_BASE_URL}?paypal=cancel`.

2. **Buyer Approval in PayPal Sandbox**:
   - Click the **"Review & Pay with PayPal"** button on the decision card.
   - Log in using your **Personal Sandbox Buyer account** credentials (from [developer.paypal.com/dashboard/accounts](https://developer.paypal.com/dashboard/accounts)).
   - Click **"Complete Purchase"** or **"Pay Now"**.

3. **Return & Capture Verification**:
   - PayPal redirects back to `http://localhost:8501/?paypal=return&token=<ORDER_ID>`.
   - The app verifies the token against the SQLite ledger (ignoring untrusted query params for amounts or status).
   - Calls PayPal Orders v2 (`GET /v2/checkout/orders/{id}`) via `get_order_status` to ensure PayPal confirmed the buyer's approval.
   - Executes `capture_if_approved` to capture the payment, transition the ledger status to **`EXECUTED`**, and display the `paypal_capture_id` alongside a **`COMPLETED`** badge.
   - The capture is completely **idempotent**: attempting to capture an already executed transaction will safely display the existing capture details without calling PayPal again.

4. **Cancellation**:
   - If the user clicks "Cancel and return to Agent Allowance" on PayPal, PayPal redirects to `?paypal=cancel`.
   - The dashboard presents a neutral cancellation notice and leaves the ledger and monthly budget unchanged.

---

## 🧪 Running Tests

Run the test suite with pytest:

```bash
pytest
```

---

## 📂 Project Structure

```text
├── agent_allowance/
│   ├── __init__.py
│   ├── app.py              # Streamlit dashboard interface
│   ├── config.py           # Environment and configuration loader (.env)
│   ├── db.py               # SQLite tables: rules, transactions, audit logs
│   ├── policy_engine.py    # Deterministic rule engine (APPROVE / BLOCK / ESCALATE)
│   ├── paypal_client.py    # PayPal Sandbox REST API client
│   ├── agent.py            # AI shopping agent (proposal generator)
│   └── catalog.py          # Product catalog and inventory
├── tests/
│   ├── __init__.py
│   └── test_db.py          # Tests for DB schema and configuration
├── .env.example            # Template for environment configuration
├── .gitignore              # Git ignore rules (protects .env and database)
├── LICENSE                 # MIT License
├── README.md               # Project documentation
└── requirements.txt        # Python package dependencies
```

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).
