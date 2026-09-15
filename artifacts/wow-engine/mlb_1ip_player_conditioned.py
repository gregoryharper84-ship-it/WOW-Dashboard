"""Player-conditioned MLB 1IP scorer for the governed composite artifact.

The composite preserves the certified aggregate conditional total-pitch PMF and
changes only the BF mixture weights using the validated pitcher-specific
Dirichlet-shrunk BF posterior.  The aggregate uncertainty interval is translated
by the exact player-conditioning probability delta, preserving its width while
allowing same-line pitcher discrimination.

This module performs no registry mutation and can never execute a wager/order.
"""
from __future__ import annotations

from typing import Any, Iterable

from mlb_1ip_bf_model import score_pitcher as score_bf_pitcher
from mlb_1ip_empirical_pmf import MODEL_FAMILY as AGGREGATE_MODEL_FAMILY
from mlb_1ip_empirical_pmf import score_empirical_pmf

MODEL_FAMILY = "MLB_1IP_PLAYER_CONDITIONED_BF_MIXTURE_V1"
ARTIFACT_FORMAT = "JSON_PLAYER_CONDITIONED_BF_MIXTURE_V1"
CALIBRATOR_VERSION = "MLB_1IP_PLAYER_CONDITIONED_TEMPORAL_CAL_V1"
FEATURE_TRANSFORM_VERSION = "MLB_1IP_PLAYER_CONDITIONED_BF_MIXTURE_V1"
BOUNDS_METHOD_VERSION = "MLB_1IP_PLAYER_SHIFTED_AGGREGATE_WILSON_V1"
CAN_EXECUTE = False
CATEGORIES = ("3", "4", "5_PLUS")
MIN_HISTORY = 5
MAX_HISTORY = 10


def _bucket_counts(recent_batters_faced: Iterable[int]) -> tuple[list[int], dict[str, int]]:
    values = [int(v) for v in recent_batters_faced]
    if len(values) > MAX_HISTORY:
        values = values[-MAX_HISTORY:]
    if len(values) < MIN_HISTORY:
        raise ValueError("MLB_1IP_PLAYER_CONDITIONING_HISTORY_INSUFFICIENT")
    counts = {"3": 0, "4": 0, "5_PLUS": 0}
    for bf in values:
        if bf == 3:
            counts["3"] += 1
        elif bf == 4:
            counts["4"] += 1
        elif bf >= 5:
            counts["5_PLUS"] += 1
        else:
            raise ValueError("MLB_1IP_BF_HISTORY_INVALID")
    return values, counts


def _conditional_side_probability(bucket_counts: dict[str, Any], *, line_value: float, side: str) -> float:
    total = sum(int(v) for v in bucket_counts.values())
    if total <= 0:
        raise ValueError("MLB_1IP_ARTIFACT_BUCKET_EMPTY")
    selected = 0
    for pitch_text, count_raw in bucket_counts.items():
        pitches = int(pitch_text)
        count = int(count_raw)
        if side == "MORE" and pitches > line_value:
            selected += count
        elif side == "LESS" and pitches < line_value:
            selected += count
    return selected / total


def score_player_conditioned_1ip(
    *,
    artifact_payload: dict[str, Any],
    pitcher_id: int,
    recent_batters_faced: Iterable[int],
    line_value: float,
    side: str,
) -> dict[str, Any]:
    if artifact_payload.get("model_family") != MODEL_FAMILY:
        raise ValueError("MLB_1IP_PLAYER_ARTIFACT_FAMILY_INVALID")
    if artifact_payload.get("artifact_format") != ARTIFACT_FORMAT:
        raise ValueError("MLB_1IP_PLAYER_ARTIFACT_FORMAT_INVALID")

    aggregate = artifact_payload.get("aggregate_artifact_payload") or {}
    if aggregate.get("model_family") != AGGREGATE_MODEL_FAMILY:
        raise ValueError("MLB_1IP_PLAYER_AGGREGATE_ARTIFACT_INVALID")
    expected_aggregate_checksum = str(artifact_payload.get("aggregate_artifact_checksum") or "")
    if expected_aggregate_checksum and str(aggregate.get("artifact_checksum") or "") != expected_aggregate_checksum:
        raise ValueError("MLB_1IP_PLAYER_AGGREGATE_CHECKSUM_MISMATCH")

    side_norm = str(side or "").strip().upper()
    if side_norm not in {"MORE", "LESS"}:
        raise ValueError("MLB_1IP_DIRECTION_INVALID")

    values, counts = _bucket_counts(recent_batters_faced)
    prior = artifact_payload.get("bf_league_prior") or {}
    alpha = float(artifact_payload.get("bf_alpha") or 0.0)
    bf = score_bf_pitcher(
        pitcher_id=int(pitcher_id),
        league_prior=prior,
        pitcher_counts={int(pitcher_id): counts},
        alpha=alpha,
    )
    weights = {
        "3": float(bf["P_BF_3"]),
        "4": float(bf["P_BF_4"]),
        "5_PLUS": float(bf["P_BF_GE_5"]),
    }

    conditional_counts = aggregate.get("conditional_total_pitch_counts") or {}
    if set(conditional_counts) != set(CATEGORIES):
        raise ValueError("MLB_1IP_ARTIFACT_BUCKETS_INVALID")
    conditional_more = {
        bucket: _conditional_side_probability(conditional_counts[bucket], line_value=float(line_value), side="MORE")
        for bucket in CATEGORIES
    }
    conditional_less = {
        bucket: _conditional_side_probability(conditional_counts[bucket], line_value=float(line_value), side="LESS")
        for bucket in CATEGORIES
    }
    p_more = sum(weights[b] * conditional_more[b] for b in CATEGORIES)
    p_less = sum(weights[b] * conditional_less[b] for b in CATEGORIES)
    selected = p_more if side_norm == "MORE" else p_less

    aggregate_scored = score_empirical_pmf(
        aggregate,
        line_value=float(line_value),
        side=side_norm,
    )
    delta = selected - float(aggregate_scored["selected_probability"])
    lower = max(0.0, min(1.0, float(aggregate_scored["lower_bound"]) + delta))
    upper = max(0.0, min(1.0, float(aggregate_scored["upper_bound"]) + delta))
    lower = min(lower, selected)
    upper = max(upper, selected)

    return {
        "model_family": MODEL_FAMILY,
        "aggregate_model_family": AGGREGATE_MODEL_FAMILY,
        "calibrator_version": CALIBRATOR_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "bounds_method_version": BOUNDS_METHOD_VERSION,
        "pitcher_id": int(pitcher_id),
        "pitcher_history_n": len(values),
        "bf_alpha": alpha,
        "P_BF_3": weights["3"],
        "P_BF_4": weights["4"],
        "P_BF_GE_5": weights["5_PLUS"],
        "P_MORE": p_more,
        "P_LESS": p_less,
        "prob_push": max(0.0, 1.0 - p_more - p_less),
        "selected_probability": selected,
        "aggregate_baseline_probability": float(aggregate_scored["selected_probability"]),
        "player_probability_delta_vs_aggregate": delta,
        "lower_bound": lower,
        "upper_bound": upper,
        "selected_support_n": int(aggregate_scored["selected_support_n"]),
        "conditional_more_by_bf_bucket": conditional_more,
        "player_discrimination_status": "PLAYER_CONDITIONED_BF_MIXTURE",
        "probability_publishable": False,
        "can_execute": False,
    }
