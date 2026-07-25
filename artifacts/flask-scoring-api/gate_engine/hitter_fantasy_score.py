"""
hitter_fantasy_score.py — Discrete event-tree model for PrizePicks Hitter Fantasy Score

WOW Stage 2 — Reviewer requirement: averages alone cannot score this market.
This module computes a discrete scoring event distribution following the exact
PrizePicks Fantasy Score (Baseball) scoring rules.

Scoring rules (current standard — Baseball/Hitter):
  Single:       3 pts
  Double:       6 pts
  Triple:       9 pts
  Home Run:    12 pts
  Run:          3 pts
  RBI:          3 pts
  Stolen Base:  6 pts
  Walk:         2 pts

Usage:
  from gate_engine.hitter_fantasy_score import (
      compute_fantasy_score_distribution,
      compute_line_probability,
      estimate_rates_from_log,
  )

HARD RULE:
    can_execute = False
    EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
"""
from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
MODULE_VERSION = "v1.0"

# PrizePicks Fantasy Score (Baseball/Hitter) scoring table — as of 2026
PRIZEPICKS_SCORING: dict[str, float] = {
    "single":       3.0,
    "double":       6.0,
    "triple":       9.0,
    "home_run":    12.0,
    "run":          3.0,
    "rbi":          3.0,
    "stolen_base":  6.0,
    "walk":         2.0,
}

# MLB population averages (per PA) used when season_log is unavailable
MLB_POP_RATES: dict[str, float] = {
    "obp":           0.320,
    "k_rate":        0.220,
    "bb_rate":       0.084,
    "hbp_rate":      0.009,
    "single_rate":   0.147,   # per PA
    "double_rate":   0.049,   # per PA
    "triple_rate":   0.004,   # per PA
    "hr_rate":       0.035,   # per PA
    "sb_per_game":   0.12,    # stolen bases per game (player-level)
    "run_per_pa":    0.052,
    "rbi_per_pa":    0.048,
    "pa_per_game":   4.0,
}

# Minimum PA per season_log entry to trust the log
MIN_PA_FOR_TRUST = 50


# ---------------------------------------------------------------------------
# Rate estimation from season log
# ---------------------------------------------------------------------------

def estimate_rates_from_log(season_log: list[Any] | None) -> dict[str, float]:
    """
    Estimate per-PA event rates from a season log.

    season_log may be:
      - list of numeric fantasy score totals (scalar log): we can only estimate
        average FS and std; individual component rates fall back to population.
      - list of dicts with keys: pa, h, 1b, 2b, 3b, hr, bb, sb, r, rbi

    Returns a rates dict suitable for passing to compute_fantasy_score_distribution.
    """
    if not season_log:
        return MLB_POP_RATES.copy()

    # Try dict-format log
    if isinstance(season_log[0], dict):
        return _rates_from_dict_log(season_log)

    # Scalar log (list of game fantasy scores)
    return _rates_from_scalar_log(season_log)


def _rates_from_dict_log(log: list[dict]) -> dict[str, float]:
    """Aggregate rates from component-level game-log dicts."""
    totals: dict[str, float] = {
        "pa": 0, "1b": 0, "2b": 0, "3b": 0, "hr": 0,
        "bb": 0, "hbp": 0, "sb": 0, "r": 0, "rbi": 0,
    }
    games = 0
    for g in log:
        if not isinstance(g, dict):
            continue
        games += 1
        for k in totals:
            totals[k] += float(g.get(k, 0) or 0)

    if games == 0 or totals["pa"] == 0:
        return MLB_POP_RATES.copy()

    pa = max(1.0, totals["pa"])
    return {
        "obp":          round((totals["1b"] + totals["2b"] + totals["3b"] +
                               totals["hr"] + totals["bb"] + totals["hbp"]) / pa, 4),
        "k_rate":       MLB_POP_RATES["k_rate"],    # not usually in game logs
        "bb_rate":      round(totals["bb"] / pa, 4),
        "hbp_rate":     round(totals["hbp"] / pa, 4) if totals.get("hbp") else MLB_POP_RATES["hbp_rate"],
        "single_rate":  round(totals["1b"] / pa, 4),
        "double_rate":  round(totals["2b"] / pa, 4),
        "triple_rate":  round(totals["3b"] / pa, 4),
        "hr_rate":      round(totals["hr"] / pa, 4),
        "sb_per_game":  round(totals["sb"] / max(1, games), 4),
        "run_per_pa":   round(totals["r"] / pa, 4),
        "rbi_per_pa":   round(totals["rbi"] / pa, 4),
        "pa_per_game":  round(totals["pa"] / max(1, games), 2),
    }


