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

## 🌐 Live demo

**URL:** `https://<your-service>.onrender.com` *(placeholder: replace with the Render URL)*

### What is real and what is simulated

- **The policy engine is real.** Every proposal goes through the same deterministic rules you see on the Rules page.
- **The product catalog is a mock.** Merchants and products are fictional; nothing ships.
- **PayPal is sandbox only. No real money moves.** The app refuses to call PayPal unless `PAYPAL_MODE=sandbox`, and it only talks to `api-m.sandbox.paypal.com`.
- **The agent runs in Demo mode or Gemini.** Demo mode is a scripted agent that always works. Gemini runs the real model, within a per-session cap and a shared daily cap (see below). When either cap is spent, the app switches to Demo mode and says why.
- **The ledger starts with two sample rows**, both labelled *Demo data*: a BLOCKED $500 gift card prompt-injection attempt and a $28.50 flour purchase waiting in Approvals. Both come from running the proposal through the real policy engine. Neither has a PayPal order.

### Completing a sandbox payment

You need your own **PayPal sandbox buyer account**, never a real PayPal login.

1. Sign in at [developer.paypal.com](https://developer.paypal.com), open **Testing Tools → Sandbox Accounts** ([direct link](https://developer.paypal.com/dashboard/accounts)), and use (or create) a **Personal** sandbox account. Its email and password are shown in the account details there.
2. In the demo, get an approved purchase, either with a small request in Chat (for example *"buy copy paper"*) or by approving the flour row on the Approvals page.
3. Click **Open PayPal sandbox checkout** (in Chat) or **Pay with PayPal sandbox** (in Approvals). On the PayPal sandbox page, log in with the sandbox buyer account from step 1 and complete the payment.
4. PayPal sends you back to the app, which checks the order with PayPal and captures it. The row becomes **EXECUTED** with a capture ID.

Do not enter real card details or your real PayPal password on the sandbox checkout.

### First load can be slow

The demo runs on a free Render instance that spins down when idle. The first visit after a quiet period can take up to about a minute while it starts again. Later page loads are fast.

### Known limitations

- **Shared demo data.** Everyone sees the same ledger, approvals and rules. Another visitor can approve, reject or reset rows while you watch.
- **Resets on restart.** The free instance has an ephemeral disk, so the SQLite database (ledger, rule edits, and the daily Gemini counter) is wiped on every restart, redeploy and spin-down. The two sample rows come back each time.
- **Approved but unpaid orders count toward the budget.** A purchase counts against the monthly allowance as soon as it is APPROVED, even if nobody completes the sandbox checkout.
- **Gemini is capped.** Each session gets `LLM_SESSION_CAP` real requests, and all visitors together get `LLM_DAILY_CAP` per UTC day. Demo mode is never capped.

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
| `LLM_DAILY_CAP` | `30` | Max real Gemini requests per UTC day across **all** visitors, stored in SQLite. When it is spent, every session uses Demo mode and the sidebar says why; it resets at midnight UTC. Demo mode is never capped. |
| `DEMO_PUBLIC` | `false` | Hides Reset behind an extra confirmation and replaces raw exception text with a generic message. |
| `DB_PATH` | `agent_allowance.db` in the project root | SQLite file location (`DATABASE_PATH` is still accepted as the older name). The parent folder is created if missing. |
| `PAYPAL_MODE` | `sandbox` | Must be `sandbox`. Any other value disables every PayPal call and shows an error banner. Missing PayPal credentials show an info banner instead; policy decisions still work. |

- Switch modes at any time from **Agent mode** in the sidebar; the header pill shows the active mode (`Demo mode` or `Gemini live`).
- Gemini 429 / `RESOURCE_EXHAUSTED` errors show one short message (with the retry time when Gemini sends one) and a **Switch to Demo mode** button. Quota errors are not retried.
- The per-session cap lives in Streamlit session state, so refreshing the page starts a new session. The daily cap (`LLM_DAILY_CAP`) is shared and stored in the database, so it holds across refreshes. Still set a quota on the API key itself.
- When the ledger is empty at startup (first start, or after **Reset demo data**), the app adds two sample rows labelled *Demo data*: a BLOCKED gift card attack and a NEEDS_APPROVAL flour purchase, each produced by the real policy engine. Seed rows are never APPROVED or EXECUTED and never have a PayPal order. If edited rules would give either proposal a different outcome, that row is skipped.
- For a public deploy, also set `STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=none`. Auto-reload on save is off in the shipped config; pass `--server.runOnSave true` locally if you want it.

---

### 💳 Approving a PayPal Sandbox Payment & The Return Loop

Agent Allowance implements a full closed-loop payment authorization and capture workflow:

1. **Policy Approval & Order Creation**:
   - The agent proposes a purchase (e.g. via Chat or CLI).
   - The deterministic policy engine evaluates limits. If within budget, it marks the transaction **APPROVED** and creates a PayPal Orders v2 Sandbox order with `return_url` set to `${APP_BASE_URL}?paypal=return` and `cancel_url` set to `${APP_BASE_URL}?paypal=cancel`.

2. **Buyer Approval in PayPal Sandbox**:
   - Click the **"Open PayPal sandbox checkout"** button on the decision card.
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

### ☁️ Deploying on Render

The public demo runs as a Render web service that was **created manually in the Render dashboard** and auto-deploys from `main`. [`render.yaml`](render.yaml) documents its settings; the dashboard is the source of truth.

- **Build:** `pip install -r requirements.txt`
- **Start:** `streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
- **Secrets** (set in the dashboard, never committed): `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `LLM_API_KEY`.
- **Recommended:** `PAYPAL_MODE=sandbox`, `DEMO_PUBLIC=true`, `DEFAULT_MODE=mock`, `LLM_DAILY_CAP=30`, `STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=none`, and `APP_BASE_URL` set to the service URL so PayPal returns to the right place.

> ⚠️ **Free Render instances have an ephemeral disk.** Anything written to the filesystem, including the SQLite database, is lost on every restart, redeploy and spin-down. That resets the ledger, rule edits and the daily Gemini counter. To keep data, attach a persistent disk (paid plans) and point `DB_PATH` at it, for example `DB_PATH=/var/data/agent_allowance.db`.

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
│   ├── paypal_guard.py     # Sandbox-only gate in front of every PayPal call
│   ├── llm_quota.py        # Shared daily Gemini request cap (SQLite)
│   ├── demo_seed.py        # Demo data rows for an empty ledger
│   ├── agent.py            # AI shopping agent (proposal generator)
│   └── catalog.py          # Product catalog and inventory
├── tests/
│   ├── __init__.py
│   └── test_db.py          # Tests for DB schema and configuration
├── .env.example            # Template for environment configuration
├── render.yaml             # Documents the (manually created) Render service
├── .gitignore              # Git ignore rules (protects .env and database)
├── LICENSE                 # MIT License
├── README.md               # Project documentation
└── requirements.txt        # Python package dependencies
```

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).
