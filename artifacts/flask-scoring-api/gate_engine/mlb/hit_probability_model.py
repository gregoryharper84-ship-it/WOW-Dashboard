"""
hit_probability_model.py  —  MLB Batter Hit Probability Model
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT

Models P(1+ hits) as a binomial process across projected plate appearances.

Formula:
    P(1+ hits) = 1 − (1 − p_per_pa) ^ n_pa

Where:
    n_pa  = projected plate appearances for this game slot
    p_per_pa = per-PA hit probability, adjusted for:
                - starter handedness split
                - batting order position (affects PA count)
                - park factor
                - bullpen exposure (for deep games)

Used for 0.5 hits props where the settlement requires at least ONE hit.
For higher thresholds (1.5+), a full at-bat simulation is needed instead.

Data sources consumed (in priority order):
    1. Explicit caller-supplied projected_pa and per_pa_hit_prob
    2. Batting average + slot-based PA estimate
    3. League-average fallback (BA=0.250, PA=3.5)

can_execute=False unconditional.
"""
from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# League-average constants (2023-24 MLB)
# ---------------------------------------------------------------------------

_LEAGUE_AVG_BA          = 0.248   # league-average batting average as proxy for p_per_pa
_LEAGUE_AVG_PA_PER_GAME = 3.65    # average PA per player per 9-inning game

# Batting order PA priors (approximate mean PA by slot, 9-inning game)
_ORDER_PA_PRIORS: dict[int, float] = {
    1: 4.3, 2: 4.1, 3: 3.9, 4: 3.8, 5: 3.5,
    6: 3.3, 7: 3.1, 8: 2.9, 9: 2.7,
}

# Starter handedness adjustments (platoon split for right-handed batter vs LHP)
# Rightie vs LHP: typical platoon advantage ~.015 BA uplift; Rightie vs RHP: baseline
# Leftie vs RHP: ~.015 uplift; Leftie vs LHP: discount
_PLATOON_ADJUSTMENTS: dict[tuple[str, str], float] = {
    ("R", "L"):  +0.015,   # RHB vs LHP — platoon advantage
    ("R", "R"):  +0.000,
    ("L", "R"):  +0.015,   # LHB vs RHP — platoon advantage
    ("L", "L"):  -0.010,   # LHB vs LHP — same-handed discount
    ("S", "L"):  +0.015,   # switch hitter vs LHP: bats right-handed
    ("S", "R"):  +0.015,   # switch hitter vs RHP: bats left-handed
}

# Park factor adjustment (neutral=1.0; >1.0 = hitter-friendly)
# Applied multiplicatively to p_per_pa
_NEUTRAL_PARK_FACTOR = 1.0

# ---------------------------------------------------------------------------
# Core model
# ---------------------------------------------------------------------------

