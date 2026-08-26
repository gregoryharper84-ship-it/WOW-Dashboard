"""
gate_engine/universal_agent/lanes/wnba_props/game_script/minutes_distribution.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

Script-Conditioned Minutes Distribution.

Computes the expected minutes played under each game script, respecting
player-supplied floor/ceiling from role_status and applying garbage-time
adjustments in blowout scripts.

Uses deterministic expected-value computation (NOT Monte Carlo sampling)
to keep the shadow layer lightweight and reproducible.

Fail-closed: returns MinutesEstimate with available=False when PlayerState
has expected_minutes=None.

can_execute = False
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from gate_engine.universal_agent.lanes.wnba_props.game_script.player_state import (
    PlayerState,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.game_environment import (
    ALL_SCRIPTS,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Hard caps on minutes per game by sport (regulation + OT allowance)
_WNBA_MINUTES_CAP = 45.0
_NBA_MINUTES_CAP  = 53.0


@dataclass(frozen=True)
class MinutesEstimate:
    """
    Script-conditioned minutes estimate for one player in one script.

    available:         False when PlayerState has no minutes data (fail-closed).
    expected_minutes:  E[minutes | script] after garbage-time adjustment.
    effective_minutes: expected_minutes * (1 - garbage_time_probability).
                       This is the figure used for stat rate calculations.
    minutes_std:       Standard deviation (passed through from PlayerState).
    minutes_cap:       Sport-specific hard cap.
    script:            Which script this estimate applies to.
    """
    script:               str
    available:            bool
    expected_minutes:     float | None
    effective_minutes:    float | None
    minutes_std:          float | None
    minutes_cap:          float


def compute_minutes_estimates(
    player_states: dict[str, PlayerState],
    sport: str = "WNBA",
) -> dict[str, MinutesEstimate]:
    """
    Compute MinutesEstimate for each game script.

    Parameters
    ----------
    player_states   Output of derive_player_states().
    sport           "WNBA" or "NBA" — used for hard cap.

    Returns {script_id: MinutesEstimate} for all 5 scripts.
    """
    cap = _NBA_MINUTES_CAP if sport.upper() == "NBA" else _WNBA_MINUTES_CAP
    estimates: dict[str, MinutesEstimate] = {}

    for script in ALL_SCRIPTS:
        ps = player_states.get(script)
        if ps is None or ps.expected_minutes is None:
            estimates[script] = MinutesEstimate(
                script=script,
                available=False,
                expected_minutes=None,
                effective_minutes=None,
                minutes_std=None,
                minutes_cap=cap,
            )
            continue

        # Clamp expected minutes to hard cap
        exp_min = min(ps.expected_minutes, cap)

        # Effective minutes = expected × (1 − garbage_time_risk)
        # In blowout scripts, some share of time is in garbage time where
        # starter role players may be on the bench.
        effective = exp_min * (1.0 - ps.garbage_time_risk * 0.5)
        effective = max(0.0, min(effective, cap))

        estimates[script] = MinutesEstimate(
            script=script,
            available=True,
            expected_minutes=round(exp_min, 4),
            effective_minutes=round(effective, 4),
            minutes_std=ps.minutes_std,
            minutes_cap=cap,
        )

    return estimates
