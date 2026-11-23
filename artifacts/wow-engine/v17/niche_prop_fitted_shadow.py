"""Exact-line niche-prop fitted SHADOW, not a published or certified specialist.

Fitting only consumes source-claimed pregame snapshots and independently
referenced settlement rows. This module cannot authenticate external sources,
verify offered lines, certify models, or publish predictions. It never uses
market-implied odds, screenshot hit rates or automatic wagering.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from v17.niche_prop_research import STUDY_CATEGORIES, classify_niche_prop

CAN_EXECUTE = False
MIN_TRAIN, MIN_CAL, MIN_TEST = 80, 30, 30
SHADOW_VERSION = "wow.v17.niche-fitted-shadow.v1"
FEATURE_KEYS: dict[str, tuple[str, ...]] = {
    "NFL_1Q_TWO_KICKER_FG_OVER_0_5": (
        "expected_q1_drives", "home_q1_fg_attempt_rate_prior",
        "away_q1_fg_attempt_rate_prior", "q1_red_zone_td_rate_prior",
        "weather_wind_mph", "fourth_down_go_rate_prior",
    ),
    "NBA_LOW_VOLUME_UNDER": (
        "projected_minutes", "stat_opportunities_per_min_prior",
        "opponent_adjustment_prior", "game_pace_prior", "usage_shift_prior",
    ),
    "NFL_1Q_QB_PASS_ATTEMPTS_OVER": (
        "projected_q1_drives", "pass_rate_prior", "plays_per_drive_prior",
        "opponent_pressure_prior",
    ),
    "NFL_1Q_QB_FANTASY_SCORE_OVER": (
        "projected_q1_plays", "pass_epa_prior", "qb_rush_share_prior",
        "weather_wind_mph", "opponent_pressure_prior",
    ),
}
_MAX_ABS_FEATURE = 100000.0


class NicheShadowHold(ValueError):
    """Typed research rejection: never substitute a fallback probability."""


def _fail(code: str) -> None:
    raise NicheShadowHold(code)


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        _fail("TIMESTAMP_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("TIMESTAMP_MALFORMED")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value: Any, *, lower: float = -_MAX_ABS_FEATURE, upper: float = _MAX_ABS_FEATURE) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("NONNUMERIC_FEATURE_OR_LINE")
    number = float(value)
    if not math.isfinite(number) or number < lower or number > upper:
        _fail("NONFINITE_OR_OUT_OF_RANGE_FEATURE")
    return number


def _validate(row: Mapping[str, Any], *, settled: bool) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        _fail("ROW_NOT_MAPPING")
    category_data = classify_niche_prop(row)
    category = category_data["category"]
    if category not in STUDY_CATEGORIES:
        _fail("NICHE_ROUTE_UNSUPPORTED")
    if category_data["identity_gaps"]:
        _fail("NICHE_EXACT_IDENTITY_INCOMPLETE")
    stat = category_data["stat_type"]
    if not all(isinstance(row.get(field), str) and row[field].strip()
               for field in ("row_id", "official_event_id", "source_receipt_id")):
        _fail("IMMUTABLE_IDENTITY_MISSING")
    snapshot_hash = row.get("feature_snapshot_hash")
    if (not isinstance(snapshot_hash, str) or len(snapshot_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in snapshot_hash.lower())):
        _fail("SOURCE_SNAPSHOT_DIGEST_MISSING")
    start = _utc(row.get("event_start_utc"))
    snapshot_time = _utc(row.get("features_as_of_utc"))
    if snapshot_time >= start:
        _fail("PREGAME_FEATURE_CHRONOLOGY_INVALID")
    keys = FEATURE_KEYS[category]
    feature_values = row.get("features")
    if not isinstance(feature_values, Mapping) or set(feature_values) != set(keys):
        _fail("FEATURE_SCHEMA_MISMATCH_OR_LEAKAGE")
    features = tuple(_finite(feature_values[key]) for key in keys)
    line = _finite(category_data["exact_line"], lower=0, upper=10000)
    selected = {
        "row_id": row["row_id"], "official_event_id": row["official_event_id"],
        # Bind the immutable forecast to the exact player or two kicker IDs.
        # A source hash and event ID alone are not an exact player-market ID.
        "official_player_id": row.get("official_player_id"),
        "official_player_position": row.get("official_player_position"),
        "combo_participant_ids": row.get("combo_participant_ids"),
        "combo_team_ids": row.get("combo_team_ids"),
        "home_team_id": row.get("home_team_id"),
        "away_team_id": row.get("away_team_id"),
        "category": category, "sport": row.get("sport"),
        "stat_type": stat, "side": category_data["side"],
        "period": category_data["period"], "exact_line": line,
        "source_receipt_id": row["source_receipt_id"],
        "feature_snapshot_hash": snapshot_hash.lower(),
        "event_start_utc": start.isoformat(),
        "features_as_of_utc": snapshot_time.isoformat(),
        "x": list(features) + [line],
    }
    if settled:
        if not isinstance(row.get("settlement_source_ref"), str) or not row["settlement_source_ref"].strip():
            _fail("INDEPENDENT_SETTLEMENT_REFERENCE_MISSING")
        settlement_time = _utc(row.get("settlement_at_utc"))
        if settlement_time <= start:
            _fail("SETTLEMENT_NOT_AFTER_EVENT_START")
        actual = _finite(row.get("actual_stat"), lower=0)
        side = category_data["side"]
        if actual == line:
            _fail("PUSH_NOT_BINARY_SETTLEMENT")
        hit = actual > line if side == "MORE" else actual < line
        result = str(row.get("settled_result", "")).upper()
        if result not in {"WIN", "LOSS"} or hit != (result == "WIN"):
            _fail("OFFICIAL_SETTLEMENT_VALUE_CONFLICT")
        selected.update({"y": int(hit),
                         "settlement_source_ref": row["settlement_source_ref"],
                         "settlement_at_utc": settlement_time.isoformat()})
    else:
        if any(key in row for key in (
            "actual_stat", "settled_result", "settlement_at_utc", "settlement_source_ref"
        )):
            _fail("PREGAME_INPUT_CONTAINS_RESULT")
    return selected


def _logistic(value: float) -> float:
    clipped = max(-35.0, min(35.0, value))
    return 1 / (1 + math.exp(-clipped))


def _predict_linear(weights: Sequence[float], x: Sequence[float]) -> float:
    return weights[0] + sum(a * b for a, b in zip(weights[1:], x))


def _normalizer(rows: Sequence[dict]) -> tuple[list[float], list[float]]:
    size = len(rows[0]["x"])
    mu = [sum(item["x"][j] for item in rows) / len(rows) for j in range(size)]
    scale = []
    for j in range(size):
        variance = sum((r["x"][j] - mu[j]) ** 2 for r in rows) / len(rows)
        scale.append(max(math.sqrt(variance), 1.0e-8))
    return mu, scale


def _scale(x: Sequence[float], mu: Sequence[float], sd: Sequence[float]) -> list[float]:
    return [(value - mean) / scale for value, mean, scale in zip(x, mu, sd)]


def _fit_logit(xs: Sequence[Sequence[float]], ys: Sequence[int]) -> list[float]:
    p = len(xs[0])
    w = [0.0] * (p + 1)
    w[0] = math.log((sum(ys) + 1) / (len(ys) - sum(ys) + 1))
    for _ in range(400):
        grads = [0.0] * (p + 1)
        for x, y in zip(xs, ys):
            diff = _logistic(_predict_linear(w, x)) - y
            grads[0] += diff
            for j, feature in enumerate(x):
                grads[j + 1] += diff * feature
        w[0] -= 0.12 * grads[0] / len(xs)
        for j in range(1, p + 1):
            w[j] -= 0.12 * (grads[j] / len(xs) + 0.04 * w[j])
    return w


def _fit_platt(logits: Sequence[float], ys: Sequence[int]) -> tuple[float, float]:
    # Calibrator fit uses later period only; no held-out test labels.
    a, b = 1.0, 0.0
    for _ in range(400):
        ga = gb = 0.0
        for z, y in zip(logits, ys):
            err = _logistic(a * z + b) - y
            ga += err * z
            gb += err
        a -= 0.04 * (ga / len(ys) + 0.05 * (a - 1.0))
        b -= 0.04 * (gb / len(ys) + 0.05 * b)
    return a, b


def _holdout_metrics(probabilities: Sequence[float], outcomes: Sequence[int]) -> dict[str, Any]:
    n = len(outcomes)
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / n
    log_loss = -sum(y * math.log(max(p, 1e-12)) +
                    (1 - y) * math.log(max(1 - p, 1e-12))
                    for p, y in zip(probabilities, outcomes)) / n
    buckets: dict[int, list[tuple[float, int]]] = {}
    for p, y in zip(probabilities, outcomes):
        buckets.setdefault(min(9, int(p * 10)), []).append((p, y))
    ece = sum(len(v) / n *
              abs(sum(p for p, _ in v) / len(v) - sum(y for _, y in v) / len(v))
              for v in buckets.values())
    return {"sample_count": n, "brier": brier, "log_loss": log_loss,
            "ece": ece, "empirical_hit_rate": sum(outcomes) / n}


def fit_niche_shadow(train: Sequence[Mapping[str, Any]],
                     calibration: Sequence[Mapping[str, Any]],
                     test: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fit only on train; Platt-calibrate on later cohort; evaluate later untouched test."""
    if len(train) < MIN_TRAIN or len(calibration) < MIN_CAL or len(test) < MIN_TEST:
        _fail("MINIMUM_COHORT_NOT_MET")
    cohorts = [[_validate(row, settled=True) for row in part]
               for part in (train, calibration, test)]
    full = [row for part in cohorts for row in part]
    signatures = {
        (r["category"], r["sport"], r["stat_type"], r["side"], r["period"]) for r in full
    }
    if len(signatures) != 1:
        _fail("MIXED_EXACT_STAT_OR_PERIOD")
    row_ids = [r["row_id"] for r in full]
    if len(set(row_ids)) != len(row_ids):
        _fail("DUPLICATE_RECORD_ID")
    event_groups = [set(r["official_event_id"] for r in part) for part in cohorts]
    if any(event_groups[i] & event_groups[j] for i, j in ((0, 1), (0, 2), (1, 2))):
        _fail("EVENT_ID_CROSS_SPLIT_LEAKAGE")
    for part in cohorts:
        if len({r["y"] for r in part}) != 2:
            _fail("CLASS_DIVERSITY_INSUFFICIENT")
    a, b, c = cohorts
    if not (max(x["event_start_utc"] for x in a) < min(x["event_start_utc"] for x in b)
            and max(x["event_start_utc"] for x in b) < min(x["event_start_utc"] for x in c)):
        _fail("CHRONOLOGICAL_COHORT_OVERLAP")
    mu, sd = _normalizer(a)
    w = _fit_logit([_scale(r["x"], mu, sd) for r in a], [r["y"] for r in a])
    cal_scores = [_predict_linear(w, _scale(r["x"], mu, sd)) for r in b]
    cal_a, cal_b = _fit_platt(cal_scores, [r["y"] for r in b])
    test_scores = [_predict_linear(w, _scale(r["x"], mu, sd)) for r in c]
    test_prob = [_logistic(cal_a * x + cal_b) for x in test_scores]
    feat = list(FEATURE_KEYS[a[0]["category"]]) + ["exact_line"]
    artifact = {
        "version": SHADOW_VERSION,
        "category": a[0]["category"],
        "sport": a[0]["sport"], "stat_type": a[0]["stat_type"],
        "period": a[0]["period"], "side": a[0]["side"],
        "feature_keys": feat,
        "means": mu, "stddevs": sd, "weights": w,
        "platt_a": cal_a, "platt_b": cal_b,
        "train_count": len(a), "calibration_count": len(b),
        "fit_data_digest": _digest(a),
        "calibration_data_digest": _digest(b),
        "last_calibration_event_start_utc": max(r["event_start_utc"] for r in b),
        "candidate_only": True,
        "publication_allowed": False,
        "can_execute": False,
    }
    artifact["model_digest"] = _digest(artifact)
    return {
        "status": "SHADOW_UNCERTIFIED",
        "artifact": artifact,
        "holdout_metrics": _holdout_metrics(test_prob, [r["y"] for r in c]),
        "heldout_data_digest": _digest(c),
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "certified_joint_probability": None,
        "can_execute": False,
    }


