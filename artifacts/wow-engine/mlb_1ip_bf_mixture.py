"""Research-only player-conditioned MLB 1IP mixture model.

The active aggregate 1IP artifact estimates total first-inning pitch counts
conditional on batters-faced bucket (3, 4, 5+), but its serving scorer mixes
those conditional distributions with one league-wide BF vector. That makes
same-line pitcher scores identical.

This module keeps the validated conditional total-pitch distributions intact
and changes only the mixture weights: a pitcher's recent BF history is shrunk
toward the separately validated league BF prior. It therefore creates genuine
player discrimination without replacing the aggregate pitch-count evidence.

This module is research-only until a disjoint test and governance review pass.
It cannot publish a governed probability or execute a wager/order.
"""
from __future__ import annotations

from typing import Any, Iterable

from mlb_1ip_bf_model import MODEL_FAMILY as BF_MODEL_FAMILY
from mlb_1ip_bf_model import score_pitcher as score_bf_pitcher
from mlb_1ip_empirical_pmf import MODEL_FAMILY as AGGREGATE_MODEL_FAMILY

MODEL_FAMILY = "MLB_1IP_PLAYER_CONDITIONED_BF_MIXTURE_V1"
CAN_EXECUTE = False
CATEGORIES = ("3", "4", "5_PLUS")


def _history_counts(recent_batters_faced: Iterable[int]) -> dict[str, int]:
    values = [int(v) for v in recent_batters_faced]
    if len(values) > 10:
        values = values[-10:]
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
    return counts


def _conditional_side_probability(
    bucket_counts: dict[str, Any], *, line_value: float, side: str
) -> float:
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


def score_player_bf_mixture(
    *,
    aggregate_artifact_payload: dict[str, Any],
    bf_league_prior: dict[str, float],
    bf_alpha: float,
    pitcher_id: int,
    recent_batters_faced: Iterable[int],
    line_value: float,
    side: str,
) -> dict[str, Any]:
    """Score one exact 1IP line using player-conditioned BF mixture weights."""
    if aggregate_artifact_payload.get("model_family") != AGGREGATE_MODEL_FAMILY:
        raise ValueError("MLB_1IP_AGGREGATE_ARTIFACT_FAMILY_INVALID")
    side_norm = str(side or "").strip().upper()
    if side_norm not in {"MORE", "LESS"}:
        raise ValueError("MLB_1IP_DIRECTION_INVALID")

    counts = aggregate_artifact_payload.get("conditional_total_pitch_counts") or {}
    if set(counts) != set(CATEGORIES):
        raise ValueError("MLB_1IP_ARTIFACT_BUCKETS_INVALID")

    history_counts = _history_counts(recent_batters_faced)
    bf = score_bf_pitcher(
        pitcher_id=int(pitcher_id),
        league_prior=bf_league_prior,
        pitcher_counts={int(pitcher_id): history_counts},
        alpha=float(bf_alpha),
    )
    weights = {
        "3": float(bf["P_BF_3"]),
        "4": float(bf["P_BF_4"]),
        "5_PLUS": float(bf["P_BF_GE_5"]),
    }

    conditional = {
        bucket: _conditional_side_probability(
            counts[bucket], line_value=float(line_value), side=side_norm
        )
        for bucket in CATEGORIES
    }
    selected_probability = sum(weights[b] * conditional[b] for b in CATEGORIES)

    aggregate_weights = {
        k: float((aggregate_artifact_payload.get("bf_weights") or {}).get(k, 0.0))
        for k in CATEGORIES
    }
    aggregate_baseline = sum(aggregate_weights[b] * conditional[b] for b in CATEGORIES)

    return {
        "model_family": MODEL_FAMILY,
        "aggregate_model_family": AGGREGATE_MODEL_FAMILY,
        "bf_model_family": BF_MODEL_FAMILY,
        "pitcher_id": int(pitcher_id),
        "line_value": float(line_value),
        "side": side_norm,
        "pitcher_history_n": int(bf["pitcher_history_n"]),
        "bf_alpha": float(bf_alpha),
        "P_BF_3": weights["3"],
        "P_BF_4": weights["4"],
        "P_BF_GE_5": weights["5_PLUS"],
        "conditional_side_probability_by_bf": conditional,
        "aggregate_baseline_probability": aggregate_baseline,
        "selected_probability": selected_probability,
        "player_probability_delta_vs_aggregate": selected_probability - aggregate_baseline,
        "player_discrimination_status": "PLAYER_CONDITIONED_BF_MIXTURE",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
