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
    final_terminal = "COMPLETED" if approved_stage_present else lowest
    return {
        "stage_terminals": normalized,
        "lowest_stage_terminal": lowest,
        "final_terminal": final_terminal,
        "approved_stage_exception_applied": bool(approved_stage_present and lowest != "COMPLETED"),
        "terminal_upgraded_from_rejection": False,
        "reduction_rule": "FINAL_TERMINAL_EQUALS_LOWEST_STAGE_TERMINAL_EXCEPT_APPROVED_STAGE",
        "can_execute": False,
    }


def assert_no_terminal_upgrade(reduction: dict[str, Any]) -> dict[str, Any]:
    """Fail closed if a hard rejection was softened into a hold.

    A softer-than-lowest final terminal is only ever permitted for a genuinely
    approved stage. Anything else is ``RUN_INVALID_TERMINAL_UPGRADE``.
    """
    final_terminal = str(reduction.get("final_terminal") or DEFAULT_ROW_STATUS)
    lowest = str(reduction.get("lowest_stage_terminal") or DEFAULT_ROW_STATUS)
    audited = dict(reduction)
    if rank_of(final_terminal) > rank_of(lowest) and final_terminal != "COMPLETED":
        audited["terminal_upgraded_from_rejection"] = True
        audited["blockers"] = list(dict.fromkeys([*(audited.get("blockers") or []), RUN_INVALID_TERMINAL_UPGRADE]))
        audited["final_terminal"] = lowest
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
