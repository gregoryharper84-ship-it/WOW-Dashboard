"""
threes_model.py  —  WNBA Player Three-Point Makes Distribution Model
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT

Models P(3PM > line) using the binomial distribution.

Formula:
    P(3PM > k) = 1 − Σ_{i=0}^{k} C(n, i) · p^i · (1-p)^(n-i)

Where:
    n = projected three-point attempts
    p = per-attempt three-point probability
    k = floor(line)   (prop lines are typically 0.5, 1.5, 2.5)

Relevant Linemaker critique (§4, Kelsey Mitchell):
  A mean of 2.83 on a line of 2.5 is not a large distributional margin.
  Three-point makes are HIGH VARIANCE. A proper model needs:
    - attempt distribution
    - three-point percentage distribution
    - opponent shot-profile allowance

can_execute=False unconditional.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

# ---------------------------------------------------------------------------
# Binomial distribution utilities (no scipy)
# ---------------------------------------------------------------------------

def _comb(n: int, k: int) -> int:
    return math.comb(n, k)


def _binom_pmf(k: int, n: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    return _comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X ≤ k) for X ~ Binomial(n, p)."""
    return sum(_binom_pmf(i, n, p) for i in range(k + 1))


# ---------------------------------------------------------------------------
# League-average constants (2023-24 WNBA)
# ---------------------------------------------------------------------------

_WNBA_LEAGUE_AVG_3PA  = 4.2    # league-average 3PA per game for a shooter
_WNBA_LEAGUE_AVG_3PCT = 0.340  # league-average 3P%

# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

