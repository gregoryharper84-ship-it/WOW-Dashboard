"""
correlation_gate.py  —  Patch: Correlation EV Gate
WOW v16 / Patch 2026-06-27

The LLM may CLASSIFY correlation but may not invent exact joint probabilities.
External math is required for accurate same-game EV.

Correlation classification labels (CorrelationClass in labels.py):
  DIRECT_OVERLAP              — same player, one stat is a component of the other
                                e.g. Points AND Points+Rebounds+Assists on same slip
  SAME_PLAYER_COMPONENT       — same player, stats are components / subsets
                                e.g. Rebounds + (Rebounds+Assists)
  TEAMMATE_USAGE_NEGATIVE     — teammates competing for usage
                                e.g. two PGs on same team for assists
  TEAMMATE_USAGE_POSITIVE     — teammates benefiting from each other
  PACE_ENVIRONMENT_POSITIVE   — same game environment (pace/total) helps all legs
  BLOWOUT_SHARED_RISK         — all legs on same team or game, blowout kills all
  INJURY_ROLE_DEPENDENCY      — one player's role depends on another's health
  LOW_CORRELATION             — sufficiently independent legs
  UNKNOWN                     — classifier cannot determine correlation

Hard rules:
  1. No same-player overlapping stat props in same slip (DIRECT_OVERLAP → auto-reject)
  2. No Power Play approval when correlation is UNKNOWN for same-game props
  3. Flex slip EV must note that independent-leg math overstates true probability
     when TEAMMATE_USAGE_NEGATIVE or BLOWOUT_SHARED_RISK is detected

Usage:
  classify_legs(legs)         — returns correlation classification for a list of legs
  run_slip_gate(row, legs)    — applies gate to a row; blocks Power Play if needed
"""
from __future__ import annotations

import re
from typing import Any

from .labels import PropLabel, CorrelationClass

# ---------------------------------------------------------------------------
# Stat component mappings: if a player has BOTH a "parent" stat and any of its
# "children" in the same slip, that is SAME_PLAYER_COMPONENT_OVERLAP or DIRECT_OVERLAP.
# ---------------------------------------------------------------------------

# parent stat → child stats (subset of parent)
STAT_COMPONENT_MAP: dict[str, list[str]] = {
    "points+rebounds+assists": ["points", "rebounds", "assists",
                                 "pts+reb", "pts+ast", "reb+ast"],
    "pts+reb+ast":             ["points", "rebounds", "assists",
                                 "pts+reb", "pts+ast", "reb+ast"],
    "points+assists":          ["points", "assists"],
    "pts+ast":                 ["points", "assists"],
    "points+rebounds":         ["points", "rebounds"],
    "pts+reb":                 ["points", "rebounds"],
    "rebounds+assists":        ["rebounds", "assists"],
    "reb+ast":                 ["rebounds", "assists"],
    "passing yards":           ["passing completions", "passing attempts"],
    "receiving yards":         ["receptions", "targets"],
    "hits+runs+rbi":           ["hits", "runs", "rbis", "rbi"],
}

# Stats that directly conflict (same player, same-side bet = double-counting)
DIRECT_OVERLAP_PAIRS: set[frozenset] = {
    frozenset({"points", "pts+reb+ast"}),
    frozenset({"rebounds", "pts+reb+ast"}),
    frozenset({"assists", "pts+reb+ast"}),
    frozenset({"points", "pts+ast"}),
    frozenset({"assists", "pts+ast"}),
    frozenset({"points", "pts+reb"}),
    frozenset({"rebounds", "pts+reb"}),
    frozenset({"rebounds", "reb+ast"}),
    frozenset({"assists", "reb+ast"}),
    frozenset({"hits", "hits+runs+rbi"}),
    frozenset({"runs", "hits+runs+rbi"}),
}

# Usage-competing stat pairs for teammates on same team
USAGE_COMPETING_STATS: set[str] = {
    "assists", "points", "field goals made", "field goals attempted",
    "three pointers made", "shots on target", "touches",
}


def _normalize(s: str) -> str:
    """Lowercase and strip a stat/player name for comparison."""
    return re.sub(r"\s+", " ", s.lower().strip())