def _rates_from_scalar_log(log: list) -> dict[str, float]:
    """
    Scalar log (list of per-game fantasy scores).
    Derive mean/std; component rates fall back to population.
    """
    scores = [float(x) for x in log if x is not None]
    if not scores:
        return MLB_POP_RATES.copy()
    mean_fs = sum(scores) / len(scores)
    rates   = MLB_POP_RATES.copy()
    # Calibrate overall output scale from observed mean
    # Expected FS per game from pop rates:
    pop_efs_per_game = _expected_fs_per_pa(MLB_POP_RATES) * MLB_POP_RATES["pa_per_game"]
    if pop_efs_per_game > 0:
        scale = mean_fs / pop_efs_per_game
        # Scale all rate-based components proportionally
        for k in ("single_rate", "double_rate", "triple_rate", "hr_rate",
                  "sb_per_game", "run_per_pa", "rbi_per_pa", "bb_rate"):
            rates[k] = round(rates[k] * scale, 4)
    return rates


def _expected_fs_per_pa(rates: dict[str, float]) -> float:
    """Expected fantasy score per single plate appearance."""
    scoring = PRIZEPICKS_SCORING
    return (
        rates.get("single_rate", 0) * scoring["single"] +
        rates.get("double_rate", 0) * scoring["double"] +
        rates.get("triple_rate", 0) * scoring["triple"] +
        rates.get("hr_rate",     0) * scoring["home_run"] +
        rates.get("bb_rate",     0) * scoring["walk"] +
        rates.get("run_per_pa",  0) * scoring["run"] +
        rates.get("rbi_per_pa",  0) * scoring["rbi"]
        # sb handled at game level below
    )


# ---------------------------------------------------------------------------
# Core event-tree distribution
# ---------------------------------------------------------------------------

