"""
Database setup and schema for Agent Allowance.
Uses SQLite for persistent storage of allowance policies, audit logs, and ledger transactions.
"""

import sqlite3
from typing import Optional, List, Dict, Any
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
                "Golden Grain Mills, EcoPack Supplies, Corner Office Supply, Nexus Retail, Beacon Mart, Zenith Electronics, Prairie Goods, CodeHaven, CloudByte",
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
    else:
        # Migrate existing rules to replace legacy real brands with fictional ones
        cursor.execute(
            """
            UPDATE rules
            SET condition_value = 'Golden Grain Mills, EcoPack Supplies, Corner Office Supply, Nexus Retail, Beacon Mart, Zenith Electronics, Prairie Goods, CodeHaven, CloudByte'
            WHERE rule_type = 'allowed_merchants' AND condition_value LIKE '%Office Depot%'
            """
        )

    conn.commit()
    conn.close()


def reset_demo_transactions(db_path: Optional[str] = None) -> int:
    """
    Reset demo data by clearing the transactions ledger and audit_logs.
    CRITICAL SECURITY & CONFIG GUARANTEE: Does NOT delete or alter the rules table.
    Returns the number of deleted transaction rows.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM transactions")
    count = cursor.fetchone()[0]
    cursor.execute("DELETE FROM transactions")
    cursor.execute("DELETE FROM audit_logs")
    conn.commit()
    conn.close()
    return count


def get_all_transactions(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieve all transactions from the ledger ordered newest first.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, proposal_id, merchant, amount, currency, category, reason, status,
               reasons, evaluation_reason, paypal_order_id, paypal_approval_url, paypal_capture_id,
               created_at, updated_at
        FROM transactions
        ORDER BY id DESC
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_pending_transactions(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retrieve all transactions with status 'NEEDS_APPROVAL' awaiting human review.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, proposal_id, merchant, amount, currency, category, reason, status,
               reasons, evaluation_reason, paypal_order_id, paypal_approval_url,
               created_at, updated_at
        FROM transactions
        WHERE status = 'NEEDS_APPROVAL'
        ORDER BY id DESC
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_dashboard_metrics(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Compute headline allowance metrics for dashboard display:
      - approved_spend: Decimal amount approved in current month
      - monthly_budget: Decimal total budget for current month
      - budget_remaining: Decimal amount remaining before hitting limit
      - budget_used_pct: float (0.0 to 1.0) fraction of budget used
      - pending_count: int number of transactions in NEEDS_APPROVAL
      - blocked_count: int number of transactions BLOCKED this month
    """
    from datetime import datetime, timezone
    from decimal import Decimal, ROUND_HALF_UP

    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # 1. Monthly budget from active rules
    cursor.execute("SELECT condition_value FROM rules WHERE rule_type = 'monthly_budget' AND is_active = 1")
    row = cursor.fetchone()
    monthly_budget_val = row["condition_value"] if row else "200.00"
    try:
        monthly_budget = Decimal(str(monthly_budget_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        monthly_budget = Decimal("200.00")

    # 2. Approved spend this calendar month
    target_month = datetime.now(timezone.utc).strftime("%Y-%m")
    cursor.execute(
        """
        SELECT amount FROM transactions
        WHERE status = 'APPROVED'
          AND (strftime('%Y-%m', created_at) = ? OR created_at LIKE ?)
        """,
        (target_month, f"{target_month}%"),
    )
    approved_spend = Decimal("0.00")
    for r in cursor.fetchall():
        approved_spend += Decimal(str(r["amount"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 3. Budget remaining and percentage used
    budget_remaining = max(Decimal("0.00"), monthly_budget - approved_spend)
    budget_used_pct = float(min(1.0, float(approved_spend) / float(monthly_budget))) if monthly_budget > 0 else 0.0

    # 4. Pending count awaiting approval
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE status = 'NEEDS_APPROVAL'")
    pending_count = cursor.fetchone()[0]

    # 5. Blocked count this month
    cursor.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE status = 'BLOCKED'
          AND (strftime('%Y-%m', created_at) = ? OR created_at LIKE ?)
        """,
        (target_month, f"{target_month}%"),
    )
    blocked_count = cursor.fetchone()[0]

    conn.close()

    return {
        "approved_spend": approved_spend,
        "monthly_budget": monthly_budget,
        "budget_remaining": budget_remaining,
        "budget_used_pct": budget_used_pct,
        "pending_count": pending_count,
        "blocked_count": blocked_count,
    }


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")

