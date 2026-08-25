"""Full-board confidence completion gate.

Prevents optimization and board-wide "promising" count claims until every
model-eligible row has a terminal confidence category.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Iterable

CONFIDENCE_CATEGORIES = frozenset({
    "HIGH_CONFIDENCE",
    "MEDIUM_CONFIDENCE",
    "LOW_CONFIDENCE",
    "NO_CONFIDENCE",
    "GLOBAL_BLOCKER",
})
FULL_BOARD_CONFIDENCE_PASS = "FULL_BOARD_CONFIDENCE_PASS"
FULL_BOARD_RUN_INCOMPLETE = "FULL_BOARD_RUN_INCOMPLETE"

_HIGH_LABELS = frozenset({"HIGH_CONFIDENCE", "FINAL_CONFIDENCE_HIGH"})
_MEDIUM_LABELS = frozenset({"MEDIUM_CONFIDENCE", "FINAL_CONFIDENCE_MEDIUM"})
_LOW_LABELS = frozenset({"LOW_CONFIDENCE", "FINAL_CONFIDENCE_LOW"})
_NO_CONFIDENCE_LABELS = frozenset({
    "NO_CONFIDENCE",
    "CONFIDENCE_UNOBTAINABLE",
    "MODEL_UNAVAILABLE",
    "DATA_UNOBTAINABLE",
    "DATA_INSUFFICIENT",
})
_GLOBAL_BLOCKER_LABELS = frozenset({
    "GLOBAL_BLOCKER",
    "GOVERNANCE_BLOCKED",
    "RUN_BLOCKED",
})


def _tokens(row: dict[str, Any]) -> set[str]:
    values: list[Any] = [
        row.get("confidence_category"),
        row.get("confidence_decision"),
        row.get("terminal_label"),
        row.get("classification"),
        row.get("final_approval_blocker"),
    ]
    values.extend(row.get("blockers") or [])
    tokens: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("code") or value.get("label") or ""
        text = str(value or "").upper()
        tokens.update(part.strip() for part in text.replace(":", " ").split() if part.strip())
        if text:
            tokens.add(text)
    return tokens


def confidence_category(row: dict[str, Any]) -> str | None:
    """Return one governed confidence category, or None when never assessed."""
    tokens = _tokens(row)
    if row.get("global_blocker") is True or tokens & _GLOBAL_BLOCKER_LABELS:
        return "GLOBAL_BLOCKER"
    if tokens & _HIGH_LABELS:
        return "HIGH_CONFIDENCE"
    if tokens & _MEDIUM_LABELS:
        return "MEDIUM_CONFIDENCE"
    if tokens & _LOW_LABELS:
        return "LOW_CONFIDENCE"
    if tokens & _NO_CONFIDENCE_LABELS:
        return "NO_CONFIDENCE"

    for key in (
        "calibrated_probability_lower_bound",
        "conservative_probability",
        "hit_probability",
        "calibrated_probability",
        "model_probability",
    ):
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            probability = float(value)
            if isfinite(probability) and 0.0 < probability < 1.0:
                if probability >= 0.60:
                    return "HIGH_CONFIDENCE"
                if probability >= 0.55:
                    return "MEDIUM_CONFIDENCE"
                return "LOW_CONFIDENCE"
    return None


def audit_full_board_confidence(
    rows: Iterable[dict[str, Any]],
    *,
    discovered_count: int,
    reconciliation_passed: bool,
) -> dict[str, Any]:
    """Audit confidence coverage and control optimization/reporting claims."""
    materialized = list(rows)
    categorized: dict[str, int] = {name: 0 for name in sorted(CONFIDENCE_CATEGORIES)}
    unaccounted_ids: list[str] = []

    for index, row in enumerate(materialized):
        category = confidence_category(row)
        if category is None:
            unaccounted_ids.append(str(
                row.get("canonical_selection_id")
                or row.get("selection_id")
                or f"ROW_{index}"
            ))
        else:
            categorized[category] += 1

    global_blockers = categorized["GLOBAL_BLOCKER"]
    model_eligible_rows = max(0, discovered_count - global_blockers)
    confidence_accounted_rows = sum(
        categorized[name]
        for name in ("HIGH_CONFIDENCE", "MEDIUM_CONFIDENCE", "LOW_CONFIDENCE", "NO_CONFIDENCE")
    )
    modeled_confidence_rows = sum(
        categorized[name]
        for name in ("HIGH_CONFIDENCE", "MEDIUM_CONFIDENCE", "LOW_CONFIDENCE")
    )
    complete = (
        reconciliation_passed
        and len(materialized) == discovered_count
        and not unaccounted_ids
        and confidence_accounted_rows == model_eligible_rows
    )
    status = FULL_BOARD_CONFIDENCE_PASS if complete else FULL_BOARD_RUN_INCOMPLETE

    return {
        "status": status,
        "discovered_rows": discovered_count,
        "terminal_rows_seen": len(materialized),
        "model_eligible_rows": model_eligible_rows,
        "confidence_accounted_rows": confidence_accounted_rows,
        "modeled_confidence_rows": modeled_confidence_rows,
        "confidence_categories": categorized,
        "unaccounted_ids": unaccounted_ids,
        "reconciliation_passed": bool(reconciliation_passed),
        "optimizer_allowed": complete,
        "promising_count_claim_allowed": complete,
        "can_execute": False,
    }
