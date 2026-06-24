"""
slip_structure.py
Reject filler legs and bad card construction.
Rules: no same-player double-dip, no same-game overload, archetype diversity check.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .labels import PropLabel

MAX_SAME_PLAYER_IN_SLIP   = 1
MAX_SAME_GAME_IN_SLIP     = 2
MAX_SAME_ARCHETYPE        = 2


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


def run_slip(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Validate slip-level construction across all rows.
    Mutates rows in-place adding slip_structure gate results.
    """
    player_counts: Counter = Counter()
    game_counts:   Counter = Counter()
    archetype_counts: Counter = Counter()

    for row in rows:
        player   = (row.get("player") or "").lower()
        game     = (row.get("game") or "").lower()
        archetype = _archetype(row.get("prop_type") or "")
        player_counts[player]     += 1
        game_counts[game]         += 1
        archetype_counts[archetype] += 1

    for row in rows:
        player   = (row.get("player") or "").lower()
        game     = (row.get("game") or "").lower()
        archetype = _archetype(row.get("prop_type") or "")
        existing = row.get("gates", {}).get("slip_structure", {})
        slip_errors: list[str] = []

        if player_counts[player] > MAX_SAME_PLAYER_IN_SLIP:
            slip_errors.append(f"SAME_PLAYER_OVERLOAD:{player_counts[player]}x")
        if game and game_counts[game] > MAX_SAME_GAME_IN_SLIP:
            slip_errors.append(f"SAME_GAME_OVERLOAD:{game_counts[game]}x")
        if archetype_counts[archetype] > MAX_SAME_ARCHETYPE:
            slip_errors.append(f"ARCHETYPE_OVERLOAD:{archetype}:{archetype_counts[archetype]}x")

        passed = len(slip_errors) == 0
        if not passed:
            row["blockers"].append(f"SLIP_STRUCTURE:BAD_CARD:{','.join(slip_errors)}")

        row["gates"]["slip_structure"] = {
            **existing,
            "passed":             existing.get("passed", True) and passed,
            "slip_context_check": "COMPLETE",
            "slip_errors":        slip_errors,
        }

    return rows


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
