"""
Global daily cap on real Gemini requests, shared by ALL visitors.

The counter lives in its own small SQLite table (llm_usage), separate from the
rules / transactions / audit_logs tables, so Reset Demo Data never clears it.
The day is the UTC calendar date, so the cap resets at midnight UTC.
Demo mode never calls this module and is never capped.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional

from .db import get_db_connection

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT PRIMARY KEY,          -- UTC date, YYYY-MM-DD
    requests INTEGER NOT NULL DEFAULT 0
)
"""


def utc_day(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _connect(db_path: Optional[str]) -> sqlite3.Connection:
    conn = get_db_connection(db_path)
    conn.execute(_CREATE_SQL)
    return conn


def daily_used(db_path: Optional[str] = None, now: Optional[datetime] = None) -> int:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT requests FROM llm_usage WHERE day = ?", (utc_day(now),)).fetchone()
        return int(row["requests"]) if row else 0
    finally:
        conn.close()


def daily_remaining(cap: int, db_path: Optional[str] = None, now: Optional[datetime] = None) -> int:
    return max(0, int(cap) - daily_used(db_path, now))


def try_claim_request(cap: int, db_path: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """
    Atomically claim one real LLM request for today. Returns False (and claims nothing)
    when the cap is already spent. Safe across concurrent sessions: the increment is a
    single conditional UPDATE inside a write transaction.
    """
    if cap <= 0:
        return False
    day = utc_day(now)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM llm_usage WHERE day < ?", (day,))
        conn.execute("INSERT OR IGNORE INTO llm_usage (day, requests) VALUES (?, 0)", (day,))
        cur = conn.execute(
            "UPDATE llm_usage SET requests = requests + 1 WHERE day = ? AND requests < ?",
            (day, int(cap)),
        )
        conn.commit()
        return cur.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def make_request_gate(cap: int, db_path: Optional[str] = None) -> Callable[[], bool]:
    """A callable for ShoppingAgent.run(llm_request_gate=...): claims one slot per real request."""
    return lambda: try_claim_request(cap, db_path=db_path)
