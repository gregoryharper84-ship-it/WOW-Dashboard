"""Bounded, non-serving V17 chronological challenger experiment lab.

A stdlib logistic reference is provided for deterministic harness testing.
The optional CatBoost backend is Class-C shadow research only. Neither engine
supplies a governed sporting probability, certified bound, or publication.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from v17.accuracy_improvement_controller import _metrics, _ci

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
PROBABILITY_PUBLISHABLE = False
MIN_TRAIN = 30
MIN_CALIBRATION = 20
MIN_HOLDOUT_EVENTS = 30
DENY_FEATURE_PARTS = (
    "odds", "implied", "market_price", "payout", "closing",
    "postgame", "final_score", "realized", "outcome", "hit_rate",
)


class ChallengerLabError(ValueError):
    pass


def _datetime(v: Any, code: str) -> datetime:
    if not isinstance(v, str):
        raise ChallengerLabError(code)
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if d.utcoffset() is not None:
            return d.astimezone(timezone.utc)
    except ValueError:
        pass
    raise ChallengerLabError(code)


def _number(x: Any, code: str) -> float:
    if type(x) not in (int, float):
        raise ChallengerLabError(code)
    n = float(x)
    if not math.isfinite(n):
        raise ChallengerLabError(code)
    return n


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))


def _logit(p: float) -> float:
    p = max(1e-6, min(1 - 1e-6, p))
    return math.log(p / (1 - p))


def _fit_reference(train: Sequence[list[float]], y: Sequence[int]) -> Any:
    d = len(train[0])
    means = [sum(row[j] for row in train) / len(train) for j in range(d)]
    stds = [max(1e-6, (sum((row[j]-means[j])**2 for row in train)/len(train))**0.5)
            for j in range(d)]
    standardized = [[(v - means[j]) / stds[j] for j, v in enumerate(row)]
                    for row in train]
    w = [0.0] * d
    b = _logit((sum(y) + 0.5) / (len(y) + 1))
    for _ in range(320):
        gw = [0.0] * d
        gb = 0.0
        for row, target in zip(standardized, y):
            err = _sigmoid(b + sum(v*a for v, a in zip(row, w))) - target
            gb += err
            for j in range(d):
                gw[j] += err*row[j]
        for j in range(d):
            w[j] -= 0.15*(gw[j]/len(y) + 0.015*w[j])
        b -= 0.15*gb/len(y)

    def predict(rows: Sequence[list[float]]) -> list[float]:
        return [_sigmoid(b + sum((v-means[j])/stds[j]*w[j] for j, v in enumerate(row)))
                for row in rows]
    return predict


def _fit_platt(probs: Sequence[float], outcomes: Sequence[int]) -> Any:
    # Learned using CALIBRATION only; holdout is not inspected for fitting.
    a, b = 1.0, 0.0
    xs = [_logit(p) for p in probs]
    for _ in range(400):
        err = [_sigmoid(a*x+b)-y for x, y in zip(xs, outcomes)]
        a -= 0.015*(sum(e*x for e, x in zip(err, xs))/len(xs) + 0.005*(a-1))
        b -= 0.015*sum(err)/len(xs)
    return lambda p: _sigmoid(a*_logit(p)+b)


def train_shadow_challenger(spec: Mapping[str, Any], *,
                            engine: str = "REFERENCE_LOGISTIC") -> dict[str, Any]:
    """Fit and evaluate from a fully supplied, frozen chronological evidence set.

    Caller-provided source IDs/hashes are NOT independently authenticated.
    We intentionally do not output deployable model objects or row probabilities.
    """
    if not isinstance(spec, Mapping):
        raise ChallengerLabError("EXPERIMENT_SPEC_REQUIRED")
    if not isinstance(spec.get("experiment_id"), str) or not spec["experiment_id"].strip():
        raise ChallengerLabError("EXPERIMENT_ID_REQUIRED")
    if not isinstance(spec.get("sport"), str) or not isinstance(spec.get("route"), str):
        raise ChallengerLabError("SPORT_ROUTE_REQUIRED")
    names = spec.get("feature_names")
    if (not isinstance(names, list) or not names or len(names) > 256
            or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", x)
                   for x in names)
            or len(set(names)) != len(names)):
        raise ChallengerLabError("FEATURE_SCHEMA_INVALID")
    if any(any(part in x.lower() for part in DENY_FEATURE_PARTS) for x in names):
        raise ChallengerLabError("MARKET_OR_POSTGAME_FEATURE_LEAKAGE")
    rows = spec.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ChallengerLabError("DATASET_REQUIRED")
    prepared = []
    seen = set()
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        if not isinstance(item, Mapping):
            raise ChallengerLabError("INVALID_DATASET_ROW")
        split = item.get("split")
        if split not in ("TRAIN", "CALIBRATION", "HOLDOUT"):
            raise ChallengerLabError("SPLIT_REQUIRED")
        if item.get("sport") != spec["sport"] or item.get("route") != spec["route"]:
            raise ChallengerLabError("SPORT_ROUTE_MIXING_PROHIBITED")
        eid, market = item.get("event_id"), item.get("market_id")
        if not isinstance(eid, str) or not eid or not isinstance(market, str) or not market:
            raise ChallengerLabError("EVENT_MARKET_ID_REQUIRED")
        identity = (eid, market)
        if identity in seen:
            raise ChallengerLabError("DUPLICATE_EVENT_MARKET")
        seen.add(identity)
        start = _datetime(item.get("event_start_utc"), "EVENT_TIME_INVALID")
        feature_asof = _datetime(item.get("feature_asof_utc"), "FEATURE_TIME_INVALID")
        locked = _datetime(item.get("champion_locked_at_utc"), "CHAMPION_LOCK_INVALID")
        settled = _datetime(item.get("settled_at_utc"), "OUTCOME_TIME_INVALID")
        if not feature_asof <= locked < start <= settled:
            raise ChallengerLabError("POINT_IN_TIME_LEAKAGE")
        if item.get("outcome") not in (0, 1) or type(item.get("outcome")) is not int:
            raise ChallengerLabError("BINARY_OUTCOME_REQUIRED")
        p = _number(item.get("champion_probability"), "CHAMPION_PROBABILITY_INVALID")
        if not 0 <= p <= 1:
            raise ChallengerLabError("CHAMPION_PROBABILITY_INVALID")
        if item.get("champion_origin") != "FITTED_SPECIALIST":
            raise ChallengerLabError("MARKET_OR_NARRATIVE_NOT_FITTED_CHAMPION")
        lb = _number(item.get("champion_lower_bound"), "CHAMPION_LOWER_BOUND_INVALID")
        if not 0 <= lb <= p:
            raise ChallengerLabError("CHAMPION_LOWER_BOUND_INVALID")
        for field in ("source_id", "settlement_source_id", "champion_prediction_id",
                      "champion_artifact_id", "champion_calibrator_id", "champion_specialist_id"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ChallengerLabError("SOURCE_LINEAGE_INCOMPLETE")
        fingerprint = item.get("source_sha256")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise ChallengerLabError("SOURCE_DIGEST_REQUIRED")
        features = item.get("features")
        if not isinstance(features, Mapping) or set(features) != set(names):
            raise ChallengerLabError("FEATURE_SCHEMA_MISMATCH")
        row = dict(item)
        row["_x"] = [_number(features[name], "INVALID_NUMERIC_FEATURE") for name in names]
        row["_start"] = start
        prepared.append(row)
        by_split[split].append(row)
    for field in ("champion_artifact_id", "champion_calibrator_id", "champion_specialist_id"):
        if len({r[field] for r in prepared}) != 1:
            raise ChallengerLabError("MIXED_CHAMPION_MODEL_LINEAGE")
    if len(by_split["TRAIN"]) < MIN_TRAIN or len(by_split["CALIBRATION"]) < MIN_CALIBRATION:
        raise ChallengerLabError("INSUFFICIENT_TRAIN_OR_CALIBRATION")
    if len({r["event_id"] for r in by_split["HOLDOUT"]}) < MIN_HOLDOUT_EVENTS:
        raise ChallengerLabError("INSUFFICIENT_UNSEEN_HOLDOUT_EVENTS")
    assigned: dict[str, str] = {}
    for r in prepared:
        old = assigned.setdefault(r["event_id"], r["split"])
        if old != r["split"]:
            raise ChallengerLabError("CROSS_SPLIT_EVENT_LEAKAGE")
    if not (max(r["_start"] for r in by_split["TRAIN"]) <
            min(r["_start"] for r in by_split["CALIBRATION"]) and
            max(r["_start"] for r in by_split["CALIBRATION"]) <
            min(r["_start"] for r in by_split["HOLDOUT"])):
        raise ChallengerLabError("CHRONOLOGICAL_SPLIT_LEAKAGE")
    train = by_split["TRAIN"]
    cal = by_split["CALIBRATION"]
    hold = by_split["HOLDOUT"]
    if engine == "REFERENCE_LOGISTIC":
        predict = _fit_reference([r["_x"] for r in train], [r["outcome"] for r in train])
    elif engine == "CATBOOST":
        try:
            from catboost import CatBoostClassifier
        except ImportError as exc:
            raise ChallengerLabError("CATBOOST_DEPENDENCY_UNAVAILABLE") from exc
        model = CatBoostClassifier(
            iterations=180, depth=4, learning_rate=0.04, l2_leaf_reg=6,
            loss_function="Logloss", random_seed=170026,
            verbose=False, allow_writing_files=False, thread_count=1,
        )
        model.fit([r["_x"] for r in train], [r["outcome"] for r in train])
        predict = lambda xs: [float(p[1]) for p in model.predict_proba(xs)]
    else:
        raise ChallengerLabError("UNSUPPORTED_SHADOW_TRAINER")
    calibrate = _fit_platt(predict([r["_x"] for r in cal]),
                           [r["outcome"] for r in cal])
    graded = [{
        "event_id": r["event_id"], "outcome": r["outcome"],
        "champion_probability": r["champion_probability"],
        "challenger_probability": calibrate(p),
    } for r, p in zip(hold, predict([r["_x"] for r in hold]))]
    baseline = _metrics(graded, "champion")
    challenger = _metrics(graded, "challenger")
    frozen = [{
        "event_id": r["event_id"], "market_id": r["market_id"],
        "split": r["split"], "source_sha256": r["source_sha256"],
        "champion_prediction_id": r["champion_prediction_id"],
        "champion_artifact_id": r["champion_artifact_id"],
        "champion_calibrator_id": r["champion_calibrator_id"],
    } for r in prepared]
    dataset_sha = hashlib.sha256(json.dumps(frozen, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return {
        "status": "SHADOW_TRAINING_COMPLETE_SOURCE_UNVERIFIED",
        "experiment_id": spec["experiment_id"],
        "sport": spec["sport"], "route": spec["route"],
        "research_engine": engine, "dataset_sha256": dataset_sha,
        "counts": {k: len(by_split[k]) for k in ("TRAIN", "CALIBRATION", "HOLDOUT")},
        "holdout_event_count": len({r["event_id"] for r in hold}),
        "champion_metrics": baseline, "challenger_metrics": challenger,
        "paired_brier_delta_champion_minus_challenger":
            round(baseline["brier"]-challenger["brier"], 8),
        "paired_brier_delta_event_cluster_bootstrap_ci": _ci(graded),
        "source_authentication_proven": False,
        "certification_disposition": "INDEPENDENT_CLASS_C_REVIEW_REQUIRED",
        "automatic_promotion": False, "probability_publishable": False,
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }
