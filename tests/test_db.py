"""
Initial test suite to verify project setup and database schema initialization.
"""

import os
import sqlite3
import pytest
from agent_allowance.config import Config
from agent_allowance.db import init_db, get_db_connection


def test_config_loads_defaults():
    """Verify default configurations are present."""
    assert Config.PAYPAL_MODE == "sandbox"
    assert Config.PAYPAL_API_BASE_URL == "https://api-m.sandbox.paypal.com"
    assert Config.DEFAULT_CURRENCY == "USD"


def test_database_initialization(tmp_path):
    """Verify SQLite database schema and rule seeding."""
    test_db = str(tmp_path / "test_allowance.db")
    init_db(test_db)

    conn = get_db_connection(test_db)
    cursor = conn.cursor()

    # Verify tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row["name"] for row in cursor.fetchall()]
    assert "rules" in tables
    assert "transactions" in tables
    assert "audit_logs" in tables

    # Verify seed rules are populated
    cursor.execute("SELECT COUNT(*) as count FROM rules")
    count = cursor.fetchone()["count"]
    assert count > 0

    conn.close()
