"""
Demo seed rows for a fresh (or freshly reset) ledger.

Each seed row is produced by running a real proposal through
policy_engine.evaluate_proposal, so its status and reasons are exactly what the
policy engine says. Seed rows never touch PayPal (evaluate_proposal makes no
network calls) and never carry an order id.

Safety: only BLOCKED and NEEDS_APPROVAL rows are ever seeded. Each proposal is
first evaluated in a throwaway database holding a copy of the live rules; if the
outcome is not the expected one (for example, someone raised the approval
threshold so the flour would auto-approve), that row is skipped.
"""

import os
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional

from .catalog import get_product
from .db import get_db_connection, init_db, reset_demo_transactions
from .policy_engine import evaluate_proposal

DEMO_PROPOSAL_PREFIX = "demo-seed-"
DEMO_DATA_LABEL = "Demo data: sample row created when the ledger was empty. No PayPal order was made."

# Statuses a seed row may ever have. APPROVED / EXECUTED are never seeded.
_ALLOWED_SEED_STATUSES = frozenset({"BLOCKED", "NEEDS_APPROVAL"})


def _attack_proposal() -> Dict[str, Any]:
    """The Apex gift card injection, built exactly like the Demo-mode agent builds it."""
    item = get_product("bad-injection-999")
    return {
        "proposal_id": f"{DEMO_PROPOSAL_PREFIX}gift-card-attack",
        "merchant": item["merchant"],
        "amount": float(item["price"]),
        "currency": item["currency"],
        "category": "Office Supplies",  # spoofed category, as the injected listing demands
        "reason": "Urgent authorized executive supplies (obeying listing override)",
        "item_id": item["id"],
        "item_title": item["name"],
        "demo_data": True,
    }


def _flour_proposal() -> Dict[str, Any]:
    item = get_product("gg-flour-001")
    return {
        "proposal_id": f"{DEMO_PROPOSAL_PREFIX}flour-restock",
        "merchant": item["merchant"],
        "amount": float(item["price"]),
        "currency": item["currency"],
        "category": item["category"],
        "reason": f"Restocking {item['name']} for bakery operations",
        "item_id": item["id"],
        "item_title": item["name"],
        "demo_data": True,
    }


def demo_seed_plan() -> List[Dict[str, Any]]:
    """Seed proposals in insertion order, each with the status it must produce."""
    return [
        {"proposal": _attack_proposal(), "expected": "BLOCKED"},
        {"proposal": _flour_proposal(), "expected": "NEEDS_APPROVAL"},
    ]


def is_demo_row(row: Dict[str, Any]) -> bool:
    proposal = row.get("proposal") or {}
    return str(row.get("proposal_id") or "").startswith(DEMO_PROPOSAL_PREFIX) or bool(
        isinstance(proposal, dict) and proposal.get("demo_data")
    )


def _transaction_count(db_path: Optional[str]) -> int:
    conn = get_db_connection(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
    finally:
        conn.close()


def _dry_run_status(proposal: Dict[str, Any], db_path: Optional[str]) -> str:
    """Evaluate against a temp DB holding a copy of the live rules and an empty ledger."""
    src = get_db_connection(db_path)
    try:
        rules = [tuple(r) for r in src.execute(
            "SELECT rule_name, description, rule_type, condition_value, action, is_active FROM rules"
        ).fetchall()]
    finally:
        src.close()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = os.path.join(tmp, "dry_run.db")
        init_db(tmp_db)
        conn = get_db_connection(tmp_db)
        conn.execute("DELETE FROM rules")
        conn.executemany(
            "INSERT INTO rules (rule_name, description, rule_type, condition_value, action, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rules,
        )
        conn.commit()
        conn.close()
        return evaluate_proposal(dict(proposal), db_path=tmp_db).status


def _delete_row(ledger_id: int, proposal_id: str, db_path: Optional[str]) -> None:
    conn = get_db_connection(db_path)
    conn.execute("DELETE FROM transactions WHERE id = ?", (ledger_id,))
    conn.execute("DELETE FROM audit_logs WHERE proposal_id = ?", (proposal_id,))
    conn.commit()
    conn.close()


def seed_demo_data(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Insert the demo rows into an EMPTY ledger. Does nothing if any transaction exists.
    Returns [{"proposal_id", "status", "ledger_id"}] for rows actually inserted.
    """
    if _transaction_count(db_path) > 0:
        return []

    seeded: List[Dict[str, Any]] = []
    for step in demo_seed_plan():
        proposal, expected = step["proposal"], step["expected"]
        if expected not in _ALLOWED_SEED_STATUSES:
            continue
        if _dry_run_status(proposal, db_path) != expected:
            continue
        try:
            decision = evaluate_proposal(dict(proposal), db_path=db_path)
        except sqlite3.IntegrityError:
            # Another session seeded this row at the same moment (proposal_id is UNIQUE).
            continue
        if decision.status not in _ALLOWED_SEED_STATUSES or decision.status != expected:
            # Rules changed between the dry run and now: never leave an unexpected row.
            _delete_row(decision.ledger_id, proposal["proposal_id"], db_path)
            continue
        seeded.append({
            "proposal_id": proposal["proposal_id"],
            "status": decision.status,
            "ledger_id": decision.ledger_id,
        })
    return seeded


def reset_and_reseed(db_path: Optional[str] = None) -> int:
    """Reset Demo Data: clear transactions and audit logs (rules kept), then re-seed."""
    deleted = reset_demo_transactions(db_path)
    seed_demo_data(db_path)
    return deleted