def compute_fantasy_score_distribution(
    pa_remaining: float,
    rates:        dict[str, float] | None = None,
    scoring:      dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Compute the expected PrizePicks Fantasy Score distribution over pa_remaining
    remaining plate appearances.

    Each PA is modeled as an independent discrete event:
        strikeout / other_out / walk / HBP / single / double / triple / home_run

    Runs are modeled as: P(run per PA) based on historical run_per_pa rate.
    RBI are modeled as: P(rbi per PA) based on historical rbi_per_pa rate.
    Stolen bases are modeled at the game level (sb_per_game / pa_per_game).

    Returns:
        {
          pa_remaining:            float
          rates_used:              dict
          expected_events:         dict[event, float]   (expected count in pa_remaining)
          expected_fs:             float
          variance_fs:             float
          std_fs:                  float
          p_at_least_n:            dict[str, float]     P(FS >= n) for n in 5..40
          scoring_rules:           dict
          p_zero_hit_events:       float
          p_positive_fs:           float
          model_version:           str
          can_execute:             bool
        }
    """
    r = MLB_POP_RATES.copy()
    if rates:
        r.update({k: v for k, v in rates.items() if isinstance(v, (int, float)) and v >= 0})

    s = scoring if scoring else PRIZEPICKS_SCORING.copy()

    n = max(0.0, float(pa_remaining))

    # Per-PA event probabilities (must sum ≤ 1)
    p_hr     = min(r.get("hr_rate",     0.035), 0.12)
    p_triple = min(r.get("triple_rate", 0.004), 0.020)
    p_double = min(r.get("double_rate", 0.049), 0.12)
    p_single = min(r.get("single_rate", 0.147), 0.28)
    p_bb     = min(r.get("bb_rate",     0.084), 0.20)
    p_hit    = p_single + p_double + p_triple + p_hr
    p_reach  = p_hit + p_bb + r.get("hbp_rate", 0.009)

    # Sanity clamp
    if p_reach > 0.95:
        scale    = 0.95 / p_reach
        p_hr     *= scale; p_triple *= scale; p_double *= scale
        p_single *= scale; p_bb *= scale

    p_run_per_pa = min(r.get("run_per_pa", 0.052), 0.15)
    p_rbi_per_pa = min(r.get("rbi_per_pa", 0.048), 0.15)
    p_sb_per_pa  = min(r.get("sb_per_game", 0.12) / max(r.get("pa_per_game", 4.0), 1.0), 0.06)

    # Expected counts over n PA
    exp: dict[str, float] = {
        "single":      round(p_single * n, 4),
        "double":      round(p_double * n, 4),
        "triple":      round(p_triple * n, 4),
        "home_run":    round(p_hr * n, 4),
        "walk":        round(p_bb * n, 4),
        "run":         round(p_run_per_pa * n, 4),
        "rbi":         round(p_rbi_per_pa * n, 4),
        "stolen_base": round(p_sb_per_pa * n, 4),
    }

    # Expected fantasy score
    efs = sum(exp[k] * s[k] for k in s if k in exp)

    # Variance: Poisson approximation — Var[FS] ≈ Σ (pts²) * E[count]
    var_fs = sum((s[k] ** 2) * exp[k] for k in s if k in exp)
    std_fs = math.sqrt(max(0.0, var_fs))

    # P(FS >= threshold) via normal approximation for thresholds 5..40 in steps of 5
    p_at_least: dict[str, float] = {}
    for threshold in range(5, 45, 5):
        if std_fs <= 0:
            p_at_least[str(threshold)] = 1.0 if efs >= threshold else 0.0
        else:
            z = (threshold - 0.5 - efs) / std_fs
            p_al = 1.0 - _norm_cdf(z)
            p_at_least[str(threshold)] = round(max(0.0, min(1.0, p_al)), 4)

    p_zero_hits    = round(max(0.0, (1.0 - p_hit) ** n), 4)
    p_positive_fs  = round(1.0 - (1.0 - p_hit - p_bb * (s.get("walk", 0) / max(s.get("walk", 2), 1))) ** n, 4)

    return {
        "pa_remaining":        round(n, 2),
        "rates_used":          {k: round(v, 5) for k, v in r.items()},
        "expected_events":     exp,
        "expected_fs":         round(efs, 3),
        "variance_fs":         round(var_fs, 3),
        "std_fs":              round(std_fs, 3),
        "p_at_least_n":        p_at_least,
        "scoring_rules":       s,
        "p_zero_hit_events":   p_zero_hits,
        "p_positive_fs":       p_positive_fs,
        "model_version":       MODULE_VERSION,
        "can_execute":         False,
    }


def compute_line_probability(
    line:         float,
    direction:    str,
    pa_remaining: float,
    rates:        dict[str, float] | None = None,
    scoring:      dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Compute P(MORE) and P(LESS) for a PrizePicks Fantasy Score prop at a given line.

    Returns:
        {
          P_MORE: float
          P_LESS: float
          raw_probability: float
          expected_fs: float
          std_fs: float
          line: float
          direction: str
          model: str
        }
    """
    dist = compute_fantasy_score_distribution(pa_remaining, rates=rates, scoring=scoring)
    efs  = dist["expected_fs"]
    std  = dist["std_fs"]

    if std <= 0:
        p_more = 1.0 if efs > line else (0.5 if efs == line else 0.0)
    else:
        z      = (line - efs) / std
        p_more = round(max(0.01, min(0.99, 1.0 - _norm_cdf(z))), 4)

    p_less = round(1.0 - p_more, 4)
    dir_upper = (direction or "MORE").upper()
    raw_prob  = p_more if dir_upper == "MORE" else p_less

    return {
        "P_MORE":          p_more,
        "P_LESS":          p_less,
        "raw_probability": raw_prob,
        "expected_fs":     efs,
        "std_fs":          std,
        "line":            line,
        "direction":       dir_upper,
        "model":           f"hitter_fantasy_score/{MODULE_VERSION}",
        "can_execute":     False,
    }


def validate_market_support(market_type: str) -> dict[str, Any]:
    """
    Confirm whether hitter_fantasy_score supports the given market_type.

    Supported:
        "hitter_fantasy_score", "fantasy_score", "hitter_fs",
        "batting_fantasy", "baseball_hitter_fs"

    Unsupported markets MUST fail closed (REJECT_DATA_QUALITY) — the gate
    must not fabricate a probability for an unsupported market type.

    Returns:
        { supported: bool, canonical_name: str | None, reason: str }
    """
    lower = (market_type or "").lower().strip()
    supported_aliases = {
        "hitter_fantasy_score", "fantasy_score", "hitter_fs",
        "batting_fantasy", "baseball_hitter_fs", "hitter fantasy score",
    }
    if lower in supported_aliases:
        return {
            "supported":      True,
            "canonical_name": "hitter_fantasy_score",
            "reason":         f"Market type '{market_type}' is supported by hitter_fantasy_score module",
        }

    return {
        "supported":      False,
        "canonical_name": None,
        "reason": (
            f"Market type '{market_type}' is NOT supported by hitter_fantasy_score module. "
            f"This market must fail closed (REJECT_DATA_QUALITY). "
            f"Do not fabricate a probability. "
            f"Supported aliases: {', '.join(sorted(supported_aliases))}"
        ),
    }


# ---------------------------------------------------------------------------
# Normal CDF helper (same as mlb_live_micro_market.py, kept local to avoid
# circular import)
# ---------------------------------------------------------------------------

def _norm_cdf(z: float) -> float:
    """Approximate standard normal CDF via Abramowitz & Stegun."""
    t = 1.0 / (1.0 + 0.2316419 * abs(z))
    coeffs = (0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429)
    poly = sum(c * t ** (i + 1) for i, c in enumerate(coeffs))
    p = 1.0 - math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi) * poly
    return p if z >= 0 else 1.0 - p