def classify_legs(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Classify correlation for a list of prop legs.

    Each leg dict should have:
      player   str   — player name
      stat     str   — stat / prop type (e.g. "points", "rebounds+assists")
      side     str   — MORE / LESS / OVER / UNDER
      team     str   — team abbreviation or name (optional, for teammate detection)

    Returns:
      {
        classification:    CorrelationClass value (worst classification found)
        classifications:   list of all classifications found
        conflicts:         list of human-readable conflict descriptions
        block_power_play:  bool  — True when Power Play should be blocked
        note_flex_math:    bool  — True when Flex EV needs hit-combination math
        can_approve_bets:  False
      }
    """
    if not legs or len(legs) < 2:
        return _clean_result(
            classification=CorrelationClass.LOW_CORRELATION,
            classifications=[CorrelationClass.LOW_CORRELATION],
            conflicts=[],
            block_power=False,
            note_flex=False,
        )

    # Normalize legs
    normed = []
    for leg in legs:
        normed.append({
            "player": _normalize(leg.get("player") or ""),
            "stat":   _normalize(leg.get("stat") or leg.get("prop_type") or ""),
            "side":   (leg.get("side") or leg.get("direction") or "MORE").upper().strip(),
            "team":   _normalize(leg.get("team") or ""),
        })

    found_classes: list[CorrelationClass] = []
    conflicts: list[str] = []

    n = len(normed)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = normed[i], normed[j]
            cls, desc = _pair_classify(a, b)
            if cls != CorrelationClass.LOW_CORRELATION:
                found_classes.append(cls)
                conflicts.append(desc)

    if not found_classes:
        found_classes.append(CorrelationClass.LOW_CORRELATION)

    # Worst classification (priority order)
    worst = _worst_class(found_classes)

    block_power = worst in (
        CorrelationClass.DIRECT_OVERLAP,
        CorrelationClass.SAME_PLAYER_COMPONENT,
        CorrelationClass.UNKNOWN,
    )
    note_flex = worst in (
        CorrelationClass.TEAMMATE_USAGE_NEGATIVE,
        CorrelationClass.BLOWOUT_SHARED_RISK,
        CorrelationClass.PACE_ENVIRONMENT_POSITIVE,
        CorrelationClass.INJURY_ROLE_DEPENDENCY,
    )

    return _clean_result(worst, found_classes, conflicts, block_power, note_flex)


def run_slip_gate(
    row:   dict[str, Any],
    legs:  list[dict[str, Any]],
    slip_type: str = "",
) -> dict[str, Any]:
    """
    Apply correlation gate to a pipeline row.

    Sets row["gates"]["correlation_gate"] and potentially row["terminal_label"].
    """
    if row.get("terminal_label") is not None:
        return row

    result = classify_legs(legs)
    is_power = "power" in slip_type.lower()

    if result["block_power_play"] and is_power:
        if not row.get("terminal_label"):
            row["terminal_label"] = PropLabel.REJECT_POWER_CORRELATED.value
            row.setdefault("blockers", []).append(
                f"CORRELATION_GATE:REJECT_POWER_CORRELATED:"
                f"{result['classification']}"
            )

    row.setdefault("gates", {})["correlation_gate"] = result
    return row


# ---------------------------------------------------------------------------
# Pair-level classification
# ---------------------------------------------------------------------------

def _pair_classify(a: dict, b: dict) -> tuple[CorrelationClass, str]:
    """Classify correlation between two legs. Returns (class, description)."""
    same_player = bool(a["player"] and a["player"] == b["player"])
    same_team   = bool(a["team"] and a["team"] == b["team"] and not same_player)

    if same_player:
        stat_pair = frozenset({a["stat"], b["stat"]})

        # Direct overlap: e.g. "points" and "pts+reb+ast" on same player
        if stat_pair in DIRECT_OVERLAP_PAIRS:
            return (
                CorrelationClass.DIRECT_OVERLAP,
                f"DIRECT_OVERLAP: {a['player']} has both '{a['stat']}' and '{b['stat']}' "
                f"— one stat is a component of the other. Auto-reject same slip.",
            )

        # Component overlap: check STAT_COMPONENT_MAP
        for parent, children in STAT_COMPONENT_MAP.items():
            if (a["stat"] == parent and b["stat"] in children) or \
               (b["stat"] == parent and a["stat"] in children):
                return (
                    CorrelationClass.SAME_PLAYER_COMPONENT,
                    f"SAME_PLAYER_COMPONENT: {a['player']} has '{a['stat']}' and '{b['stat']}' "
                    f"— component overlap.",
                )

        # Same player, different stats but same direction = correlated by role
        if a["side"] == b["side"]:
            return (
                CorrelationClass.PACE_ENVIRONMENT_POSITIVE,
                f"SAME_PLAYER_ENVIRONMENT: {a['player']} '{a['stat']}' and '{b['stat']}' "
                f"same side — shared role environment.",
            )

    elif same_team:
        # Usage-competing teammates
        if a["stat"] in USAGE_COMPETING_STATS and b["stat"] in USAGE_COMPETING_STATS:
            if a["side"] == b["side"]:
                return (
                    CorrelationClass.TEAMMATE_USAGE_NEGATIVE,
                    f"TEAMMATE_USAGE_NEGATIVE: {a['player']} and {b['player']} "
                    f"on same team, both '{a['stat']}'/{b['stat']}' same side — usage competition.",
                )

        # Blowout shared risk: both legs on same team in same game, more direction
        return (
            CorrelationClass.BLOWOUT_SHARED_RISK,
            f"BLOWOUT_SHARED_RISK: {a['player']} and {b['player']} on same team "
            f"— shared blowout/game-script risk.",
        )

    return CorrelationClass.LOW_CORRELATION, ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLASS_PRIORITY = [
    CorrelationClass.LOW_CORRELATION,
    CorrelationClass.PACE_ENVIRONMENT_POSITIVE,
    CorrelationClass.BLOWOUT_SHARED_RISK,
    CorrelationClass.INJURY_ROLE_DEPENDENCY,
    CorrelationClass.TEAMMATE_USAGE_POSITIVE,
    CorrelationClass.TEAMMATE_USAGE_NEGATIVE,
    CorrelationClass.UNKNOWN,
    CorrelationClass.SAME_PLAYER_COMPONENT,
    CorrelationClass.DIRECT_OVERLAP,
]


def _worst_class(classes: list[CorrelationClass]) -> CorrelationClass:
    def rank(c: CorrelationClass) -> int:
        try:
            return _CLASS_PRIORITY.index(c)
        except ValueError:
            return 0
    return max(classes, key=rank)


def _clean_result(
    classification: CorrelationClass,
    classifications: list[CorrelationClass],
    conflicts: list[str],
    block_power: bool,
    note_flex: bool,
) -> dict[str, Any]:
    return {
        "classification":   classification.value,
        "classifications":  [c.value for c in classifications],
        "conflicts":        conflicts,
        "block_power_play": block_power,
        "note_flex_math":   note_flex,
        "passed":           not block_power,
        "can_approve_bets": False,
    }
