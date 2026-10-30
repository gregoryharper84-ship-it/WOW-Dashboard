"""Strict lowest-terminal reduction for V17 Daily row wrappers.

The Daily wrapper is an aggregator, not a terminal authority. It may never
report a softer row status than the strictest terminal its own stages already
produced. A hard inner rejection (``pick_rejected``) reported by the outer
wrapper as ``HELD`` is the forbidden ``RUN_INVALID_TERMINAL_UPGRADE`` condition:
``HELD`` asserts an unresolved row that could still resolve, while a rejection
is a decided terminal.

One deliberate exception preserves existing approval semantics. Two-sided props
assess both directions independently, and the complement of an approved side is
normally rejected by construction (a strongly favoured MORE implies a rejected
LESS). Collapsing such a row to ``REJECTED`` would destroy every valid approved
row, so an approved stage keeps the row ``COMPLETED``. That exception is scoped
to a genuinely approved stage and is recorded in the reduction audit; it never
softens a rejection into a hold.

This module never scores a probability, never mutates a stage terminal, and
never authorizes execution.
"""
from __future__ import annotations

from typing import Any, Iterable

CAN_EXECUTE = False

RUN_INVALID_TERMINAL_UPGRADE = "RUN_INVALID_TERMINAL_UPGRADE"

# Ascending rank == stricter/lower terminal. "Lowest terminal wins" takes the
# minimum rank across the row's own stages.
TERMINAL_RANK: dict[str, int] = {
    "INVALID": 0,
    "PURGED": 1,
    "REJECTED": 2,
    "HELD": 3,
    "COMPLETED": 4,
}

DEFAULT_ROW_STATUS = "HELD"

# Only an explicit slate purge downgrades past REJECTED. Every other rejection
# terminal reduces to REJECTED so the hard/soft distinction is never lost.
_PURGE_TERMINAL_LABELS = {"SLATE_PURGE"}


