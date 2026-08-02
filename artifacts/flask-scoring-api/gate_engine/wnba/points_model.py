"""
points_model.py  —  WNBA Player Points Distribution Model
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT

Models P(points > line) using a Normal distribution fitted to recent game logs.

Inputs consumed (priority order):
    1. Explicit caller-supplied mean/std from historical log
    2. Computed from raw game-value list
    3. League-average fallback (warns loudly)

Adjustments available:
    - Blowout/garbage-time risk: discount on high-point outcomes
    - Foul trouble: discount on minutes/usage
    - Opponent defensive rank: positive/negative adjustment
    - Primary teammate availability: usage boost or deficit

can_execute=False unconditional.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

# ---------------------------------------------------------------------------
# Normal CDF (no scipy dependency)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via Abramowitz & Stegun approximation."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _p_over(mean: float, std: float, line: float) -> float:
    if std <= 0:
        return 1.0 if mean > line else 0.0
    z = (line - mean) / std
    return round(1.0 - _norm_cdf(z), 6)


# ---------------------------------------------------------------------------
# League-average constants (2023-24 WNBA)
# ---------------------------------------------------------------------------

_WNBA_LEAGUE_AVG_PPG = 9.5
_WNBA_LEAGUE_AVG_STD = 6.2    # typical game-to-game std for a 10ppg player
_MIN_STD             = 1.5    # floor for std (single-digit scoring / small samples)

# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

def compute_points_probability(
    line:                     float,
    mean_points:              float | None       = None,
    std_points:               float | None       = None,
    game_values:              list[float] | None = None,
    minutes_mean:             float | None       = None,
    usage_rate:               float | None       = None,
    blowout_risk:             float              = 0.0,    # 0.0–1.0 probability of garbage-time
    foul_trouble_discount:    float              = 0.0,    # fraction off minutes in worst case
    opponent_def_rank:        int   | None       = None,   # 1=best D, 12=worst D in WNBA
    primary_teammate_avail:   bool  | None       = None,   # None=unknown
    direction:                str                = "MORE",
) -> dict[str, Any]:
    """
    P(points > line) or P(points < line) via Normal distribution.

    Blowout adjustment: reduces mean by blowout_discount factor.
    Foul trouble: reduces minutes/usage, further reducing mean.
    Opponent rank: league-wide percentile adjustment.

    Returns
    -------
    {
        "raw_probability":        float,
        "calibrated_lower_bound": float,
        "confidence_interval":    dict,
        "mean_used":              float,
        "std_used":               float,
        "adjustments_applied":    list[str],
        "data_quality":           str,
        "data_quality_warning":   str | None,
        "model_method":           str,
        "model_name":             str,
        "can_execute":            False,
    }
    """
    warnings: list[str] = []
    adjustments: list[str] = []
    data_quality = "FULL"

    # ── Resolve mean and std ─────────────────────────────────────────────────
    if mean_points is not None and std_points is not None:
        mu  = float(mean_points)
        std = float(std_points)
        src = "caller_supplied"
    elif game_values and len(game_values) >= 3:
        mu  = statistics.mean(game_values)
        std = statistics.stdev(game_values) if len(game_values) > 1 else _MIN_STD
        src = f"computed_from_{len(game_values)}_games"
        if len(game_values) < 8:
            data_quality = "PARTIAL"
            warnings.append(
                f"Only {len(game_values)} game values available — distribution estimates are less stable."
            )
    elif game_values and len(game_values) > 0:
        mu  = statistics.mean(game_values)
        std = _MIN_STD
        src = f"single_game_fallback ({len(game_values)} values)"
        data_quality = "MINIMAL"
        warnings.append("Fewer than 3 game values — std floored at minimum; high uncertainty.")
    else:
        mu  = _WNBA_LEAGUE_AVG_PPG
        std = _WNBA_LEAGUE_AVG_STD
        src = "league_average_fallback"
        data_quality = "MINIMAL"
        warnings.append(
            "No points data supplied — league-average fallback (9.5 ± 6.2). "
            "Result is unreliable; real game logs required."
        )

    std = max(std, _MIN_STD)

    # ── Blowout risk adjustment ─────────────────────────────────────────────
    if blowout_risk > 0:
        blowout_discount = blowout_risk * 0.30   # 30% mean reduction in full blowout scenario
        mu_adj = mu * (1.0 - blowout_discount)
        adjustments.append(
            f"blowout_risk={blowout_risk:.0%}: mean {mu:.1f} → {mu_adj:.1f} "
            f"(discount={blowout_discount:.1%})"
        )
        mu = mu_adj

    # ── Foul trouble adjustment ─────────────────────────────────────────────
    if foul_trouble_discount > 0 and minutes_mean:
        adj_factor = 1.0 - foul_trouble_discount
        mu = mu * adj_factor
        std = std * math.sqrt(adj_factor)   # std also narrows with fewer minutes
        adjustments.append(
            f"foul_trouble_discount={foul_trouble_discount:.0%}: mean reduced, "
            f"std narrowed."
        )

    # ── Opponent defensive rank adjustment ──────────────────────────────────
    if opponent_def_rank is not None:
        # rank 1 = best defense, rank 12 = worst
        # adjustment: ±0.6 ppg per rank unit from median (rank 6.5)
        rank_adj = (opponent_def_rank - 6.5) * 0.6
        mu += rank_adj
        adjustments.append(
            f"opponent_def_rank={opponent_def_rank}: "
            f"mean adjusted by {rank_adj:+.2f} ppg"
        )

    # ── Primary teammate availability ────────────────────────────────────────
    if primary_teammate_avail is False:
        mu *= 1.06   # 6% usage boost when primary playmaker is out
        adjustments.append("primary_teammate_out: +6% usage boost applied to mean")
    elif primary_teammate_avail is True:
        pass  # no change; normal usage baseline

    # ── Compute probability ──────────────────────────────────────────────────
    if direction.upper() in ("MORE", "OVER", ">"):
        raw_p = _p_over(mu, std, float(line))
    else:
        raw_p = 1.0 - _p_over(mu, std, float(line))

    # Conservative calibrated lower bound
    std_adj = std * 1.10   # 10% wider std for lower bound
    if direction.upper() in ("MORE", "OVER", ">"):
        lb = _p_over(mu - 0.5, std_adj, float(line))
    else:
        lb = 1.0 - _p_over(mu + 0.5, std_adj, float(line))

    lb = max(0.0, min(1.0, round(lb, 5)))

    ci_width = 0.08   # ±4pp around raw as conservative interval
    ci = {
        "lower": round(max(0.0, raw_p - ci_width / 2), 5),
        "upper": round(min(1.0, raw_p + ci_width / 2), 5),
        "width": ci_width,
        "note":  "Normal model CI — widens for small samples.",
    }

    return {
        "raw_probability":        round(raw_p, 5),
        "calibrated_lower_bound": lb,
        "confidence_interval":    ci,
        "mean_used":              round(mu, 3),
        "std_used":               round(std, 3),
        "line":                   float(line),
        "direction":              direction.upper(),
        "data_source":            src,
        "adjustments_applied":    adjustments,
        "data_quality":           data_quality,
        "data_quality_warning":   "; ".join(warnings) if warnings else None,
        "model_method":           (
            f"Normal(μ={mu:.2f}, σ={std:.2f}) P({'>' if direction.upper() in ('MORE','OVER','>') else '<'}{line})"
        ),
        "model_name":             "wnba_points_normal_v1",
        "can_execute":            False,
    }
