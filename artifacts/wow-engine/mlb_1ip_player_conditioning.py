"""Player-conditioned MLB 1IP empirical research scorer.

This module fixes a specific discrimination failure in the aggregate 1IP PMF:
identical exact lines produced identical probabilities for every pitcher. It
uses the aggregate empirical model as the league prior and applies the
pre-existing temporally tested pitcher-history shrinkage formulation.

This file by itself has no serving or publication authority. The shrinkage
alpha must come from a lineage-bound temporal validation packet / certified
artifact before a caller may treat the result as governed model probability.
can_execute is permanently false.
"""
from __future__ import annotations

import math
from typing import Any

from mlb_1ip_empirical_pmf import score_empirical_pmf

MODEL_FAMILY = "MLB_1IP_PITCHER_SHRUNK_EMPIRICAL_PMF_V2"
CAN_EXECUTE = False
MIN_RECENT_STARTS = 5
MAX_RECENT_STARTS = 10


def _wilson_fractional(successes: float, n: float, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval supporting fractional pseudo-counts from shrinkage."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((p * (1.0 - p) / n) + (z * z / (4.0 * n * n))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def score_player_conditioned_1ip(
    *,
    aggregate_artifact_payload: dict[str, Any],
    recent_1ip_pitch_totals: list[int] | tuple[int, ...],
    line_value: float,
    side: str,
    shrinkage_alpha: float,
    max_recent_starts: int = MAX_RECENT_STARTS,
) -> dict[str, Any]:
    """Shrink a pitcher's exact-line recent evidence toward the league prior.

    This is the same mathematical form used by the existing 2024-tune / 2025
    untouched temporal shadow challenger:

        posterior = (alpha * league_prior + recent_hits) / (alpha + n)

    The function intentionally refuses thin history and invalid alpha values.
    It does not infer or tune alpha at runtime.
    """
    side_norm = str(side or "").strip().upper()
    if side_norm not in {"MORE", "LESS"}:
        raise ValueError("MLB_1IP_DIRECTION_INVALID")
    alpha = float(shrinkage_alpha)
    if not math.isfinite(alpha) or alpha <= 0:
        raise ValueError("MLB_1IP_SHRINKAGE_ALPHA_INVALID")

    recent = [int(v) for v in recent_1ip_pitch_totals][-int(max_recent_starts):]
    if len(recent) < MIN_RECENT_STARTS:
        raise ValueError("MLB_1IP_PLAYER_HISTORY_INSUFFICIENT")

    baseline = score_empirical_pmf(
        aggregate_artifact_payload,
        line_value=float(line_value),
        side=side_norm,
    )
    league_prior = float(baseline["selected_probability"])
    if side_norm == "MORE":
        recent_hits = sum(1 for pitches in recent if pitches > float(line_value))
    else:
        recent_hits = sum(1 for pitches in recent if pitches < float(line_value))

    effective_successes = alpha * league_prior + recent_hits
    effective_n = alpha + len(recent)
    probability = effective_successes / effective_n
    lower_bound, upper_bound = _wilson_fractional(effective_successes, effective_n)

    return {
        "model_family": MODEL_FAMILY,
        "aggregate_prior_model_family": baseline["model_family"],
        "side": side_norm,
        "line_value": float(line_value),
        "league_prior_probability": league_prior,
        "recent_start_n": len(recent),
        "recent_hits": recent_hits,
        "recent_hit_rate": recent_hits / len(recent),
        "recent_1ip_pitch_totals": recent,
        "shrinkage_alpha": alpha,
        "selected_probability": probability,
        "raw_probability": probability,
        "calibrated_probability": probability,
        "calibrated_probability_lower_bound": lower_bound,
        "calibrated_probability_upper_bound": upper_bound,
        "effective_support_n": effective_n,
        "player_discrimination_status": "PLAYER_CONDITIONED",
        "probability_publishable": False,
        "can_execute": False,
    }
