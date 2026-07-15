"""
slip_structure.py
Reject filler legs and bad card construction.
Rules: no same-player double-dip, no same-game overload, archetype diversity check.

WOW-PATCH-2026-07-15 additions:
  Prop Reliability Freeze (2026-07-15 through 2026-07-22 inclusive)
    - Core 1-2 legs (unchanged)
    - Flex: maximum 3 legs
    - Power 4-6 legs: REJECT_BAD_STRUCTURE
    - Maximum 2 legs from one event/script per slip
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .labels import PropLabel
from .governance import is_in_prop_reliability_freeze


MAX_SAME_PLAYER_IN_SLIP   = 1
MAX_SAME_GAME_IN_SLIP     = 2
MAX_SAME_ARCHETYPE        = 2

# Prop Reliability Freeze limits
FREEZE_MAX_FLEX_LEGS      = 3
FREEZE_MAX_POWER_LEGS     = 3   # 4-6 → REJECT_BAD_STRUCTURE
FREEZE_MAX_LEGS_PER_EVENT = 2   # max 2 legs from same event/script


def run_single(row: dict[str, Any]) -> dict[str, Any]:
    """
    Validate individual row structure (no per-slip context needed).
    Detects degenerate rows: no line, no player, unparseable direction.
    """
    errors: list[str] = []

    if row.get("line") is None:
        errors.append("NO_LINE")
    if not row.get("player"):
        errors.append("NO_PLAYER")
    if not row.get("prop_type"):
        errors.append("NO_PROP_TYPE")
    if not row.get("direction"):
        errors.append("NO_DIRECTION")

    passed = len(errors) == 0
    if not passed:
        row["blockers"].append(f"SLIP_STRUCTURE:BAD_ROW:{','.join(errors)}")

    result = {
        "passed":            passed,
        "row_errors":        errors,
        "slip_context_check": "PENDING",
    }
    row["gates"]["slip_structure"] = result
    return row


def run_slip(rows: list[dict[str, Any]],
             slip_type: str = "flex",
             as_of: str | None = None) -> list[dict[str, Any]]:
    """
    Validate slip-level construction across all rows.
    Mutates rows in-place adding slip_structure gate results.

    slip_type: "core" | "flex" | "power"  (default "flex")
    as_of:     YYYY-MM-DD string for freeze window check (default today)
    """
    player_counts:    Counter = Counter()
    game_counts:      Counter = Counter()
    archetype_counts: Counter = Counter()

    for row in rows:
        player    = (row.get("player") or "").lower()
        game      = (row.get("game") or "").lower()
        archetype = _archetype(row.get("prop_type") or "")
        player_counts[player]       += 1
        game_counts[game]           += 1
        archetype_counts[archetype] += 1

    freeze = is_in_prop_reliability_freeze(as_of)
    total_legs = len(rows)
    slip_type_norm = slip_type.lower()

    # --- Prop Reliability Freeze: slip-level structural checks ---
    slip_level_errors: list[str] = []

    if freeze:
        if slip_type_norm == "power" and total_legs > FREEZE_MAX_POWER_LEGS:
            slip_level_errors.append(
                f"REJECT_BAD_STRUCTURE:POWER_{total_legs}_LEGS_DURING_FREEZE"
                f"(max {FREEZE_MAX_POWER_LEGS})"
            )
        if slip_type_norm == "flex" and total_legs > FREEZE_MAX_FLEX_LEGS:
            slip_level_errors.append(
                f"REJECT_BAD_STRUCTURE:FLEX_{total_legs}_LEGS_DURING_FREEZE"
                f"(max {FREEZE_MAX_FLEX_LEGS})"
            )
        # Max 2 legs from same event/script
        for game, count in game_counts.items():
            if game and count > FREEZE_MAX_LEGS_PER_EVENT:
                slip_level_errors.append(
                    f"REJECT_BAD_STRUCTURE:SAME_EVENT_{count}x:{game}"
                )

    for row in rows:
        player    = (row.get("player") or "").lower()
        game      = (row.get("game") or "").lower()
        archetype = _archetype(row.get("prop_type") or "")
        existing  = row.get("gates", {}).get("slip_structure", {})
        slip_errors: list[str] = []

        if player_counts[player] > MAX_SAME_PLAYER_IN_SLIP:
            slip_errors.append(f"SAME_PLAYER_OVERLOAD:{player_counts[player]}x")
        if game and game_counts[game] > MAX_SAME_GAME_IN_SLIP:
            slip_errors.append(f"SAME_GAME_OVERLOAD:{game_counts[game]}x")
        if archetype_counts[archetype] > MAX_SAME_ARCHETYPE:
            slip_errors.append(f"ARCHETYPE_OVERLOAD:{archetype}:{archetype_counts[archetype]}x")

        # Propagate slip-level freeze errors to every row
        slip_errors.extend(slip_level_errors)

        passed = len(slip_errors) == 0
        if not passed:
            row["blockers"].append(f"SLIP_STRUCTURE:BAD_CARD:{','.join(slip_errors)}")
            # Mark terminal label for hard freeze violations
            for err in slip_errors:
                if "REJECT_BAD_STRUCTURE" in err:
                    if row.get("terminal_label") is None:
                        row["terminal_label"] = PropLabel.REJECT_BAD_STRUCTURE.value

        row["gates"]["slip_structure"] = {
            **existing,
            "passed":             existing.get("passed", True) and passed,
            "slip_context_check": "COMPLETE",
            "slip_errors":        slip_errors,
            "freeze_active":      freeze,
            "slip_type":          slip_type_norm,
            "total_legs":         total_legs,
        }

    return rows


def check_freeze_power(rows: list[dict[str, Any]],
                       as_of: str | None = None) -> dict[str, Any]:
    """
    Standalone freeze check for Power slip leg count.
    Returns {passed, label, detail} without mutating rows.

    Used by the /gate-engine/run route to check slip-level structure
    before pipeline processing.
    """
    freeze = is_in_prop_reliability_freeze(as_of)
    if not freeze:
        return {"passed": True, "freeze_active": False}

    total = len(rows)
    if total > FREEZE_MAX_POWER_LEGS:
        return {
            "passed":       False,
            "freeze_active": True,
            "label":        PropLabel.REJECT_BAD_STRUCTURE.value,
            "detail": (
                f"Power slip has {total} legs; "
                f"Prop Reliability Freeze limits Power to "
                f"{FREEZE_MAX_POWER_LEGS} legs (2026-07-15 through 2026-07-22)"
            ),
        }
    return {"passed": True, "freeze_active": True}


def _archetype(prop_type: str) -> str:
    pt = prop_type.lower()
    if "point" in pt:    return "scoring"
    if "rebound" in pt:  return "rebound"
    if "assist" in pt:   return "assist"
    if "steal" in pt:    return "steal"
    if "block" in pt:    return "block"
    if "hit" in pt or "rbi" in pt or "home" in pt: return "mlb_batting"
    if "strikeout" in pt or "pitch" in pt:         return "mlb_pitching"
    if "shot" in pt or "goal" in pt:               return "soccer"
    return "other"
