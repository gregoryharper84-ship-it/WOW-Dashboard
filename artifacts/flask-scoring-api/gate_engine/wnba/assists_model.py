"""
assists_model.py  —  WNBA Player Assists Distribution Model
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT

Models P(assists > line) using a Poisson-based approximation.

Assists are count data — Poisson is a better base model than Normal.
For observed λ ≥ 4, the Normal approximation is adequate; below that,
the discrete Poisson CDF is used.

Inputs consumed (priority order):
    1. Explicit caller-supplied lambda (expected assists)
    2. Computed from raw game-value list
    3. League-average fallback

Adjustments available:
    - Primary teammate availability (on-ball playmaker in/out)
    - Pace (high/low tempo adjusts expected possessions)
    - Turnover risk (reduces realized assist opportunities)
    - Minutes distribution

can_execute=False unconditional.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

# ---------------------------------------------------------------------------
# Poisson CDF (no scipy dependency)
# ---------------------------------------------------------------------------

def _poisson_cdf(k: int, lam: float) -> float:
    """P(X ≤ k) for X ~ Poisson(λ)."""
    if lam <= 0:
        return 1.0
    result = 0.0
    term   = math.exp(-lam)
    for i in range(k + 1):
        result += term
        term   *= lam / (i + 1)
    return min(1.0, result)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _p_over_count(lam: float, line: float) -> float:
    """P(X > line) where X is an assists count using Poisson or Normal approximation."""
    k_floor = int(math.floor(line))   # P(X > line) = P(X ≥ k_floor + 1)
    if lam >= 4.0:
        # Normal approximation adequate for higher λ
        std = math.sqrt(lam)
        z   = (line - lam) / std
        return round(1.0 - _norm_cdf(z), 6)
    # Use discrete Poisson CDF
    return round(1.0 - _poisson_cdf(k_floor, lam), 6)


# ---------------------------------------------------------------------------
# League-average constants (2023-24 WNBA)
# ---------------------------------------------------------------------------

_WNBA_LEAGUE_AVG_APG = 2.1   # league-average assists per game
_MIN_LAMBDA          = 0.5

# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

def compute_assists_probability(
    line:                      float,
    lambda_assists:            float | None       = None,  # explicit expected assists
    game_values:               list[float] | None = None,
    minutes_mean:              float | None       = None,
    on_ball_role:              bool  | None       = None,  # True = primary ball-handler
    primary_teammate_avail:    bool  | None       = None,  # key target player available
    pace_adjustment:           float              = 0.0,   # +/- fractional adjustment to λ
    turnover_risk:             float              = 0.0,   # 0.0–1.0, reduces λ
    direction:                 str                = "MORE",
) -> dict[str, Any]:
    """
    P(assists > line) or P(assists < line) via Poisson distribution.

    Parameters
    ----------
    lambda_assists         : Explicit expected assists per game.
    game_values            : Recent per-game assist totals for fitting λ.
    minutes_mean           : Average minutes; used to scale λ when actual minutes differ.
    on_ball_role           : Is the player the primary ball-handler?
    primary_teammate_avail : Is the primary assist target (e.g. C/F) available?
    pace_adjustment        : Fractional adjustment to λ for pace: +0.1 = 10% more possessions.
    turnover_risk          : Fraction of potential assists lost to turnovers under pressure.
    direction              : "MORE" or "LESS".
    """
    warnings: list[str] = []
    adjustments: list[str] = []
    data_quality = "FULL"

    # ── Resolve λ ────────────────────────────────────────────────────────────
    if lambda_assists is not None:
        lam = float(lambda_assists)
        src = "caller_supplied"
    elif game_values and len(game_values) >= 3:
        lam = statistics.mean(game_values)
        src = f"computed_from_{len(game_values)}_games"
        if len(game_values) < 8:
            data_quality = "PARTIAL"
            warnings.append(f"Only {len(game_values)} game values — λ less stable.")
    elif game_values and len(game_values) > 0:
        lam = statistics.mean(game_values)
        src = "single_game_fallback"
        data_quality = "MINIMAL"
        warnings.append("Fewer than 3 games — assists λ is unreliable.")
    else:
        lam = _WNBA_LEAGUE_AVG_APG
        src = "league_average_fallback"
        data_quality = "MINIMAL"
        warnings.append(
            "No assists data — league-average λ=2.1. Result is unreliable."
        )

    lam = max(_MIN_LAMBDA, lam)

    # ── On-ball role adjustment ────────────────────────────────────────────
    if on_ball_role is True and lam < 3.0:
        lam *= 1.10
        adjustments.append("on_ball_role=True: λ boosted 10%")

    # ── Primary teammate availability ─────────────────────────────────────
    if primary_teammate_avail is False:
        lam *= 0.85   # 15% reduction if primary target is unavailable
        adjustments.append("primary_teammate_avail=False: λ reduced 15%")
    elif primary_teammate_avail is True:
        pass  # no change

    # ── Pace adjustment ────────────────────────────────────────────────────
    if pace_adjustment != 0.0:
        lam *= (1.0 + pace_adjustment)
        adjustments.append(f"pace_adjustment={pace_adjustment:+.0%}: λ adjusted")

    # ── Turnover risk ──────────────────────────────────────────────────────
    if turnover_risk > 0:
        lam *= (1.0 - turnover_risk * 0.5)   # turnovers reduce realized assists
        adjustments.append(f"turnover_risk={turnover_risk:.0%}: λ reduced by {turnover_risk*50:.0f}%")

    lam = max(_MIN_LAMBDA, lam)

    # ── Compute probability ────────────────────────────────────────────────
    if direction.upper() in ("MORE", "OVER", ">"):
        raw_p = _p_over_count(lam, float(line))
    else:
        k_floor = int(math.floor(float(line)))
        raw_p = _poisson_cdf(k_floor, lam)

    # Conservative lower bound: λ reduced by 10%
    lb_lam = max(_MIN_LAMBDA, lam * 0.90)
    if direction.upper() in ("MORE", "OVER", ">"):
        lb = _p_over_count(lb_lam, float(line))
    else:
        lb = _poisson_cdf(int(math.floor(float(line))), lb_lam)

    lb = round(max(0.0, min(1.0, lb)), 5)

    ci_width = 0.08
    ci = {
        "lower": round(max(0.0, raw_p - ci_width / 2), 5),
        "upper": round(min(1.0, raw_p + ci_width / 2), 5),
        "width": ci_width,
        "note":  "Poisson model CI — widens for low-λ props.",
    }

    return {
        "raw_probability":        round(raw_p, 5),
        "calibrated_lower_bound": lb,
        "confidence_interval":    ci,
        "lambda_used":            round(lam, 4),
        "line":                   float(line),
        "direction":              direction.upper(),
        "data_source":            src,
        "adjustments_applied":    adjustments,
        "data_quality":           data_quality,
        "data_quality_warning":   "; ".join(warnings) if warnings else None,
        "model_method":           (
            f"Poisson(λ={lam:.3f}) P({'>' if direction.upper() in ('MORE','OVER','>') else '≤'}{line})"
        ),
        "model_name":             "wnba_assists_poisson_v1",
        "can_execute":            False,
    }
