"""
gate_engine/universal_agent/lanes/wnba_props/game_script/conditional_hit_prob.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

Stat-Specific Conditional Hit Probability.

For each game script computes P(stat >= line | script) using a Poisson
model parameterised by the player's per-minute rate derived from game_log.

Supported stat keys (case-insensitive):
  "rebounds", "reb", "points", "pts", "assists", "ast",
  "blocks", "blk", "steals", "stl", "turnovers", "to"

Fail-closed contract
--------------------
- Returns ConditionalHitResult with available=False when:
    * game_log is missing or empty
    * Stat key not recognised
    * Rate cannot be computed (zero minutes in log)
    * MinutesEstimate is unavailable
- Never fabricates a probability. Never raises — returns sentinel instead.

Ceiling: MODEL_QUALIFIED_HOLD (set at shadow gate level).
can_execute = False
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from gate_engine.universal_agent.lanes.wnba_props.game_script.game_environment import (
    ALL_SCRIPTS,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.minutes_distribution import (
    MinutesEstimate,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Canonical stat key → possible game_log field names (first match wins)
_STAT_FIELDS: dict[str, tuple[str, ...]] = {
    "rebounds":  ("reb", "rebounds", "Reb", "REB"),
    "points":    ("pts", "points", "Pts", "PTS"),
    "assists":   ("ast", "assists", "Ast", "AST"),
    "blocks":    ("blk", "blocks", "Blk", "BLK"),
    "steals":    ("stl", "steals", "Stl", "STL"),
    "turnovers": ("to", "turnovers", "tov", "TO", "TOV"),
}

# Minutes field names in game_log entries
_MIN_FIELDS: tuple[str, ...] = ("min", "minutes", "Min", "MIN")

# Normalize incoming stat key to canonical
_STAT_ALIASES: dict[str, str] = {
    "reb": "rebounds", "rebounds": "rebounds",
    "pts": "points",   "points": "points",
    "ast": "assists",  "assists": "assists",
    "blk": "blocks",   "blocks": "blocks",
    "stl": "steals",   "steals": "steals",
    "to":  "turnovers","tov": "turnovers", "turnovers": "turnovers",
}


@dataclass(frozen=True)
class ConditionalHitResult:
    """
    P(stat >= line | script) for one game script.

    available:      False on any fail-closed condition.
    probability:    P(hit | script) ∈ [0, 1] or None if unavailable.
    rate_per_min:   Derived per-minute rate from game_log.
    effective_min:  Minutes used in the Poisson computation.
    expected_stat:  rate_per_min * effective_min (E[stat | script]).
    script:         Game script identifier.
    stat_key:       Canonical stat key.
    """
    script:        str
    stat_key:      str
    available:     bool
    probability:   float | None
    rate_per_min:  float | None
    effective_min: float | None
    expected_stat: float | None


def compute_conditional_hit_probs(
    combined:          dict,
    minutes_estimates: dict[str, MinutesEstimate],
    line:              float,
    stat_key_raw:      str,
) -> dict[str, ConditionalHitResult]:
    """
    Compute P(stat >= line | script) for all 5 game scripts.

    Parameters
    ----------
    combined           Combined evidence dict (for game_log / box_score_log).
    minutes_estimates  Output of compute_minutes_estimates().
    line               Prop line value (threshold).
    stat_key_raw       Raw stat key string from the row (e.g. "rebounds", "reb").

    Returns {script_id: ConditionalHitResult} for all 5 scripts.
    """
    stat_key = _STAT_ALIASES.get(stat_key_raw.lower().strip()) if stat_key_raw else None
    if stat_key is None:
        return {s: _unavailable(s, "unrecognised", line) for s in ALL_SCRIPTS}

    rate = _compute_stat_rate(combined, stat_key)
    if rate is None:
        return {s: _unavailable(s, stat_key, line) for s in ALL_SCRIPTS}

    results: dict[str, ConditionalHitResult] = {}
    for script in ALL_SCRIPTS:
        me = minutes_estimates.get(script)
        if me is None or not me.available or me.effective_minutes is None:
            results[script] = _unavailable(script, stat_key, line)
            continue

        eff_min  = me.effective_minutes
        exp_stat = rate * eff_min
        prob     = _poisson_hit_prob(line, exp_stat)

        results[script] = ConditionalHitResult(
            script=script,
            stat_key=stat_key,
            available=True,
            probability=round(prob, 6),
            rate_per_min=round(rate, 6),
            effective_min=round(eff_min, 4),
            expected_stat=round(exp_stat, 4),
        )

    return results


# ── Internal helpers ──────────────────────────────────────────────────────────

def _compute_stat_rate(combined: dict, stat_key: str) -> float | None:
    """
    Derive per-minute stat rate from game_log or box_score_log.
    Returns None if game_log absent, empty, or zero total minutes.
    """
    log = combined.get("game_log") or combined.get("box_score_log") or []
    if not isinstance(log, list) or not log:
        return None

    stat_fields = _STAT_FIELDS.get(stat_key, ())
    total_stat  = 0.0
    total_min   = 0.0

    for entry in log:
        if not isinstance(entry, dict):
            continue
        # Extract minutes
        min_val = None
        for mf in _MIN_FIELDS:
            if entry.get(mf) is not None:
                try:
                    min_val = float(entry[mf])
                    break
                except (TypeError, ValueError):
                    pass
        if min_val is None or min_val < 0:
            continue

        # Extract stat
        stat_val = None
        for sf in stat_fields:
            if entry.get(sf) is not None:
                try:
                    stat_val = float(entry[sf])
                    break
                except (TypeError, ValueError):
                    pass
        if stat_val is None:
            continue

        total_min  += min_val
        total_stat += stat_val

    if total_min <= 0:
        return None
    return total_stat / total_min


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam). Pure Python, no scipy."""
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    result = 0.0
    term = math.exp(-lam)
    for i in range(max(0, k) + 1):
        result += term
        if i <= k:
            term *= lam / (i + 1)
    return min(result, 1.0)


def _poisson_hit_prob(line: float, lam: float) -> float:
    """
    P(X >= ceil(line)) for X ~ Poisson(lam).
    For half-point lines (e.g. 10.5), ceil gives 11 — correct for O/U bets.
    """
    k = int(math.floor(line))
    return max(0.0, min(1.0, 1.0 - _poisson_cdf(k, lam)))


def _unavailable(script: str, stat_key: str, line: float) -> ConditionalHitResult:
    return ConditionalHitResult(
        script=script, stat_key=stat_key,
        available=False, probability=None,
        rate_per_min=None, effective_min=None, expected_stat=None,
    )