def predict_niche_shadow(artifact: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explicitly uncertified research candidate, never executable."""
    if not isinstance(artifact, Mapping):
        _fail("ARTIFACT_MISSING")
    digest = artifact.get("model_digest")
    without_hash = {k: v for k, v in artifact.items() if k != "model_digest"}
    if not isinstance(digest, str) or _digest(without_hash) != digest:
        _fail("ARTIFACT_DIGEST_MISMATCH")
    if artifact.get("version") != SHADOW_VERSION or artifact.get("publication_allowed") is not False:
        _fail("MODEL_NOT_SHADOW")
    record = _validate(row, settled=False)
    for key in ("category", "sport", "stat_type", "period", "side"):
        if record[key] != artifact.get(key):
            _fail("PREDICTION_ROUTE_IDENTITY_CONFLICT")
    if record["event_start_utc"] <= artifact.get("last_calibration_event_start_utc", ""):
        _fail("PREDICTION_NOT_FORWARD_OF_CALIBRATION")
    feat = list(FEATURE_KEYS[record["category"]]) + ["exact_line"]
    if artifact.get("feature_keys") != feat:
        _fail("FEATURE_ARTIFACT_SCHEMA_MISMATCH")
    score = _predict_linear(artifact["weights"],
                            _scale(record["x"], artifact["means"], artifact["stddevs"]))
    p = _logistic(artifact["platt_a"] * score + artifact["platt_b"])
    return {
        "status": "SHADOW_UNCERTIFIED",
        "candidate_probability": p,
        "certified_probability": None,
        "calibrated_lower_bound": None,
        "pregame_receipt_hash": _digest({"artifact": digest, "pregame_record": record}),
        "model_digest": digest,
        "source_authentication": "UNVERIFIED_EXTERNAL_CLAIM",
        "model_certified": False, "publication_allowed": False, "can_execute": False,
    }
