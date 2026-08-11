"""
gate_engine/universal_agent/lanes/wnba_props/game_script/player_state.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

Player-State Distribution per game script.

Derives the player's expected role/status in each game script:
  - expected_minutes   — script-adjusted from projected_minutes
  - minutes_std        — derived from minutes_low/high or heuristic
  - garbage_time_risk  — probability player exits early (blowout scripts)
  - dnp_risk           — probability player does not play at all

Fail-closed: returns PlayerState with status=UNAVAILABLE when required
inputs are missing or invalid.

can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gate_engine.universal_agent.lanes.wnba_props.game_script.game_environment import (
    SCRIPT_BLOWOUT_HOME, SCRIPT_BLOWOUT_AWAY,
    SCRIPT_CLOSE_HIGH, SCRIPT_CLOSE_LOW, SCRIPT_NEUTRAL,
    ALL_SCRIPTS,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_DEFAULT_WNBA_MINUTES = 28.0
_DEFAULT_NBA_MINUTES  = 32.0

# Script-specific minute adjustment (additive on top of projected_minutes)
# Blowout: star may be sat; Close: heavy-minute games
_SCRIPT_MINUTE_DELTA: dict[str, float] = {
    SCRIPT_BLOWOUT_HOME: -3.0,   # home team runs away → star may rest
    SCRIPT_BLOWOUT_AWAY: -3.0,   # away team blown out → garbage time risk
    SCRIPT_CLOSE_HIGH:   +2.0,   # tight high-scoring game → more minutes
    SCRIPT_CLOSE_LOW:    +1.0,   # tight low-scoring game → minutes stay up
    SCRIPT_NEUTRAL:       0.0,
}

_SCRIPT_GARBAGE_TIME_RISK: dict[str, float] = {
    SCRIPT_BLOWOUT_HOME: 0.35,
    SCRIPT_BLOWOUT_AWAY: 0.45,
    SCRIPT_CLOSE_HIGH:   0.02,
    SCRIPT_CLOSE_LOW:    0.02,
    SCRIPT_NEUTRAL:      0.08,
}


@dataclass(frozen=True)
class PlayerState:
    """
    Player-state estimate for one game script.

    status:            "AVAILABLE" | "QUESTIONABLE" | "OUT" | "UNAVAILABLE"
    expected_minutes:  float or None (None = unavailable)
    minutes_std:       float or None
    garbage_time_risk: probability of early exit (0.0 – 1.0)
    dnp_risk:          probability of not playing at all (0.0 – 1.0)
    script:            which script this state applies to
    """
    script:            str
    status:            str
    expected_minutes:  float | None
    minutes_std:       float | None
    garbage_time_risk: float
    dnp_risk:          float


def derive_player_states(combined: dict) -> dict[str, PlayerState]:
    """
    Return a PlayerState for each of the 5 game scripts.

    Reads from combined["role_status"] for projected_minutes, minutes_low,
    minutes_high, active_status, usage_role.

    Returns {script_id: PlayerState} for all 5 scripts.
    Each state is independent — fail for one script does not fail others.
    """
    rs = combined.get("role_status") or {}
    sport = (combined.get("sport") or "WNBA").strip().upper()
    default_minutes = _DEFAULT_NBA_MINUTES if sport == "NBA" else _DEFAULT_WNBA_MINUTES

    active_status = (rs.get("active_status") or "UNKNOWN").strip().upper()
    dnp_baseline  = _dnp_risk_from_status(active_status)

    proj_min  = rs.get("projected_minutes")
    min_low   = rs.get("minutes_low")
    min_high  = rs.get("minutes_high")

    base_minutes = float(proj_min) if proj_min is not None else default_minutes

    # Derive standard deviation from range if available, else heuristic
    if min_low is not None and min_high is not None:
        try:
            minutes_std = (float(min_high) - float(min_low)) / 4.0
        except (TypeError, ValueError):
            minutes_std = base_minutes * 0.18
    else:
        minutes_std = base_minutes * 0.18

    states: dict[str, PlayerState] = {}
    for script in ALL_SCRIPTS:
        delta = _SCRIPT_MINUTE_DELTA.get(script, 0.0)
        gt_risk = _SCRIPT_GARBAGE_TIME_RISK.get(script, 0.10)
        exp_min = max(0.0, base_minutes + delta)

        # If player is OUT, all scripts get expected_minutes=0
        if active_status == "OUT":
            states[script] = PlayerState(
                script=script,
                status="OUT",
                expected_minutes=0.0,
                minutes_std=0.0,
                garbage_time_risk=1.0,
                dnp_risk=1.0,
            )
        elif active_status in ("QUESTIONABLE", "GTD", "GAME_TIME_DECISION", "DOUBTFUL"):
            states[script] = PlayerState(
                script=script,
                status="QUESTIONABLE",
                expected_minutes=exp_min * (1.0 - dnp_baseline * 0.5),
                minutes_std=minutes_std,
                garbage_time_risk=gt_risk,
                dnp_risk=dnp_baseline,
            )
        elif proj_min is None:
            # No minutes data → UNAVAILABLE (fail-closed)
            states[script] = PlayerState(
                script=script,
                status="UNAVAILABLE",
                expected_minutes=None,
                minutes_std=None,
                garbage_time_risk=gt_risk,
                dnp_risk=dnp_baseline,
            )
        else:
            states[script] = PlayerState(
                script=script,
                status="AVAILABLE",
                expected_minutes=exp_min,
                minutes_std=minutes_std,
                garbage_time_risk=gt_risk,
                dnp_risk=dnp_baseline,
            )

    return states


def _dnp_risk_from_status(active_status: str) -> float:
    """Map active_status to baseline DNP risk probability."""
    mapping = {
        "ACTIVE":            0.01,
        "AVAILABLE":         0.01,
        "PROBABLE":          0.06,
        "QUESTIONABLE":      0.25,
        "GTD":               0.20,
        "GAME_TIME_DECISION": 0.20,
        "DOUBTFUL":          0.55,
        "OUT":               1.00,
        "DNP":               1.00,
        "INACTIVE":          1.00,
    }
    return mapping.get(active_status, 0.05)
