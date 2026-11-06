"""
Tests for demo seed rows: produced by the real policy engine, never APPROVED or
EXECUTED, never carrying a PayPal order id, and re-created by Reset Demo Data.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

from agent_allowance.db import get_db_connection, get_ledger_rows, init_db
from agent_allowance.demo_seed import (
    DEMO_DATA_LABEL,
    demo_seed_plan,
    is_demo_row,
    reset_and_reseed,
    seed_demo_data,
)
from agent_allowance.policy_engine import evaluate_proposal, get_monthly_approved_spend


@pytest.fixture
def test_db(tmp_path):
    db_file = str(tmp_path / "test_seed.db")
    init_db(db_file)
    return db_file


@pytest.fixture(autouse=True)
def no_network():
    """Any HTTP call during seeding is a bug."""
    with patch("agent_allowance.paypal_client.requests.post", side_effect=AssertionError("network")), \
         patch("agent_allowance.paypal_client.requests.get", side_effect=AssertionError("network")):
        yield


def _fresh_reasons(proposal):
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "ref.db")
        init_db(db)
        d = evaluate_proposal(dict(proposal), db_path=db)
        return d.status, d.reasons


def test_seeds_blocked_attack_and_pending_flour(test_db):
    seeded = seed_demo_data(test_db)
    assert [s["status"] for s in seeded] == ["BLOCKED", "NEEDS_APPROVAL"]

    rows = {r["proposal_id"]: r for r in get_ledger_rows(test_db)}
    assert len(rows) == 2
    attack = rows["demo-seed-gift-card-attack"]
    flour = rows["demo-seed-flour-restock"]

    assert attack["status"] == "BLOCKED"
    assert attack["merchant"] == "Apex Gift Cards"
    assert attack["amount"] == "500.00"
    assert flour["status"] == "NEEDS_APPROVAL"
    assert flour["merchant"] == "Golden Grain Mills"
    assert flour["amount"] == "28.50"


def test_seed_reasons_match_the_real_policy_engine(test_db):
    seed_demo_data(test_db)
    rows = {r["proposal_id"]: r for r in get_ledger_rows(test_db)}
    for step in demo_seed_plan():
        status, reasons = _fresh_reasons(step["proposal"])
        row = rows[step["proposal"]["proposal_id"]]
        assert row["status"] == status == step["expected"]
        assert row["reasons"] == reasons
    attack_reasons = " ".join(rows["demo-seed-gift-card-attack"]["reasons"])
    assert "not on the allowed merchants list" in attack_reasons
    assert "maximum per-purchase limit" in attack_reasons


def test_seed_rows_never_claim_paypal_orders_or_approval(test_db):
    seed_demo_data(test_db)
    conn = get_db_connection(test_db)
    rows = conn.execute(
        "SELECT status, paypal_order_id, paypal_approval_url, paypal_capture_id FROM transactions"
    ).fetchall()
    conn.close()
    for r in rows:
        assert r["status"] not in ("APPROVED", "EXECUTED")
        assert not r["paypal_order_id"]
        assert not r["paypal_approval_url"]
        assert not r["paypal_capture_id"]
    assert get_monthly_approved_spend(db_path=test_db) == 0


def test_seed_rows_are_labelled_demo_data(test_db):
    seed_demo_data(test_db)
    rows = get_ledger_rows(test_db)
    assert rows and all(is_demo_row(r) for r in rows)
    assert "Demo data" in DEMO_DATA_LABEL


def test_non_seed_rows_are_not_labelled_demo(test_db):
    evaluate_proposal({"merchant": "CodeHaven", "amount": 5, "currency": "USD",
                       "category": "Software", "reason": "x"}, db_path=test_db)
    assert not any(is_demo_row(r) for r in get_ledger_rows(test_db))


def test_does_not_seed_a_non_empty_ledger(test_db):
    evaluate_proposal({"merchant": "CodeHaven", "amount": 5, "currency": "USD",
                       "category": "Software", "reason": "x"}, db_path=test_db)
    assert seed_demo_data(test_db) == []
    assert len(get_ledger_rows(test_db)) == 1


def test_seeding_is_idempotent(test_db):
    seed_demo_data(test_db)
    seed_demo_data(test_db)
    assert len(get_ledger_rows(test_db)) == 2


def test_reset_reseeds_and_keeps_rules(test_db):
    conn = get_db_connection(test_db)
    rules_before = [tuple(r) for r in conn.execute("SELECT * FROM rules ORDER BY id").fetchall()]
    conn.close()

    seed_demo_data(test_db)
    evaluate_proposal({"merchant": "CodeHaven", "amount": 5, "currency": "USD",
                       "category": "Software", "reason": "x"}, db_path=test_db)
    deleted = reset_and_reseed(test_db)
    assert deleted == 3

    rows = get_ledger_rows(test_db)
    assert sorted(r["status"] for r in rows) == ["BLOCKED", "NEEDS_APPROVAL"]
    assert all(is_demo_row(r) for r in rows)

    conn = get_db_connection(test_db)
    rules_after = [tuple(r) for r in conn.execute("SELECT * FROM rules ORDER BY id").fetchall()]
    conn.close()
    assert rules_after == rules_before


def test_skips_flour_when_edited_rules_would_auto_approve_it(test_db):
    conn = get_db_connection(test_db)
    conn.execute("UPDATE rules SET condition_value = '100.00' WHERE rule_type = 'approval_threshold'")
    conn.commit()
    conn.close()

    seeded = seed_demo_data(test_db)
    assert [s["status"] for s in seeded] == ["BLOCKED"]
    statuses = [r["status"] for r in get_ledger_rows(test_db)]
    assert statuses == ["BLOCKED"]
    # The dry run must not have left any trace in the real audit log either.
    conn = get_db_connection(test_db)
    audit = [r["proposal_id"] for r in conn.execute("SELECT proposal_id FROM audit_logs").fetchall()]
    conn.close()
    assert audit == ["demo-seed-gift-card-attack"]
