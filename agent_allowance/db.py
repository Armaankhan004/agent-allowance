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
            rule_type TEXT NOT NULL, -- 'max_per_purchase', 'monthly_budget', 'allowed_merchants', 'approval_threshold'
            condition_value TEXT NOT NULL, -- Threshold amount or comma-separated list of merchants/categories
            action TEXT NOT NULL, -- 'BLOCK', 'NEEDS_APPROVAL', 'APPROVE'
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
            merchant TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            category TEXT NOT NULL,
            reason TEXT,
            agent_name TEXT DEFAULT 'Shopping Agent',
            item_id TEXT DEFAULT '',
            item_title TEXT DEFAULT '',
            status TEXT NOT NULL, -- 'APPROVED', 'BLOCKED', 'NEEDS_APPROVAL', 'REJECTED', 'EXECUTED'
            reasons TEXT, -- JSON array of plain-English evaluation strings
            evaluation_reason TEXT, -- Semicolon-delimited summary string
            raw_proposal TEXT, -- JSON dump of the original proposal
            paypal_order_id TEXT,
            paypal_approval_url TEXT,
            paypal_capture_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Automatically add any missing columns if table was created in an older version
    cursor.execute("PRAGMA table_info(transactions)")
    existing_cols = {row["name"] for row in cursor.fetchall()}
    needed_cols = {
        "reason": "TEXT",
        "reasons": "TEXT",
        "raw_proposal": "TEXT",
        "paypal_approval_url": "TEXT",
    }
    for col_name, col_type in needed_cols.items():
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_type}")

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
                "Max Single Purchase Cap",
                "Transactions over $50.00 are strictly blocked.",
                "max_per_purchase",
                "50.00",
                "BLOCK",
                1,
            ),
            (
                "Monthly Allowance Budget",
                "Total approved spend in the current calendar month cannot exceed $200.00.",
                "monthly_budget",
                "200.00",
                "BLOCK",
                1,
            ),
            (
                "Allowed Merchants Allowlist",
                "Only approved commercial vendors may be used by the agent (case-insensitive).",
                "allowed_merchants",
                "Golden Grain Mills, EcoPack Supplies, Office Depot, Amazon, Target, Best Buy, Walmart, GitHub, DigitalOcean",
                "BLOCK",
                1,
            ),
            (
                "Human Approval Threshold",
                "Transactions exceeding $25.00 require human confirmation before execution.",
                "approval_threshold",
                "25.00",
                "NEEDS_APPROVAL",
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
