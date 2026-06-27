"""
directional_exposure.py  —  Module G: Directional Exposure Ledger
WOW v16 / Section 27.6

Per-player and per-game caps do not catch hidden correlated game-script risk.
This module tracks exposure by script type across all legs in a session.
It runs after per-prop classification, before final slip label.

SCRIPT TYPES:
  fast_pace_over       — Multiple MORE pts/PRA/assists from same game
  slow_pace_under      — Multiple LESS props from same game
  blowout_script       — Favorite overs + dog unders + bench-risk legs
  pitcher_dominance    — Ks MORE + opp hitters LESS + game under
  starter_short_leash  — Outs LESS + bullpen angle + opp late scoring
  injury_role_script   — Multiple props dependent on same teammate being out
  pace_sensitive_combo — Any 3+ legs whose hit-prob changes materially if
                         pace is 5+ possessions different from projection

RULES:
  Slip-level:
    3+ legs in one slip sharing same script type →
      DIRECTIONAL_EXPOSURE_BLOCK unless correlation EV math is documented

  Session-level:
    4+ same-script legs across full session →
      SESSION_EXPOSURE_WARNING (logged, analyst must acknowledge)
    6+ same-script legs across full session →
      SESSION_DIRECTIONAL_EXPOSURE_BLOCK (no further same-script without ChatGPT override)
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Script type registry
# ---------------------------------------------------------------------------

SCRIPT_TYPES = (
    "fast_pace_over",
    "slow_pace_under",
    "blowout_script",
    "pitcher_dominance",
    "starter_short_leash",
    "injury_role_script",
    "pace_sensitive_combo",
)

# Thresholds
SLIP_BLOCK_THRESHOLD    = 3   # 3+ same-script in ONE slip → DIRECTIONAL_EXPOSURE_BLOCK
SESSION_WARN_THRESHOLD  = 4   # 4+ same-script across session → SESSION_EXPOSURE_WARNING
SESSION_BLOCK_THRESHOLD = 6   # 6+ same-script across session → SESSION_DIRECTIONAL_EXPOSURE_BLOCK


# ---------------------------------------------------------------------------
# Slip-level analysis
# ---------------------------------------------------------------------------

def check_slip(
    legs: list[dict[str, Any]],
    require_ev_math: bool = True,
) -> dict[str, Any]:
    """
    Analyse a single slip (list of leg dicts) for directional exposure.

    Each leg dict should have:
        directional_exposure_tags: list[str]  — script types applying to this leg
        player:                    str
        prop_type:                 str
        correlation_ev_documented: bool       (optional, default False)

    Returns:
        {
          passed:                       bool
          dominant_script:              str | None
          directional_exposure_count:   int
          script_counts:                dict[str, int]
          ev_math_documented:           bool
          verdict:                      "CLEAN"|"WARNING"|"BLOCK"
          blocked_legs:                 list[str]   (player+prop for exposed legs)
          code:                         str
          detail:                       str
        }
    """
    script_counts: Counter[str] = Counter()
    ev_math_documented = False

    for leg in legs:
        tags = leg.get("directional_exposure_tags") or []
        if isinstance(tags, str):
            tags = [tags]
        for t in tags:
            if t in SCRIPT_TYPES:
                script_counts[t] += 1
        if leg.get("correlation_ev_documented"):
            ev_math_documented = True

    dominant_script: str | None = None
    dominant_count  = 0
    if script_counts:
        dominant_script, dominant_count = script_counts.most_common(1)[0]

    blocked_legs: list[str] = []
    if dominant_count >= SLIP_BLOCK_THRESHOLD:
        for leg in legs:
            tags = leg.get("directional_exposure_tags") or []
            if dominant_script in tags:
                label = f"{leg.get('player','')}:{leg.get('prop_type','')}"
                blocked_legs.append(label)

    if dominant_count >= SLIP_BLOCK_THRESHOLD and (
        not ev_math_documented or not require_ev_math
    ):
        verdict = "BLOCK"
        code    = "DIRECTIONAL_EXPOSURE_BLOCK"
        passed  = False
        detail  = (
            f"{dominant_count} legs share script '{dominant_script}' in this slip "
            f"(>={SLIP_BLOCK_THRESHOLD} threshold). "
            + ("No correlation EV math documented — slip blocked."
               if not ev_math_documented
               else "Block overridden by documented EV math.")
        )
    elif dominant_count >= SLIP_BLOCK_THRESHOLD and ev_math_documented:
        verdict = "WARNING"
        code    = "DIRECTIONAL_EXPOSURE_EV_OVERRIDE"
        passed  = True
        detail  = (
            f"{dominant_count} legs share script '{dominant_script}' — "
            f"correlation EV math documented, proceeding with WARNING."
        )
    elif dominant_count >= 2:
        verdict = "WARNING"
        code    = "DIRECTIONAL_EXPOSURE_WARNING"
        passed  = True
        detail  = (
            f"{dominant_count} legs share script '{dominant_script}' — "
            f"below block threshold but worth noting."
        )
    else:
        verdict = "CLEAN"
        code    = "DIRECTIONAL_EXPOSURE_CLEAN"
        passed  = True
        detail  = "No dominant directional script detected."

    return {
        "passed":                     passed,
        "dominant_script":            dominant_script,
        "directional_exposure_count": dominant_count,
        "script_counts":              dict(script_counts),
        "ev_math_documented":         ev_math_documented,
        "verdict":                    verdict,
        "blocked_legs":               blocked_legs,
        "code":                       code,
        "detail":                     detail,
    }


# ---------------------------------------------------------------------------
# Session-level ledger
# ---------------------------------------------------------------------------

class SessionExposureLedger:
    """
    Tracks directional script exposure across all legs in a session.
    Accumulate rows by calling .record(row) after each prop is classified.
    Call .snapshot() to get the current session status.
    """

    def __init__(self) -> None:
        self._script_counts: Counter[str] = Counter()
        self._legs: list[dict[str, Any]] = []

    def record(self, row: dict[str, Any]) -> None:
        """Record a single prop row into the session ledger."""
        tags = row.get("directional_exposure_tags") or []
        if isinstance(tags, str):
            tags = [tags]
        for t in tags:
            if t in SCRIPT_TYPES:
                self._script_counts[t] += 1
        self._legs.append(row)

    def snapshot(self) -> dict[str, Any]:
        """Return the current session exposure status."""
        if not self._script_counts:
            return {
                "dominant_script":         None,
                "session_directional_count": 0,
                "script_counts":           {},
                "session_verdict":         "CLEAN",
                "code":                    "SESSION_CLEAN",
                "detail":                  "No directional exposure detected this session.",
            }

        dominant_script, dominant_count = self._script_counts.most_common(1)[0]

        if dominant_count >= SESSION_BLOCK_THRESHOLD:
            verdict = "SESSION_BLOCK"
            code    = "SESSION_DIRECTIONAL_EXPOSURE_BLOCK"
            detail  = (
                f"{dominant_count} legs sharing script '{dominant_script}' this session "
                f"(>={SESSION_BLOCK_THRESHOLD}). No further same-script legs without ChatGPT override."
            )
        elif dominant_count >= SESSION_WARN_THRESHOLD:
            verdict = "SESSION_WARNING"
            code    = "SESSION_EXPOSURE_WARNING"
            detail  = (
                f"{dominant_count} legs sharing script '{dominant_script}' this session "
                f"(>={SESSION_WARN_THRESHOLD}). Analyst acknowledgment required."
            )
        else:
            verdict = "CLEAN"
            code    = "SESSION_CLEAN"
            detail  = (
                f"Dominant script '{dominant_script}' has {dominant_count} legs "
                f"— below warning threshold."
            )

        return {
            "dominant_script":           dominant_script,
            "session_directional_count": dominant_count,
            "script_counts":             dict(self._script_counts),
            "session_verdict":           verdict,
            "code":                      code,
            "detail":                    detail,
        }


# ---------------------------------------------------------------------------
# Per-row gate (wires into pipeline)
# ---------------------------------------------------------------------------

def run(row: dict[str, Any], session_ledger: "SessionExposureLedger | None" = None) -> dict[str, Any]:
    """
    Record a single row into the session ledger and stamp the gate result.
    The slip-level check happens separately via check_slip().

    Returns a minimal gate result for the row.
    """
    tags = row.get("directional_exposure_tags") or []

    result: dict[str, Any] = {
        "passed":                  True,
        "directional_exposure_tags": tags,
        "session_ledger_updated":  session_ledger is not None,
    }

    if session_ledger is not None:
        session_ledger.record(row)
        snap = session_ledger.snapshot()
        if snap["session_verdict"] == "SESSION_BLOCK":
            row["blockers"].append(
                f"SESSION_DIRECTIONAL_EXPOSURE_BLOCK:{snap['dominant_script']}:"
                f"count={snap['session_directional_count']}"
            )
            result["session_verdict"] = snap["session_verdict"]
        elif snap["session_verdict"] == "SESSION_WARNING":
            result["session_verdict"] = snap["session_verdict"]
        else:
            result["session_verdict"] = "CLEAN"

    row.setdefault("gates", {})["directional_exposure"] = result
    return result
