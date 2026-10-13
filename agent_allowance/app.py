"""
Streamlit Web Application for Agent Allowance.
Interactive dashboard for setting allowance policies, simulating agent purchase proposals,
inspecting the deterministic policy engine decisions, and viewing the PayPal Sandbox audit ledger.
"""

import sys
from pathlib import Path
import streamlit as st

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from agent_allowance.config import Config
from agent_allowance.db import init_db, get_db_connection

# Page setup
st.set_page_config(
    page_title="Agent Allowance | PayPal Sandbox",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom minimal CSS for polish
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.3rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #6c757d;
        margin-bottom: 1.5rem;
    }
    .status-badge {
        display: inline-block;
        padding: 0.35rem 0.75rem;
        border-radius: 9999px;
        font-weight: 600;
        font-size: 0.85rem;
        background-color: #e3f2fd;
        color: #0d47a1;
        border: 1px solid #90caf9;
    }
    .card {
        padding: 1.2rem;
        border-radius: 10px;
        border: 1px solid #e0e0e0;
        background-color: #fafafa;
        margin-bottom: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def main():
    # Initialize DB on load
    init_db()

    # Header section
    st.markdown('<div class="main-header">🛡️ Agent Allowance</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-header">Deterministic Financial Guardrails for Autonomous AI Agents · Powered by PayPal Sandbox</div>',
        unsafe_allow_html=True,
    )

    # Top status bar
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(label="PayPal Environment", value="Sandbox Only 🧪")
    with col2:
        paypal_configured = "Configured ✅" if Config.validate_paypal_config() else "Needs .env ⚠️"
        st.metric(label="PayPal Credentials", value=paypal_configured)
    with col3:
        st.metric(label="LLM Provider", value=Config.LLM_PROVIDER.title())
    with col4:
        conn = get_db_connection()
        rule_count = conn.execute("SELECT COUNT(*) FROM rules WHERE is_active = 1").fetchone()[0]
        conn.close()
        st.metric(label="Active Rules", value=f"{rule_count} Enforced")

    st.divider()

    # Core Concept & Architecture Overview
    st.subheader("System Architecture")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            """
            ### 1. 🤖 AI Shopping Agent
            - Reads shopping requests
            - Proposes candidate purchases
            - **Zero access** to payment credentials or execution APIs
            """
        )
    with c2:
        st.markdown(
            """
            ### 2. 🛡️ Deterministic Rule Engine
            - Pure Python code (never the LLM)
            - Hard constraints & daily limits
            - Verdict: **APPROVE**, **BLOCK**, or **ESCALATE**
            """
        )
    with c3:
        st.markdown(
            """
            ### 3. 💳 PayPal Sandbox Execution
            - Creates PayPal order **only** upon strict rule approval
            - Captures sandbox payment
            - Full audit trail stored in SQLite ledger
            """
        )

    st.info(
        "🚀 **Setup Ready!** Project foundation, database tables, and configuration are loaded. "
        "Next steps: implement the deterministic policy engine, PayPal client, and AI agent proposals."
    )


if __name__ == "__main__":
    main()
