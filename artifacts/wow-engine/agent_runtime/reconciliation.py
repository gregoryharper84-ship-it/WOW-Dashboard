"""Row reconciliation (packet sections 1 and 25):

    rows_in = rows_completed + rows_held + rows_rejected

Zero survivors is valid — an empty completed bucket with a nonzero held or
rejected bucket is not itself unbalanced; only an arithmetic mismatch is.
"""
from __future__ import annotations

from dataclasses import dataclass

BALANCED = "BALANCED"
UNBALANCED = "UNBALANCED"

# Ceilings that count as "completed" for reconciliation purposes: a candidate
# reached a real, non-blocked terminal decision. Everything else that reached
# a terminal ceiling (holds, rejects, unavailable) is "held" or "rejected" —
# see classify_ceiling().
_COMPLETED_CEILINGS = frozenset({"VERIFIED", "MARKET_VERIFIED", "FINAL_APPROVED", "WATCH"})
_REJECTED_CEILINGS = frozenset({"REJECT", "MODEL_UNAVAILABLE", "NO_SPECIALIST_COVERAGE"})


def classify_ceiling(ceiling: str) -> str:
    """Bucket one candidate's terminal ceiling into completed/held/rejected
    for reconciliation. Anything not explicitly classified as completed or
    rejected is held, by design — an unrecognized or advisory-only ceiling
    must not silently count as either a success or a rejection."""
    if ceiling in _COMPLETED_CEILINGS:
        return "completed"
    if ceiling in _REJECTED_CEILINGS:
        return "rejected"
    return "held"


@dataclass(frozen=True)
class ReconciliationResult:
    rows_in: int
    rows_completed: int
    rows_held: int
    rows_rejected: int
    status: str


def reconcile(*, rows_in: int, rows_completed: int, rows_held: int, rows_rejected: int) -> ReconciliationResult:
    for name, value in (
        ("rows_in", rows_in), ("rows_completed", rows_completed),
        ("rows_held", rows_held), ("rows_rejected", rows_rejected),
    ):
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer, got {value!r}")

    status = BALANCED if rows_in == (rows_completed + rows_held + rows_rejected) else UNBALANCED
    return ReconciliationResult(
        rows_in=rows_in, rows_completed=rows_completed,
        rows_held=rows_held, rows_rejected=rows_rejected, status=status,
    )


def reconcile_from_ceilings(*, rows_in: int, terminal_ceilings: list[str]) -> ReconciliationResult:
    """Convenience wrapper: classify a list of candidates' terminal ceilings
    and reconcile against the discovered row count in one call."""
    completed = held = rejected = 0
    for ceiling in terminal_ceilings:
        bucket = classify_ceiling(ceiling)
        if bucket == "completed":
            completed += 1
        elif bucket == "rejected":
            rejected += 1
        else:
            held += 1
    return reconcile(rows_in=rows_in, rows_completed=completed, rows_held=held, rows_rejected=rejected)
