"""
Deterministic Policy Engine for Agent Allowance.
Evaluates agent purchase proposals against configured allowance rules (pure code, NO LLM, NO network).
Enforces strict budget guardrails, per-purchase caps, merchant allowlists, and human approval thresholds.
"""

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Any, List, Optional, Tuple
from .db import get_db_connection


class Decision(dict):
    """
    Structured policy decision object.
    Supports both dict indexing (decision['status']) and attribute access (decision.status).
    """

    def __init__(
        self,
        status: str,
        reasons: List[str],
        ledger_id: Optional[int] = None,
        proposal: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            status=status,
            reasons=reasons,
            ledger_id=ledger_id,
            proposal=proposal or {},
        )
        self.status = status
        self.reasons = reasons
        self.ledger_id = ledger_id
        self.proposal = proposal or {}

    def __repr__(self) -> str:
        return f"Decision(status='{self.status}', reasons={self.reasons!r}, ledger_id={self.ledger_id})"


def get_monthly_approved_spend(
    db_path: Optional[str] = None, month_prefix: Optional[str] = None
) -> Decimal:
    """
    Calculate the sum of all APPROVED transactions in the specified calendar month.
    Defaults to current UTC calendar month.
    """
    target_month = month_prefix or datetime.now(timezone.utc).strftime("%Y-%m")
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT amount FROM transactions
        WHERE status IN ('APPROVED', 'EXECUTED')
          AND strftime('%Y-%m', created_at) = ?
        """,
        (target_month,),
    )

    total = Decimal("0.00")
    for row in cursor.fetchall():
        total += Decimal(str(row["amount"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    conn.close()
    return total


def evaluate_proposal(
    proposal: Dict[str, Any], db_path: Optional[str] = None
) -> Decision:
    """
    Deterministically evaluate a purchase proposal against active allowance rules in SQLite.

    Input:
        proposal: dict with keys {merchant, amount, currency, category, reason}
        db_path: optional path to SQLite DB (useful for isolated tests)

    Output:
        Decision object with status ('APPROVED' | 'BLOCKED' | 'NEEDS_APPROVAL') and reasons.
    """
    reasons: List[str] = []
    has_hard_violation = False
    needs_approval = False

    # --------------------------------------------------------------------------
    # 1. Input Validation
    # --------------------------------------------------------------------------
    if not isinstance(proposal, dict):
        reasons.append("Proposal must be a valid dictionary.")
        return _write_decision_to_ledger(
            proposal={"raw": str(proposal)},
            status="BLOCKED",
            reasons=reasons,
            amount_decimal=None,
            db_path=db_path,
        )

    # Validate required keys
    required_keys = ("merchant", "amount", "currency", "category", "reason")
    for key in required_keys:
        if key not in proposal:
            reasons.append(f"Missing required field: '{key}'.")
            has_hard_violation = True
        elif proposal[key] is None or (
            isinstance(proposal[key], str) and not proposal[key].strip()
        ):
            reasons.append(f"Required field '{key}' cannot be empty.")
            has_hard_violation = True

    # Validate Amount using Decimal
    amount_decimal: Optional[Decimal] = None
    if "amount" in proposal and proposal["amount"] is not None:
        try:
            raw_amt_str = str(proposal["amount"]).strip()
            amount_decimal = Decimal(raw_amt_str).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if amount_decimal.is_nan() or amount_decimal.is_infinite():
                reasons.append("Amount must be a finite numeric value.")
                has_hard_violation = True
                amount_decimal = None
            elif amount_decimal <= Decimal("0.00"):
                reasons.append(
                    f"Amount must be positive and greater than $0.00 (received ${raw_amt_str})."
                )
                has_hard_violation = True
        except (InvalidOperation, ValueError, TypeError):
            reasons.append(
                f"Invalid or non-numeric amount: {proposal['amount']!r}."
            )
            has_hard_violation = True
            amount_decimal = None

    # Validate Currency
    currency_str = str(proposal.get("currency", "")).strip().upper()
    if currency_str and currency_str != "USD":
        reasons.append(
            f"Unsupported currency '{proposal.get('currency')}'. Only 'USD' is permitted."
        )
        has_hard_violation = True

    # Validate Merchant string
    merchant_str = str(proposal.get("merchant", "")).strip()

    # --------------------------------------------------------------------------
    # 2. Load Active Rules from Database
    # --------------------------------------------------------------------------
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT rule_type, condition_value FROM rules WHERE is_active = 1"
    )
    rules_map: Dict[str, str] = {}
    for row in cursor.fetchall():
        rules_map[row["rule_type"].strip().lower()] = row["condition_value"]
    conn.close()

    # --------------------------------------------------------------------------
    # 3. Rule Evaluations (Evaluate ALL rules, collecting all violations)
    # --------------------------------------------------------------------------

    # Rule A: allowed_merchants (case-insensitive allowlist)
    if merchant_str and "allowed_merchants" in rules_map:
        allowed_raw = rules_map["allowed_merchants"]
        allowed_merchants = [
            m.strip().lower() for m in allowed_raw.split(",") if m.strip()
        ]
        if merchant_str.lower() not in allowed_merchants:
            reasons.append(
                f"Merchant '{merchant_str}' is not on the allowed merchants list."
            )
            has_hard_violation = True

    # Rule B: max_per_purchase (single purchase limit)
    if amount_decimal is not None and "max_per_purchase" in rules_map:
        try:
            max_per_purchase = Decimal(rules_map["max_per_purchase"]).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if amount_decimal > max_per_purchase:
                reasons.append(
                    f"Amount ${amount_decimal:.2f} exceeds the maximum per-purchase limit of ${max_per_purchase:.2f}."
                )
                has_hard_violation = True
        except (InvalidOperation, ValueError):
            pass

    # Rule C: monthly_budget (current month approved + this proposal)
    if amount_decimal is not None and "monthly_budget" in rules_map:
        try:
            monthly_budget = Decimal(rules_map["monthly_budget"]).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            current_approved_spend = get_monthly_approved_spend(db_path=db_path)
            projected_spend = current_approved_spend + amount_decimal
            if projected_spend > monthly_budget:
                reasons.append(
                    f"Amount ${amount_decimal:.2f} would bring monthly approved spend to ${projected_spend:.2f}, "
                    f"exceeding the monthly budget of ${monthly_budget:.2f} "
                    f"(current approved spend: ${current_approved_spend:.2f})."
                )
                has_hard_violation = True
        except (InvalidOperation, ValueError):
            pass

    # Rule D: approval_threshold (amounts above this, but otherwise valid, need human approval)
    if amount_decimal is not None and "approval_threshold" in rules_map:
        try:
            approval_threshold = Decimal(
                rules_map["approval_threshold"]
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if amount_decimal > approval_threshold:
                needs_approval = True
                # If there are no hard violations yet, state why human approval is required
                threshold_reason = (
                    f"Amount ${amount_decimal:.2f} exceeds the human approval threshold of ${approval_threshold:.2f} "
                    "and requires human approval."
                )
        except (InvalidOperation, ValueError):
            pass

    # --------------------------------------------------------------------------
    # 4. Final Status Determination
    # --------------------------------------------------------------------------
    if has_hard_violation:
        final_status = "BLOCKED"
    elif needs_approval:
        final_status = "NEEDS_APPROVAL"
        reasons.append(threshold_reason)
    else:
        final_status = "APPROVED"
        reasons.append("Within all allowance limits and approved automatically.")

    # --------------------------------------------------------------------------
    # 5. Ledger Recording & Decision Creation
    # --------------------------------------------------------------------------
    return _write_decision_to_ledger(
        proposal=proposal,
        status=final_status,
        reasons=reasons,
        amount_decimal=amount_decimal,
        db_path=db_path,
    )


def _write_decision_to_ledger(
    proposal: Dict[str, Any],
    status: str,
    reasons: List[str],
    amount_decimal: Optional[Decimal],
    db_path: Optional[str] = None,
) -> Decision:
    """Internal helper to persist proposal evaluation to SQLite transactions and audit_logs."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    proposal_id = proposal.get("proposal_id") or f"prop-{uuid.uuid4().hex[:12]}"
    merchant = str(proposal.get("merchant", "UNKNOWN")).strip()
    currency = str(proposal.get("currency", "USD")).strip().upper()
    category = str(proposal.get("category", "Uncategorized")).strip()
    reason_for_purchase = str(proposal.get("reason", "")).strip()

    amount_float = (
        float(amount_decimal) if amount_decimal is not None else 0.0
    )
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        INSERT INTO transactions (
            proposal_id, merchant, amount, currency, category, reason,
            status, reasons, evaluation_reason, raw_proposal,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proposal_id,
            merchant,
            amount_float,
            currency,
            category,
            reason_for_purchase,
            status,
            json.dumps(reasons),
            "; ".join(reasons),
            json.dumps(proposal, default=str),
            now_str,
            now_str,
        ),
    )
    ledger_id = cursor.lastrowid

    # Record in audit logs as well
    cursor.execute(
        """
        INSERT INTO audit_logs (proposal_id, step, result, details, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            proposal_id,
            "POLICY_EVALUATION",
            status,
            "; ".join(reasons),
            now_str,
        ),
    )

    conn.commit()
    conn.close()

    return Decision(
        status=status,
        reasons=reasons,
        ledger_id=ledger_id,
        proposal=proposal,
    )


# ==============================================================================
# Human Escalation Actions (Approve / Reject Pending)
# ==============================================================================


def approve_pending(
    ledger_id: int, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Approve a transaction previously marked as NEEDS_APPROVAL.
    Transitions status to APPROVED, allowing it to count toward monthly budget and proceed to payment.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM transactions WHERE id = ?", (ledger_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Ledger record with ID {ledger_id} not found.")

    if row["status"] != "NEEDS_APPROVAL":
        conn.close()
        raise ValueError(
            f"Cannot approve transaction {ledger_id}: current status is '{row['status']}', expected 'NEEDS_APPROVAL'."
        )

    # Re-check monthly budget before approving
    cursor.execute(
        "SELECT condition_value FROM rules WHERE rule_type = 'monthly_budget' AND is_active = 1"
    )
    budget_row = cursor.fetchone()
    if budget_row:
        try:
            monthly_budget = Decimal(str(budget_row["condition_value"])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            item_amount = Decimal(str(row["amount"])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            target_month = (row["created_at"] or "")[:7] or datetime.now(timezone.utc).strftime("%Y-%m")
            current_approved = get_monthly_approved_spend(db_path=db_path, month_prefix=target_month)
            projected = current_approved + item_amount
            if projected > monthly_budget:
                refusal_reason = (
                    f"Approval refused: Transaction amount ${item_amount:.2f} would bring "
                    f"monthly approved spend to ${projected:.2f}, exceeding the monthly "
                    f"budget of ${monthly_budget:.2f} (current approved spend: ${current_approved:.2f})."
                )
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute(
                    """
                    INSERT INTO audit_logs (proposal_id, step, result, details, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["proposal_id"],
                        "HUMAN_APPROVAL_REFUSED",
                        "REFUSED",
                        refusal_reason,
                        now_str,
                    ),
                )
                conn.commit()
                conn.close()
                existing_reasons = []
                try:
                    existing_reasons = json.loads(row["reasons"] or "[]")
                except Exception:
                    pass
                return {
                    "ledger_id": ledger_id,
                    "proposal_id": row["proposal_id"],
                    "status": "NEEDS_APPROVAL",
                    "approved": False,
                    "error": refusal_reason,
                    "refusal_reason": refusal_reason,
                    "reasons": existing_reasons + [refusal_reason],
                }
        except (InvalidOperation, ValueError):
            pass

    # Parse existing reasons and append approval note
    existing_reasons = []
    try:
        existing_reasons = json.loads(row["reasons"] or "[]")
    except Exception:
        pass
    existing_reasons.append(
        "Approved by human supervisor. Ready for payment execution."
    )

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        UPDATE transactions
        SET status = 'APPROVED',
            reasons = ?,
            evaluation_reason = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            json.dumps(existing_reasons),
            "; ".join(existing_reasons),
            now_str,
            ledger_id,
        ),
    )

    cursor.execute(
        """
        INSERT INTO audit_logs (proposal_id, step, result, details, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            row["proposal_id"],
            "HUMAN_APPROVAL",
            "APPROVED",
            "Human approved transaction.",
            now_str,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "ledger_id": ledger_id,
        "proposal_id": row["proposal_id"],
        "status": "APPROVED",
        "approved": True,
        "reasons": existing_reasons,
    }


def reject_pending(
    ledger_id: int,
    db_path: Optional[str] = None,
    rejection_reason: str = "Rejected by human supervisor.",
) -> Dict[str, Any]:
    """
    Reject a transaction previously marked as NEEDS_APPROVAL.
    Transitions status to REJECTED. It will NOT count toward monthly budget.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM transactions WHERE id = ?", (ledger_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Ledger record with ID {ledger_id} not found.")

    if row["status"] != "NEEDS_APPROVAL":
        conn.close()
        raise ValueError(
            f"Cannot reject transaction {ledger_id}: current status is '{row['status']}', expected 'NEEDS_APPROVAL'."
        )

    existing_reasons = []
    try:
        existing_reasons = json.loads(row["reasons"] or "[]")
    except Exception:
        pass
    existing_reasons.append(rejection_reason)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        UPDATE transactions
        SET status = 'REJECTED',
            reasons = ?,
            evaluation_reason = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            json.dumps(existing_reasons),
            "; ".join(existing_reasons),
            now_str,
            ledger_id,
        ),
    )

    cursor.execute(
        """
        INSERT INTO audit_logs (proposal_id, step, result, details, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            row["proposal_id"],
            "HUMAN_REJECTION",
            "REJECTED",
            rejection_reason,
            now_str,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "ledger_id": ledger_id,
        "proposal_id": row["proposal_id"],
        "status": "REJECTED",
        "reasons": existing_reasons,
    }


# ==============================================================================
# Rule Inspection & Mutation Helpers (Stored in SQLite, strictly validated)
# ==============================================================================


def validate_rule_value(
    rule_type: str, condition_value: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate allowance rule values:
    - Numeric rules (max_per_purchase, monthly_budget, approval_threshold) must be positive Decimals (> $0.00).
    - allowed_merchants must be a non-empty comma-separated list with at least one merchant.

    Returns:
        (is_valid: bool, error_message: Optional[str])
    """
    rt = (rule_type or "").strip().lower()
    val = (condition_value or "").strip()

    if not val:
        return False, f"Rule '{rule_type}' condition value cannot be empty."

    if rt in ("max_per_purchase", "monthly_budget", "approval_threshold"):
        try:
            dec_val = Decimal(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if dec_val.is_nan() or dec_val.is_infinite():
                return False, f"Rule '{rule_type}' must be a finite number."
            if dec_val <= Decimal("0.00"):
                return False, f"Rule '{rule_type}' must be greater than $0.00 (got ${val})."
            return True, None
        except (InvalidOperation, ValueError, TypeError):
            return False, f"Rule '{rule_type}' value '{val}' is not a valid decimal number."

    elif rt == "allowed_merchants":
        merchants = [m.strip() for m in val.split(",") if m.strip()]
        if not merchants:
            return False, "allowed_merchants must contain at least one valid merchant name."
        return True, None

    return True, None


def get_policy_rules(db_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Retrieve all active policy rules from SQLite indexed by rule_type.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, rule_name, description, rule_type, condition_value, action, is_active, created_at
        FROM rules
        WHERE is_active = 1
        ORDER BY id ASC
        """
    )
    rules_dict = {}
    for row in cursor.fetchall():
        rules_dict[row["rule_type"].strip().lower()] = dict(row)
    conn.close()
    return rules_dict


def update_policy_rule(
    rule_type: str, new_value: str, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate and update an existing allowance rule in SQLite.
    Rule changes apply to future decisions only.
    """
    is_valid, err_msg = validate_rule_value(rule_type, new_value)
    if not is_valid:
        raise ValueError(err_msg)

    rt = rule_type.strip().lower()
    clean_val = new_value.strip()

    # Format numeric rules to 2 decimal places if applicable
    if rt in ("max_per_purchase", "monthly_budget", "approval_threshold"):
        clean_val = str(Decimal(clean_val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE rules SET condition_value = ? WHERE rule_type = ?",
        (clean_val, rt),
    )
    if cursor.rowcount == 0:
        conn.close()
        raise ValueError(f"No active rule found with rule_type '{rule_type}'.")

    conn.commit()
    conn.close()
    return {"rule_type": rt, "new_value": clean_val}

