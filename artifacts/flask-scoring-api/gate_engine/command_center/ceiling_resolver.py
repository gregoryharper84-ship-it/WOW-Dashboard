"""
gate_engine/command_center/ceiling_resolver.py
WOW Sports Intelligence Command Center — Phase 1

Monotonic terminal ceiling enforcement.

Rule: the ceiling can only move to a MORE restrictive label (higher rank).
A downstream pass that tries to set a LESS restrictive label is silently
dropped and CC:UPSTREAM_BLOCKER_PRESERVED is appended to cc_blockers.

This is a stronger guarantee than route_registry.enforce_route_completion():
  - route_registry: lowers qualifying labels when required gates are absent
  - ceiling_resolver: prevents ANY downstream from upgrading past ANY upstream
    blocker, including CC-namespaced blockers set by the orchestrator itself

can_execute = False (unconditional)
"""
from __future__ import annotations

from typing import Any

from .cc_labels import (
    CAN_EXECUTE,
    ceiling_rank,
    CC_CEILING_ENFORCED,
    CC_UPSTREAM_BLOCKER_PRESERVED,
)


# ---------------------------------------------------------------------------
# Core ceiling primitives
# ---------------------------------------------------------------------------

def resolve_ceiling(current: str | None, candidate: str | None) -> str | None:
    """
    Return whichever label is MORE restrictive (higher rank).
    If both are None → None.
    If one is None → the other.
    If equal → current (no change).
    """
    if candidate is None:
        return current
    if current is None:
        return candidate
    if ceiling_rank(candidate) >= ceiling_rank(current):
        return candidate
    return current   # current is more restrictive — keep it


def apply_ceiling_to_row(
    row: dict[str, Any],
    proposed_label: str | None,
    source: str = "orchestrator",
) -> bool:
    """
    Apply a proposed label to row["cc_ceiling"] following monotonic rules.
    If the proposed label is LESS restrictive than the current ceiling:
      - row["cc_ceiling"] is unchanged
      - CC:UPSTREAM_BLOCKER_PRESERVED is appended to row["cc_blockers"]
      - returns False (ceiling not changed)
    If more restrictive or current is None:
      - row["cc_ceiling"] is updated
      - CC:CEILING_ENFORCED is appended
      - returns True (ceiling changed)
    """
    if proposed_label is None:
        return False

    current = row.get("cc_ceiling")

    if current is not None and ceiling_rank(proposed_label) < ceiling_rank(current):
        # Proposed is LESS restrictive — block the upgrade
        row.setdefault("cc_blockers", []).append(
            f"{CC_UPSTREAM_BLOCKER_PRESERVED}:upstream={current}:rejected={proposed_label}"
        )
        return False

    row["cc_ceiling"] = proposed_label
    row.setdefault("cc_blockers", []).append(
        f"{CC_CEILING_ENFORCED}:source={source}:label={proposed_label}"
    )
    return True


# ---------------------------------------------------------------------------
# Batch enforcement
# ---------------------------------------------------------------------------

def enforce_batch_ceilings(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    For each row, resolve the final_label as the most restrictive of:
      1. engine_label     (from the controlling engine)
      2. cc_ceiling       (set by orchestrator shared services)
      3. Any CC blocker labels already in cc_blockers

    Stamps each row with:
      row["final_label"]  — the resolved ceiling label
      row["can_execute"]  — always False

    Returns a summary dict.
    """
    enforced_count = 0

    for row in rows:
        engine_label = row.get("engine_label")
        cc_ceiling   = row.get("cc_ceiling")

        # Build candidate labels: engine result + cc_ceiling + worst cc_blocker
        candidates = [
            label for label in [engine_label, cc_ceiling]
            if label is not None
        ]

        # Extract any label-like strings from cc_blockers (only labels in CEILING_ORDER)
        from .cc_labels import CEILING_ORDER as _ORDER
        _order_set = frozenset(_ORDER)
        for blocker in (row.get("cc_blockers") or []):
            # Blockers are typically descriptive strings (not pure labels);
            # extract embedded labels if the blocker IS a label
            if blocker in _order_set:
                candidates.append(blocker)

        # Resolve to the most restrictive
        resolved: str | None = None
        for c in candidates:
            resolved = resolve_ceiling(resolved, c)

        if resolved and resolved != row.get("final_label"):
            row["final_label"] = resolved
            enforced_count += 1
        elif resolved:
            row["final_label"] = resolved

        # Governance invariant — always stamped
        row["can_execute"] = CAN_EXECUTE

    return {
        "rows_processed":  len(rows),
        "ceilings_enforced": enforced_count,
        "can_execute":     CAN_EXECUTE,
    }


def check_no_upstream_erasure(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Validate that no row's final_label is LESS restrictive than its cc_ceiling.
    Returns list of rows that violate the monotonic rule (should be empty).
    """
    violations = []
    for row in rows:
        final    = row.get("final_label")
        cc_ceil  = row.get("cc_ceiling")
        if cc_ceil and final:
            if ceiling_rank(final) < ceiling_rank(cc_ceil):
                violations.append({
                    "candidate_id": row.get("candidate_id"),
                    "final_label":  final,
                    "cc_ceiling":   cc_ceil,
                    "violation":    "FINAL_LABEL_LESS_RESTRICTIVE_THAN_CC_CEILING",
                })
    return violations
