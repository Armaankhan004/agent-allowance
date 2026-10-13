# 🛡️ Agent Allowance

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

Edit `.env` with your PayPal Sandbox credentials:
```env
PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=your_sandbox_client_id_here
PAYPAL_CLIENT_SECRET=your_sandbox_client_secret_here
LLM_PROVIDER=mock
```

### 3. Run the Streamlit Application

You can launch the dashboard using either:

```bash
streamlit run app.py
```
or
```bash
streamlit run agent_allowance/app.py
```

Open `http://localhost:8501` in your browser.

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
