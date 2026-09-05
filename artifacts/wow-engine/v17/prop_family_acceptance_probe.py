"""Read-only V17 production acceptance audit helpers for certified prop families.

This module does not score, publish, or execute wagers. It only classifies whether
an already-certified prop family has reached production proof based on immutable
ledger evidence. Runtime scoring remains owned by the canonical WOW prop path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class FamilyAcceptance:
    stat_type: str
    evidence_pass_n: int
    prediction_n: int
    publishable_n: int
    bounded_n: int
    status: str


def classify_family_acceptance(
    *,
    stat_type: str,
    evidence_pass_n: int,
    prediction_n: int,
    publishable_n: int,
    bounded_n: int,
) -> FamilyAcceptance:
    """Classify ledger proof without weakening any model or evidence gate."""
    counts = (evidence_pass_n, prediction_n, publishable_n, bounded_n)
    if any((not isinstance(value, int) or value < 0) for value in counts):
        raise ValueError("acceptance counts must be non-negative integers")

    if evidence_pass_n == 0:
        status = "BLOCKED_NO_PASS_EVIDENCE"
    elif prediction_n == 0:
        status = "BLOCKED_NO_GOVERNED_PREDICTION"
    elif publishable_n == 0 or bounded_n == 0:
        status = "BLOCKED_NO_PUBLISHABLE_BOUNDED_OUTPUT"
    else:
        status = "PRODUCTION_PROVEN"

    return FamilyAcceptance(
        stat_type=stat_type,
        evidence_pass_n=evidence_pass_n,
        prediction_n=prediction_n,
        publishable_n=publishable_n,
        bounded_n=bounded_n,
        status=status,
    )


def summarize_rows(rows: Iterable[dict[str, Any]]) -> dict[str, FamilyAcceptance]:
    """Normalize query rows from a ledger audit into deterministic classifications."""
    out: dict[str, FamilyAcceptance] = {}
    for row in rows:
        stat_type = str(row["stat_type"])
        out[stat_type] = classify_family_acceptance(
            stat_type=stat_type,
            evidence_pass_n=int(row.get("evidence_pass_n") or 0),
            prediction_n=int(row.get("prediction_n") or 0),
            publishable_n=int(row.get("publishable_n") or 0),
            bounded_n=int(row.get("bounded_n") or 0),
        )
    return out