def compute_hit_probability(
    projected_pa:      float | None      = None,
    per_pa_hit_prob:   float | None      = None,
    batting_average:   float | None      = None,
    batting_order:     int   | None      = None,
    batter_hand:       str   | None      = None,   # R / L / S
    starter_hand:      str   | None      = None,   # R / L
    park_factor:       float | None      = None,
    pinch_hit_risk:    bool              = False,
    bullpen_pa:        float | None      = None,   # extra PA vs bullpen arms
) -> dict[str, Any]:
    """
    Compute P(1+ hits) = 1 − (1 − p)^n for a batter in one game.

    Parameters
    ----------
    projected_pa     : Explicit projected plate appearances (caller-supplied).
    per_pa_hit_prob  : Explicit per-PA hit probability (overrides batting_average + adjustments).
    batting_average  : Season/period BA used as base p_per_pa when per_pa_hit_prob absent.
    batting_order    : Batting slot 1-9 for PA count prior.
    batter_hand      : Batter handedness R/L/S.
    starter_hand     : Opposing starter handedness R/L.
    park_factor      : Multiplicative park factor (1.0 = neutral).
    pinch_hit_risk   : If True, caps projected PA at 1 (likely to be removed).
    bullpen_pa       : Additional PA expected against bullpen arms.

    Returns
    -------
    {
        "p_at_least_one_hit":   float,
        "p_zero_hits":          float,
        "p_per_pa":             float,
        "n_projected_pa":       float,
        "data_quality":         str,   # FULL / PARTIAL / MINIMAL
        "data_quality_warning": str | None,
        "model_method":         str,
        "input_summary":        dict,
        "can_execute":          False,
    }
    """
    warnings: list[str] = []
    data_quality = "FULL"

    # ── Step 1: resolve n_projected_pa ─────────────────────────────────────
    if projected_pa is not None:
        n_pa = float(projected_pa)
        pa_source = "caller_supplied"
    elif batting_order is not None and batting_order in _ORDER_PA_PRIORS:
        n_pa = _ORDER_PA_PRIORS[batting_order]
        pa_source = f"order_slot_{batting_order}_prior"
        data_quality = "PARTIAL"
    else:
        n_pa = _LEAGUE_AVG_PA_PER_GAME
        pa_source = "league_average_fallback"
        data_quality = "MINIMAL"
        warnings.append(
            "No batting order or projected PA supplied — using league-average PA=3.65. "
            "Accuracy limited."
        )

    # Bullpen PA boost
    if bullpen_pa is not None:
        n_pa += float(bullpen_pa)

    # Pinch-hit risk cap
    if pinch_hit_risk:
        n_pa = min(n_pa, 1.0)
        warnings.append("pinch_hit_risk=True — PA capped at 1.")

    # ── Step 2: resolve p_per_pa ────────────────────────────────────────────
    if per_pa_hit_prob is not None:
        p_base = float(per_pa_hit_prob)
        p_source = "caller_supplied"
    elif batting_average is not None:
        p_base = float(batting_average)
        p_source = "batting_average"
        if data_quality == "FULL":
            data_quality = "PARTIAL"
    else:
        p_base = _LEAGUE_AVG_BA
        p_source = "league_average_fallback"
        data_quality = "MINIMAL"
        warnings.append(
            "No batting average or per_pa_hit_prob supplied — using league-average BA=0.248. "
            "Accuracy limited."
        )

    # ── Step 3: platoon adjustment ──────────────────────────────────────────
    platoon_adj = 0.0
    if batter_hand and starter_hand:
        key = (batter_hand.upper()[:1], starter_hand.upper()[:1])
        platoon_adj = _PLATOON_ADJUSTMENTS.get(key, 0.0)
    elif batter_hand or starter_hand:
        warnings.append(
            "Only one of batter_hand / starter_hand provided — platoon adjustment skipped."
        )

    # ── Step 4: park factor ─────────────────────────────────────────────────
    pf = float(park_factor) if park_factor is not None else _NEUTRAL_PARK_FACTOR

    # ── Step 5: final p ─────────────────────────────────────────────────────
    p = max(0.001, min(0.999, (p_base + platoon_adj) * pf))

    # ── Step 6: binomial result ─────────────────────────────────────────────
    p_zero = (1.0 - p) ** n_pa
    p_one_plus = 1.0 - p_zero

    method = (
        f"Binomial: P(1+) = 1 − (1−{p:.4f})^{n_pa:.2f}; "
        f"p_source={p_source}; pa_source={pa_source}; "
        f"platoon_adj={platoon_adj:+.3f}; park_factor={pf:.3f}"
    )

    return {
        "p_at_least_one_hit":   round(p_one_plus, 5),
        "p_zero_hits":          round(p_zero, 5),
        "p_per_pa":             round(p, 5),
        "n_projected_pa":       round(n_pa, 2),
        "platoon_adjustment":   round(platoon_adj, 4),
        "park_factor_applied":  round(pf, 4),
        "data_quality":         data_quality,
        "data_quality_warning": "; ".join(warnings) if warnings else None,
        "model_method":         method,
        "input_summary": {
            "projected_pa":    projected_pa,
            "per_pa_hit_prob": per_pa_hit_prob,
            "batting_average": batting_average,
            "batting_order":   batting_order,
            "batter_hand":     batter_hand,
            "starter_hand":    starter_hand,
            "park_factor":     park_factor,
            "pinch_hit_risk":  pinch_hit_risk,
            "bullpen_pa":      bullpen_pa,
        },
        "can_execute": False,
    }


def score_zero_point_five_hits(
    batting_average:   float | None = None,
    batting_order:     int   | None = None,
    batter_hand:       str   | None = None,
    starter_hand:      str   | None = None,
    park_factor:       float | None = None,
    pinch_hit_risk:    bool         = False,
    calibration_floor: float        = 0.05,   # subtract from raw p as conservative haircut
) -> dict[str, Any]:
    """
    Convenience scorer for standard 0.5-hits props.

    Adds a calibration floor adjustment to produce a conservative lower bound.
    Returns raw_probability and calibrated_lower_bound.
    """
    result = compute_hit_probability(
        batting_average = batting_average,
        batting_order   = batting_order,
        batter_hand     = batter_hand,
        starter_hand    = starter_hand,
        park_factor     = park_factor,
        pinch_hit_risk  = pinch_hit_risk,
    )

    raw_p = result["p_at_least_one_hit"]
    lb    = max(0.0, raw_p - calibration_floor)

    result["raw_probability"]          = raw_p
    result["calibrated_lower_bound"]   = round(lb, 5)
    result["calibration_floor_applied"] = calibration_floor
    result["confidence_interval"]       = {
        "lower": round(lb, 5),
        "upper": round(min(1.0, raw_p + calibration_floor), 5),
        "width": round(calibration_floor * 2, 5),
        "note":  "Confidence interval is ±calibration_floor around raw probability.",
    }
    result["model_name"] = "mlb_binomial_hit_v1"
    return result
