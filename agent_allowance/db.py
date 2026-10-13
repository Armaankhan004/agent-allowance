"""
Database setup and schema for Agent Allowance.
Uses SQLite for persistent storage of allowance policies, audit logs, and ledger transactions.
"""

import sqlite3
from typing import Optional
from .config import Config


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get a connection to the SQLite database with dictionary-like row access."""
    path = db_path or Config.DATABASE_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize database tables and seed baseline allowance rules if not present."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Table 1: Rules (Allowance policies enforced by the deterministic rule engine)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_name TEXT NOT NULL UNIQUE,
            description TEXT,
            rule_type TEXT NOT NULL, -- 'MAX_PER_TRANSACTION', 'DAILY_ALLOWANCE', 'ALLOWED_CATEGORY', 'FORBIDDEN_CATEGORY', 'APPROVAL_THRESHOLD'
            condition_value TEXT NOT NULL, -- Threshold amount or comma-separated list of categories/tags
            action TEXT NOT NULL, -- 'APPROVE', 'BLOCK', 'ESCALATE'
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Table 2: Transactions / Ledger (Audit trail of proposals, evaluations, and PayPal Sandbox payments)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT NOT NULL UNIQUE,
            agent_name TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_title TEXT NOT NULL,
            category TEXT NOT NULL,
            merchant TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            status TEXT NOT NULL, -- 'PROPOSED', 'APPROVED', 'BLOCKED', 'ESCALATED', 'EXECUTED', 'REJECTED'
            evaluation_reason TEXT,
            paypal_order_id TEXT,
            paypal_capture_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Table 3: Audit Log (Detailed step-by-step trace of policy checks and decisions)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT NOT NULL,
            step TEXT NOT NULL,
            result TEXT NOT NULL,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Seed baseline rules if table is empty
    cursor.execute("SELECT COUNT(*) as count FROM rules")
    count = cursor.fetchone()["count"]
    if count == 0:
        default_rules = [
            (
                "Hard Cap Per Transaction",
                "Transactions over $75.00 are strictly blocked.",
                "MAX_PER_TRANSACTION",
                "75.00",
                "BLOCK",
                1,
            ),
            (
                "Human Escalation Threshold",
                "Transactions between $30.00 and $75.00 require human escalation/approval.",
                "APPROVAL_THRESHOLD",
                "30.00",
                "ESCALATE",
                1,
            ),
            (
                "Daily Budget Cap",
                "Cumulative approved transactions cannot exceed $150.00 per day.",
                "DAILY_ALLOWANCE",
                "150.00",
                "BLOCK",
                1,
            ),
            (
                "Forbidden Categories",
                "Certain high-risk or prohibited categories are blocked automatically.",
                "FORBIDDEN_CATEGORY",
                "gift_cards,crypto,gambling,adult",
                "BLOCK",
                1,
            ),
        ]
        cursor.executemany(
            """
            INSERT INTO rules (rule_name, description, rule_type, condition_value, action, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            default_rules,
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