def compute_threes_probability(
    line:                  float,
    projected_attempts:    float | None       = None,
    three_point_pct:       float | None       = None,
    game_attempt_values:   list[float] | None = None,
    game_make_values:      list[float] | None = None,
    minutes_mean:          float | None       = None,
    shot_quality_adj:      float              = 0.0,   # +/- per-shot quality vs average
    opponent_3pa_allow:    float | None       = None,  # opponent 3PA allowed per game to position
    conversion_uncertainty: float             = 0.0,   # additional std in p (0=none)
    direction:             str                = "MORE",
) -> dict[str, Any]:
    """
    P(3PM ≥ k+1) or P(3PM ≤ k) where k = floor(line).

    For half-integer lines (0.5, 1.5, 2.5):
        P(Over 0.5) = P(3PM ≥ 1) = 1 − P(3PM = 0)
        P(Over 1.5) = P(3PM ≥ 2) = 1 − P(0) − P(1)
        P(Over 2.5) = P(3PM ≥ 3) = 1 − P(0) − P(1) − P(2)

    Parameters
    ----------
    projected_attempts    : Expected 3PA per game.
    three_point_pct       : Per-attempt conversion probability.
    game_attempt_values   : Recent per-game 3PA list for fitting n.
    game_make_values      : Recent per-game 3PM list for fitting (n, p) jointly.
    shot_quality_adj      : Fractional adjustment to p_per_attempt (+0.05 = better looks).
    opponent_3pa_allow    : Opponent's mean 3PA allowed (scales attempt projection).
    conversion_uncertainty : Additional variance in p — widens the confidence interval.
    """
    warnings: list[str] = []
    adjustments: list[str] = []
    data_quality = "FULL"

    # ── Resolve n (projected attempts) ──────────────────────────────────────
    if projected_attempts is not None:
        n_float = float(projected_attempts)
        n_src   = "caller_supplied"
    elif game_attempt_values and len(game_attempt_values) >= 3:
        n_float = statistics.mean(game_attempt_values)
        n_src   = f"mean_from_{len(game_attempt_values)}_games"
        if len(game_attempt_values) < 8:
            data_quality = "PARTIAL"
    elif game_make_values and len(game_make_values) >= 3:
        # Estimate attempts from makes assuming league-average 3P%
        avg_makes = statistics.mean(game_make_values)
        n_float   = avg_makes / _WNBA_LEAGUE_AVG_3PCT
        n_src     = "estimated_from_makes_at_league_avg_pct"
        data_quality = "PARTIAL"
        warnings.append(
            "Attempts estimated from makes using league-average 3P%=34.0%. "
            "Provide actual attempts for better accuracy."
        )
    else:
        n_float = _WNBA_LEAGUE_AVG_3PA
        n_src   = "league_average_fallback"
        data_quality = "MINIMAL"
        warnings.append(
            "No attempt data — league-average 3PA=4.2 used. Result is unreliable."
        )

    # Opponent allowance scaling
    if opponent_3pa_allow is not None:
        ratio    = float(opponent_3pa_allow) / _WNBA_LEAGUE_AVG_3PA
        n_float *= ratio
        adjustments.append(
            f"opponent_3pa_allow={opponent_3pa_allow:.1f}: "
            f"attempts scaled ×{ratio:.3f}"
        )

    n = max(0, round(n_float))

    # ── Resolve p (per-attempt conversion) ──────────────────────────────────
    if three_point_pct is not None:
        p_base = float(three_point_pct)
        p_src  = "caller_supplied"
    elif game_make_values and game_attempt_values and len(game_make_values) >= 3:
        total_makes    = sum(game_make_values)
        total_attempts = sum(game_attempt_values)
        p_base = total_makes / total_attempts if total_attempts > 0 else _WNBA_LEAGUE_AVG_3PCT
        p_src  = f"computed_from_{len(game_make_values)}_games"
    elif game_make_values and len(game_make_values) >= 3:
        # Estimate p from makes using n estimate
        avg_makes = statistics.mean(game_make_values)
        p_base    = avg_makes / n_float if n_float > 0 else _WNBA_LEAGUE_AVG_3PCT
        p_base    = max(0.01, min(0.99, p_base))
        p_src     = "estimated_from_makes_and_estimated_n"
    else:
        p_base = _WNBA_LEAGUE_AVG_3PCT
        p_src  = "league_average_fallback"
        if data_quality == "FULL":
            data_quality = "MINIMAL"
        warnings.append("No 3P% data — league-average 34.0% used.")

    # Shot quality adjustment
    if shot_quality_adj != 0.0:
        p_base = max(0.01, min(0.99, p_base + shot_quality_adj))
        adjustments.append(f"shot_quality_adj={shot_quality_adj:+.3f}")

    p = max(0.01, min(0.99, p_base))

    # ── Compute probability ──────────────────────────────────────────────────
    k_floor = int(math.floor(float(line)))   # P(X > line) = P(X ≥ k_floor + 1) for half-int lines

    if n == 0:
        raw_p = 0.0 if direction.upper() in ("MORE", "OVER", ">") else 1.0
    elif direction.upper() in ("MORE", "OVER", ">"):
        raw_p = 1.0 - _binom_cdf(k_floor, n, p)
    else:
        raw_p = _binom_cdf(k_floor, n, p)

    # ── Conservative lower bound ─────────────────────────────────────────────
    # Reduce n by 1 attempt and p by conversion_uncertainty + 0.03 floor
    p_lb = max(0.01, p - max(conversion_uncertainty, 0.03))
    n_lb = max(0, n - 1)
    if n_lb == 0:
        lb = 0.0 if direction.upper() in ("MORE", "OVER", ">") else 1.0
    elif direction.upper() in ("MORE", "OVER", ">"):
        lb = 1.0 - _binom_cdf(k_floor, n_lb, p_lb)
    else:
        lb = _binom_cdf(k_floor, n_lb, p_lb)

    lb = round(max(0.0, min(1.0, lb)), 5)

    # Standard analytical CI
    raw_var = n * p * (1 - p)
    se      = math.sqrt(raw_var / max(n, 1)) if n > 0 else 0.05
    ci = {
        "lower": round(max(0.0, raw_p - se), 5),
        "upper": round(min(1.0, raw_p + se), 5),
        "width": round(2 * se, 5),
        "note":  (
            "Binomial SE CI — high-variance prop; interval widens with fewer attempts. "
            "Mean near line is NOT a sufficient edge on its own."
        ),
    }

    return {
        "raw_probability":        round(raw_p, 5),
        "calibrated_lower_bound": lb,
        "confidence_interval":    ci,
        "n_attempts_used":        n,
        "p_per_attempt_used":     round(p, 5),
        "n_float_pre_round":      round(n_float, 3),
        "line":                   float(line),
        "direction":              direction.upper(),
        "n_source":               n_src,
        "p_source":               p_src,
        "adjustments_applied":    adjustments,
        "data_quality":           data_quality,
        "data_quality_warning":   "; ".join(warnings) if warnings else None,
        "model_method":           (
            f"Binomial(n={n}, p={p:.4f}) "
            f"P({'≥' if direction.upper() in ('MORE','OVER','>') else '≤'}{k_floor + 1})"
        ),
        "model_name":             "wnba_threes_binomial_v1",
        "high_variance_warning":  (
            "Three-point makes are high-variance. A mean near the line does not imply "
            "a strong distributional edge. Verify attempt distribution and p distribution."
        ),
        "can_execute":            False,
    }
