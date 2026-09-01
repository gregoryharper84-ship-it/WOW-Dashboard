"""Formal research/runtime contract for the MLB 1IP empirical PMF challenger.

This module turns the successful temporal-shadow challenger into a deterministic
artifact shape without promoting or activating it. The artifact models total
first-inning pitches conditional on batters faced (3, 4, 5+), then mixes those
conditional empirical distributions with the artifact's fitted BF weights.

Important governance properties:
- no caller/model metadata can make the artifact serving-eligible;
- no probability is publishable from this module by itself;
- no database writes or promotion occur here;
- can_execute is permanently false.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from typing import Any, Iterable

MODEL_FAMILY = "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1"
ARTIFACT_FORMAT = "JSON_CONDITIONAL_DISCRETE_PMF_V1"
CALIBRATOR_VERSION = "MLB_1IP_EMPIRICAL_TEMPORAL_CAL_V1"
FEATURE_TRANSFORM_VERSION = "MLB_1IP_BF_CONDITIONAL_TOTAL_PITCH_PMF_V1"
CAN_EXECUTE = False


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _bf_bucket(bf: int) -> str:
    if int(bf) == 3:
        return "3"
    if int(bf) == 4:
        return "4"
    return "5_PLUS"


def _wilson_interval(count: int, n: int, *, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = count / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((p * (1.0 - p) / n) + (z * z / (4.0 * n * n))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def fit_empirical_pmf(rows: Iterable[Any]) -> dict[str, Any]:
    """Fit a compact discrete total-pitches PMF from first-inning rows.

    ``rows`` need only expose integer-ish ``bf`` and ``pitches`` attributes.
    The output intentionally contains counts rather than duplicated raw samples,
    keeping the serving artifact compact and auditable.
    """
    grouped: dict[str, Counter[int]] = {
        "3": Counter(),
        "4": Counter(),
        "5_PLUS": Counter(),
    }
    n = 0
    for row in rows:
        bf = int(row.bf)
        pitches = int(row.pitches)
        if bf < 3 or pitches < 1:
            continue
        grouped[_bf_bucket(bf)][pitches] += 1
        n += 1
    if n < 1000:
        raise ValueError("MLB_1IP_TRAINING_ROWS_INSUFFICIENT")
    if any(sum(counter.values()) == 0 for counter in grouped.values()):
        raise ValueError("MLB_1IP_BF_BUCKET_SUPPORT_INSUFFICIENT")

    bf_counts = {key: sum(counter.values()) for key, counter in grouped.items()}
    payload = {
        "model_family": MODEL_FAMILY,
        "artifact_format": ARTIFACT_FORMAT,
        "bf_weights": {key: bf_counts[key] / n for key in grouped},
        "conditional_total_pitch_counts": {
            key: {str(pitches): count for pitches, count in sorted(counter.items())}
            for key, counter in grouped.items()
        },
        "training_rows": n,
        "probability_publishable": False,
        "can_execute": False,
    }
    payload["artifact_checksum"] = _sha(payload)
    return payload


def score_empirical_pmf(
    artifact: dict[str, Any],
    *,
    line_value: float,
    side: str,
) -> dict[str, Any]:
    """Return exact MORE/LESS/push mass from a fitted discrete PMF artifact."""
    if artifact.get("model_family") != MODEL_FAMILY:
        raise ValueError("MLB_1IP_ARTIFACT_MODEL_FAMILY_INVALID")
    if artifact.get("artifact_format") != ARTIFACT_FORMAT:
        raise ValueError("MLB_1IP_ARTIFACT_FORMAT_INVALID")

    weights = artifact.get("bf_weights") or {}
    counts = artifact.get("conditional_total_pitch_counts") or {}
    if set(weights) != {"3", "4", "5_PLUS"} or set(counts) != {"3", "4", "5_PLUS"}:
        raise ValueError("MLB_1IP_ARTIFACT_BUCKETS_INVALID")

    p_more = p_less = p_push = 0.0
    conditional_more: dict[str, float] = {}
    total_more = total_less = total_push = 0
    support_n = 0
    for bucket in ("3", "4", "5_PLUS"):
        bucket_counts = counts[bucket]
        total = sum(int(v) for v in bucket_counts.values())
        if total <= 0:
            raise ValueError("MLB_1IP_ARTIFACT_BUCKET_EMPTY")
        more = less = push = 0
        for pitch_text, count_raw in bucket_counts.items():
            pitches = int(pitch_text)
            count = int(count_raw)
            if pitches > line_value:
                more += count
            elif pitches < line_value:
                less += count
            else:
                push += count
        weight = float(weights[bucket])
        conditional_more[bucket] = more / total
        p_more += weight * more / total
        p_less += weight * less / total
        p_push += weight * push / total
        total_more += more
        total_less += less
        total_push += push
        support_n += total

    if support_n != int(artifact.get("training_rows") or 0):
        raise ValueError("MLB_1IP_ARTIFACT_SUPPORT_COUNT_MISMATCH")

    side_norm = str(side or "").strip().upper()
    if side_norm not in {"MORE", "LESS"}:
        raise ValueError("MLB_1IP_DIRECTION_INVALID")
    selected_count = total_more if side_norm == "MORE" else total_less
    selected = p_more if side_norm == "MORE" else p_less
    lower_bound, upper_bound = _wilson_interval(selected_count, support_n)
    return {
        "model_family": MODEL_FAMILY,
        "calibrator_version": CALIBRATOR_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "P_MORE": p_more,
        "P_LESS": p_less,
        "prob_push": p_push,
        "selected_probability": selected,
        "selected_support_n": support_n,
        "selected_support_count": selected_count,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "conditional_more_by_bf_bucket": conditional_more,
        "probability_publishable": False,
        "can_execute": False,
    }