def _label(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().upper()
    return ""


def _qualification(payload: dict[str, Any]) -> dict[str, Any]:
    qualification = payload.get("probability_qualification")
    return qualification if isinstance(qualification, dict) else {}


def _truthy(payload: dict[str, Any], qualification: dict[str, Any], key: str) -> bool:
    return payload.get(key) is True or qualification.get(key) is True


def classify_stage_status(payload: Any) -> str:
    """Classify one scored stage into its native Daily terminal.

    Approval requires both publication and rank eligibility, matching the
    pre-existing Daily contract. A rejection is read from the controlling
    model's own ``pick_rejected`` receipt, never re-derived here.
    """
    if not isinstance(payload, dict):
        return DEFAULT_ROW_STATUS

    qualification = _qualification(payload)
    terminal_label = _label(payload, "terminal_label") or _label(qualification, "terminal_label")

    if terminal_label in _PURGE_TERMINAL_LABELS:
        return "PURGED"
    if _truthy(payload, qualification, "pick_rejected"):
        return "REJECTED"

    # Numeric model completion is not publication authority. Downstream wrappers
    # can carry stale approval flags while the controlling NFL certification
    # receipt explicitly imposes MODEL_QUALIFIED_HOLD. Its ceiling always wins.
    detail = payload.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    qualified_detail = qualification.get("detail")
    qualified_detail = qualified_detail if isinstance(qualified_detail, dict) else {}
    # Inspect every supported controlling source, including nested qualified
    # receipts. A wrapper cannot override a non-PASS NFL certification stage
    # by flattening the outer publication flags. An explicitly present but
    # malformed/empty receipt also fails closed; absence of an optional
    # receipt is not itself a HOLD for unrelated sports.
    for controlling in (payload, qualification, detail, qualified_detail):
        ceiling = _label(controlling, "terminal_ceiling")
        label = _label(controlling, "terminal_label")
        if ceiling and ceiling != "FINAL_APPROVED":
            return DEFAULT_ROW_STATUS
        if label in {"MODEL_QUALIFIED_HOLD", "REGISTRY_INFRASTRUCTURE_BLOCKED"}:
            return DEFAULT_ROW_STATUS
        if "nfl_stage_receipt" in controlling:
            nfl_receipt = controlling["nfl_stage_receipt"]
            if not isinstance(nfl_receipt, dict) or _label(nfl_receipt, "status") != "PASS":
                return DEFAULT_ROW_STATUS

    publishable = payload.get("probability_publishable") is True
    rank_eligible = (
        payload.get("rank_eligible") is True
        or payload.get("probability_rank_eligible") is True
        or qualification.get("rank_eligible") is True
        or qualification.get("probability_rank_eligible") is True
    )
    if publishable and rank_eligible:
        return "COMPLETED"
    return DEFAULT_ROW_STATUS


def rank_of(status: str) -> int:
    return TERMINAL_RANK.get(str(status or "").strip().upper(), TERMINAL_RANK[DEFAULT_ROW_STATUS])


def lowest_stage_terminal(statuses: Iterable[str]) -> str:
    """Return the strictest (lowest-ranked) terminal among the given stages."""
    collected = [str(status or "").strip().upper() for status in statuses]
    collected = [status for status in collected if status in TERMINAL_RANK]
    if not collected:
        return DEFAULT_ROW_STATUS
    return min(collected, key=rank_of)


def reduce_row_terminal(stage_statuses: Iterable[str]) -> dict[str, Any]:
    """Reduce stage terminals to one row terminal that never upgrades a stage.

    Returns the audit record proving the reduction: the stage ladder, the
    lowest stage terminal, the final terminal, and whether the approved-stage
    exception was applied.
    """
    statuses = [str(status or "").strip().upper() or DEFAULT_ROW_STATUS for status in stage_statuses]
    normalized = [status if status in TERMINAL_RANK else DEFAULT_ROW_STATUS for status in statuses]
    lowest = lowest_stage_terminal(normalized)
    approved_stage_present = "COMPLETED" in normalized
    # The exception is only for a genuinely approved direction and its
    # independently rejected complement (e.g. MORE approved, LESS rejected).
    # A pending/invalid/purged controlling stage must never be upgraded.
    complement_only = all(stage in {"COMPLETED", "REJECTED"} for stage in normalized)
    approved_exception = approved_stage_present and complement_only and lowest == "REJECTED"
    final_terminal = "COMPLETED" if approved_exception else lowest
    return {
        "stage_terminals": normalized,
        "lowest_stage_terminal": lowest,
        "final_terminal": final_terminal,
        "approved_stage_exception_applied": bool(approved_exception),
        "terminal_upgraded_from_rejection": False,
        "reduction_rule": "FINAL_TERMINAL_EQUALS_LOWEST_STAGE_TERMINAL_EXCEPT_APPROVED_STAGE",
        "can_execute": False,
    }


def assert_no_terminal_upgrade(reduction: dict[str, Any]) -> dict[str, Any]:
    """Fail closed if a hard rejection was softened into a hold.

    A softer-than-lowest final terminal is only ever permitted for a genuinely
    approved stage. Anything else is ``RUN_INVALID_TERMINAL_UPGRADE``.
    """
    final_terminal = str(reduction.get("final_terminal") or DEFAULT_ROW_STATUS).upper()
    declared_lowest = str(reduction.get("lowest_stage_terminal") or DEFAULT_ROW_STATUS).upper()
    if declared_lowest not in TERMINAL_RANK:
        declared_lowest = DEFAULT_ROW_STATUS
    audited = dict(reduction)
    stages = [str(value or "").strip().upper() for value in audited.get("stage_terminals") or []]
    normalized = [stage if stage in TERMINAL_RANK else DEFAULT_ROW_STATUS for stage in stages]
    actual_lowest = lowest_stage_terminal(normalized)
    audit_consistent = (
        bool(normalized)
        and normalized == stages
        and declared_lowest == actual_lowest
    )
    # A forged audit must not claim a softer lowest stage than its own ladder.
    # Conversely, a forged stricter low stage cannot be overridden merely by
    # presenting a COMPLETED-only ladder. Preserve the stricter of the two.
    strictest = min((declared_lowest, actual_lowest), key=rank_of)
    approved_complement = (
        audit_consistent
        and final_terminal == "COMPLETED"
        and bool(audited.get("approved_stage_exception_applied"))
        and actual_lowest == "REJECTED"
        and "COMPLETED" in normalized
        and "REJECTED" in normalized
        and all(stage in {"COMPLETED", "REJECTED"} for stage in normalized)
    )
    if not audit_consistent or (rank_of(final_terminal) > rank_of(strictest) and not approved_complement):
        audited["terminal_upgraded_from_rejection"] = True
        audited["blockers"] = list(dict.fromkeys([*(audited.get("blockers") or []), RUN_INVALID_TERMINAL_UPGRADE]))
        audited["final_terminal"] = strictest
    return audited


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_ROW_STATUS",
    "RUN_INVALID_TERMINAL_UPGRADE",
    "TERMINAL_RANK",
    "assert_no_terminal_upgrade",
    "classify_stage_status",
    "lowest_stage_terminal",
    "rank_of",
    "reduce_row_terminal",
]
