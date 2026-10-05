"""Research-only NFL moneyline challenger package for #1342.

This module formalizes the retrospective winner of the structural audit without
changing production model behavior. It owns no production route, cannot publish,
cannot promote, and cannot execute.

Chosen research stack:
- stationary history sufficiency instead of cumulative prior-game counters;
- 224-day half-life weighting over the latest eight prior games;
- Platt calibration;
- composite lower bound = min(local Wilson-90% k=50, block-bootstrap q10).

The lower-bound method is a research decision-confidence construct, not a
frequentist confidence interval for the latent game win probability.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

CHALLENGER_ID = "NFL_ML_STATIONARY_DECAY_PLATT_COMPOSITE_LB_V1"
FEATURE_SCHEMA_VERSION = "NFL_EVENT_PREGAME_STATIONARY_DECAY_V1"
MODEL_FAMILY = "NFL_OUTRIGHT_WIN_LOGREG_STATIONARY_DECAY_V1"
CALIBRATION_METHOD = "PLATT_TIME_SPLIT_V1"
LOWER_BOUND_METHOD = "LOCAL_WILSON90_K50_MIN_BLOCK_BOOTSTRAP_Q10_V1"

HISTORY_SUFFICIENCY_GAMES = 8
ROLLING_GAMES = 8
DECAY_HALF_LIFE_DAYS = 224
LOCAL_CALIBRATION_K = 50
LOCAL_WILSON_Z = 1.645
BOOTSTRAP_QUANTILE = 0.10
BOOTSTRAP_BLOCK_GAMES = 32


@dataclass(frozen=True)
class LowerBoundEvidence:
    point_probability: float
    local_wilson_lower: float
    bootstrap_q10_lower: float
    composite_lower: float
    local_sample_n: int
    bootstrap_replicates: int
    method: str = LOWER_BOUND_METHOD


def wilson_lower(successes: int, n: int, *, z: float = LOCAL_WILSON_Z) -> float:
    if n <= 0:
        raise ValueError("NFL_CHALLENGER_LOCAL_CALIBRATION_EMPTY")
    if successes < 0 or successes > n:
        raise ValueError("NFL_CHALLENGER_LOCAL_CALIBRATION_SUCCESS_COUNT_INVALID")
    p = successes / n
    zz = z * z
    denominator = 1.0 + zz / n
    center = p + zz / (2.0 * n)
    adjustment = z * sqrt((p * (1.0 - p) + zz / (4.0 * n)) / n)
    return max(0.0, (center - adjustment) / denominator)


def nearest_local_lower(
    point_probability: float,
    calibration_decisions: Iterable[tuple[float, int]],
    *,
    k: int = LOCAL_CALIBRATION_K,
) -> tuple[float, int]:
    p = float(point_probability)
    if not 0.5 <= p <= 1.0:
        raise ValueError("NFL_CHALLENGER_SELECTED_PROBABILITY_OUT_OF_RANGE")
    rows = [
        (float(prob), int(hit))
        for prob, hit in calibration_decisions
        if 0.5 <= float(prob) <= 1.0 and int(hit) in (0, 1)
    ]
    rows.sort(key=lambda row: abs(row[0] - p))
    selected = rows[:k]
    if len(selected) < k:
        raise ValueError(
            f"NFL_CHALLENGER_LOCAL_CALIBRATION_TOO_THIN:{len(selected)}:{k}"
        )
    successes = sum(hit for _, hit in selected)
    return min(p, wilson_lower(successes, len(selected))), len(selected)


def empirical_lower(
    selected_side_probabilities: Sequence[float],
    *,
    quantile: float = BOOTSTRAP_QUANTILE,
) -> float:
    values = sorted(float(v) for v in selected_side_probabilities)
    if not values:
        raise ValueError("NFL_CHALLENGER_BOOTSTRAP_EMPTY")
    if any(v < 0.0 or v > 1.0 for v in values):
        raise ValueError("NFL_CHALLENGER_BOOTSTRAP_PROBABILITY_INVALID")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("NFL_CHALLENGER_BOOTSTRAP_QUANTILE_INVALID")
    index = int(quantile * (len(values) - 1))
    return values[index]


def composite_lower_bound(
    point_probability: float,
    calibration_decisions: Iterable[tuple[float, int]],
    bootstrap_selected_side_probabilities: Sequence[float],
) -> LowerBoundEvidence:
    p = float(point_probability)
    local_lower, local_n = nearest_local_lower(p, calibration_decisions)
    bootstrap_lower = min(
        p,
        empirical_lower(
            bootstrap_selected_side_probabilities,
            quantile=BOOTSTRAP_QUANTILE,
        ),
    )
    composite = min(p, local_lower, bootstrap_lower)
    return LowerBoundEvidence(
        point_probability=p,
        local_wilson_lower=local_lower,
        bootstrap_q10_lower=bootstrap_lower,
        composite_lower=composite,
        local_sample_n=local_n,
        bootstrap_replicates=len(bootstrap_selected_side_probabilities),
    )


def build_forward_shadow_record(
    *,
    official_event_id: str,
    provider_event_id: str | None,
    event_start_time_utc: str,
    prediction_created_at: str,
    home_team: str,
    away_team: str,
    selected_participant: str,
    calibrated_probability: float,
    lower_bound: LowerBoundEvidence,
    training_cutoff: str,
    calibration_season: int,
    source_feature_hash: str,
    research_metadata: dict | None = None,
) -> dict:
    if lower_bound.point_probability != float(calibrated_probability):
        raise ValueError("NFL_CHALLENGER_LOWER_BOUND_POINT_MISMATCH")
    if not official_event_id or not selected_participant:
        raise ValueError("NFL_CHALLENGER_FORWARD_IDENTITY_MISSING")
    return {
        "challenger_id": CHALLENGER_ID,
        "official_event_id": str(official_event_id),
        "provider_event_id": None if provider_event_id is None else str(provider_event_id),
        "event_start_time_utc": str(event_start_time_utc),
        "prediction_created_at": str(prediction_created_at),
        "home_team": str(home_team),
        "away_team": str(away_team),
        "selected_participant": str(selected_participant),
        "calibrated_probability": float(calibrated_probability),
        "local_wilson_lower": float(lower_bound.local_wilson_lower),
        "bootstrap_q10_lower": float(lower_bound.bootstrap_q10_lower),
        "composite_lower_bound": float(lower_bound.composite_lower),
        "lower_bound_method": LOWER_BOUND_METHOD,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_family": MODEL_FAMILY,
        "calibration_method": CALIBRATION_METHOD,
        "training_cutoff": str(training_cutoff),
        "calibration_season": int(calibration_season),
        "source_feature_hash": str(source_feature_hash),
        "research_metadata": dict(research_metadata or {}),
        "lifecycle_state": "FORWARD_SHADOW",
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


__all__ = [
    "BOOTSTRAP_BLOCK_GAMES",
    "BOOTSTRAP_QUANTILE",
    "CALIBRATION_METHOD",
    "CAN_EXECUTE",
    "CHALLENGER_ID",
    "DECAY_HALF_LIFE_DAYS",
    "FEATURE_SCHEMA_VERSION",
    "HISTORY_SUFFICIENCY_GAMES",
    "LOCAL_CALIBRATION_K",
    "LOWER_BOUND_METHOD",
    "LowerBoundEvidence",
    "MODEL_FAMILY",
    "PROBABILITY_PUBLISHABLE",
    "PROMOTION_AUTHORIZED",
    "ROLLING_GAMES",
    "TERMINAL_AUTHORITY",
    "build_forward_shadow_record",
    "composite_lower_bound",
    "empirical_lower",
    "nearest_local_lower",
    "wilson_lower",
]
