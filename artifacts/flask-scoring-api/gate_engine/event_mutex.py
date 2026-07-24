"""
gate_engine/event_mutex.py
Stage 2 — Item 2: Event mutex validator

Enforces the rule: zero or one final selection per event_key.

If two opposing sides on the same event_key are both in a final-selection
label the entire run is invalidated and the conflict is stored with a
machine-readable reason code.

Prerequisite: rows must have "event_key" populated.
Use gate_engine.event_identity.annotate_rows_with_event_key() first.

IMPORTANT: can_execute is always False.
  This module never produces or influences live orders.
"""
from __future__ import annotations

from typing import Any

# ── Safety constants ──────────────────────────────────────────────────────────
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
CAN_EXECUTE    = False

# ── Labels considered "final selections" for mutex purposes ───────────────────
FINAL_SELECTION_LABELS = frozenset({
    "FINAL_APPROVED",
    "MONEY_QUALIFIED",
    "LLP_APPROVED",
    "LLP_PLAYABLE",
})

# ── Run-level invalidation code ───────────────────────────────────────────────
RUN_INVALID_CODE = "RUN_INVALID_OPPOSING_SIDES"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _label(row: dict[str, Any]) -> str:
    return (row.get("terminal_label") or row.get("final_label") or "").upper()


def _is_final_selection(row: dict[str, Any]) -> bool:
    return _label(row) in FINAL_SELECTION_LABELS


def _side_key(row: dict[str, Any]) -> str:
    """Normalised side string for a row (used to detect opposing-side conflict)."""
    return (row.get("side") or row.get("selected_side") or "").lower().strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def validate_event_mutex(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Scan all rows for opposing-side conflicts on the same event_key.

    A conflict occurs when:
      1. Two or more rows share the same event_key.
      2. All such rows carry a final-selection label.
      3. Their normalised side/selected_side values differ AND are non-empty.

    When a conflict is found:
      - The run is invalidated (run_valid=False).
      - Each conflicting row gets a blockers entry and _run_invalid=True.
      - The conflict is recorded with a human-readable reason.

    Parameters
    ----------
    rows : list of row dicts (mutated in-place on conflict).

    Returns
    -------
    {
      passed               bool   — True when no conflicts exist
      run_valid            bool   — False when any conflict exists
      invalidation_code    str | None  — RUN_INVALID_OPPOSING_SIDES or None
      conflicts            list[dict]
      checked_rows         int
      final_selection_rows int
      can_execute          bool   — always False
      execution_rule       str
    }

    Each conflict dict:
    {
      event_key     str
      sides         list[str]   — all conflicting side values
      row_indices   list[int]
      labels        list[str]
      reason        str
    }
    """
    # Group final-selection rows by event_key
    by_event: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    final_count = 0

    for idx, row in enumerate(rows):
        if not _is_final_selection(row):
            continue
        final_count += 1
        key = row.get("event_key")
        if not key:
            continue
        by_event.setdefault(key, []).append((idx, row))

    conflicts: list[dict[str, Any]] = []

    for event_key, selections in by_event.items():
        if len(selections) < 2:
            continue

        # Bucket selections by side
        sides_seen: dict[str, list[tuple[int, dict]]] = {}
        for idx, row in selections:
            sk = _side_key(row)
            sides_seen.setdefault(sk, []).append((idx, row))

        # Conflict: two or more distinct non-empty sides both have final labels
        non_empty = {s: v for s, v in sides_seen.items() if s}
        if len(non_empty) < 2:
            continue

        all_sides   = list(non_empty.keys())
        all_indices = [idx for idx, _ in selections]
        all_labels  = [_label(row) for _, row in selections]

        conflicts.append({
            "event_key":   event_key,
            "sides":       all_sides,
            "row_indices": all_indices,
            "labels":      all_labels,
            "reason": (
                f"Opposing sides {all_sides} are both final on "
                f"event_key={event_key}. "
                "Zero or one final selection is allowed per event. "
                "Run is invalid — resubmit with a single side selected."
            ),
        })

    passed = len(conflicts) == 0

    # Annotate conflicting rows
    if not passed:
        conflict_indices = {idx for c in conflicts for idx in c["row_indices"]}
        for idx in conflict_indices:
            row = rows[idx]
            row.setdefault("blockers", []).append(
                f"EVENT_MUTEX:{RUN_INVALID_CODE}:"
                f"event_key={row.get('event_key')}"
            )
            row["_run_invalid"]        = True
            row["_run_invalid_reason"] = RUN_INVALID_CODE

    return {
        "passed":               passed,
        "run_valid":            passed,
        "invalidation_code":    None if passed else RUN_INVALID_CODE,
        "conflicts":            conflicts,
        "checked_rows":         len(rows),
        "final_selection_rows": final_count,
        "can_execute":          CAN_EXECUTE,
        "execution_rule":       EXECUTION_RULE,
    }


def get_event_mutex_summary(result: dict[str, Any]) -> str:
    """Return a one-line human-readable summary of a validate_event_mutex result."""
    if result["passed"]:
        return (
            f"EVENT_MUTEX:PASS — "
            f"{result['final_selection_rows']} final selections, "
            f"{result['checked_rows']} rows, no opposing-side conflicts."
        )
    n = len(result["conflicts"])
    keys = [c["event_key"] for c in result["conflicts"]]
    return (
        f"EVENT_MUTEX:FAIL — {n} opposing-side conflict(s) detected. "
        f"Run is INVALID ({RUN_INVALID_CODE}). "
        f"Event keys: {keys}"
    )
