"""Training/certification pipeline for MLB 1IP fitted artifacts.

This module deliberately separates CANDIDATE creation from certification.
It will not emit a promoted/active artifact unless empirical validation gates
are satisfied. It never executes bets.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any

SPORT = "MLB"
STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
FEATURE_SCHEMA_VERSION = "MLB_1IP_FEATURES_V1"
FEATURE_TRANSFORM_VERSION = "MLB_1IP_EVENT_TREE_TRANSFORM_V1"
SPECIALIST_VERSION = "wow.mlb-first-inning-pitch-count-expert@1"
MODEL_FAMILY = "MLB_1IP_EVENT_TREE_EMPIRICAL_V1"
CAN_EXECUTE = False

MIN_TRAINING_ROWS = 1000
MIN_VALIDATION_ROWS = 250
MAX_BRIER = 0.25
MAX_ECE = 0.06


@dataclass(frozen=True)
class TrainingRow:
    bf: int
    pitches: int


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _brier(y: list[int], p: list[float]) -> float:
    return sum((a-b)**2 for a,b in zip(y,p)) / len(y)


def _ece(y: list[int], p: list[float], bins: int = 10) -> float:
    total = len(y)
    out = 0.0
    for i in range(bins):
        lo, hi = i/bins, (i+1)/bins
        idx = [j for j,v in enumerate(p) if (lo <= v < hi) or (i == bins-1 and v == 1.0)]
        if not idx:
            continue
        conf = sum(p[j] for j in idx)/len(idx)
        acc = sum(y[j] for j in idx)/len(idx)
        out += len(idx)/total * abs(conf-acc)
    return out


def fit_candidate(rows: list[TrainingRow], *, training_code_sha: str) -> dict[str, Any]:
    if len(rows) < MIN_TRAINING_ROWS:
        raise ValueError("MLB_1IP_TRAINING_ROWS_INSUFFICIENT")
    bfs = [r.bf for r in rows]
    pitches = [r.pitches for r in rows]
    n = len(rows)
    payload = {
        "bf_distribution": {
            "p_bf_3": sum(1 for x in bfs if x == 3)/n,
            "p_bf_4": sum(1 for x in bfs if x == 4)/n,
            "p_bf_gte5": sum(1 for x in bfs if x >= 5)/n,
        },
        "pitches_per_batter": {
            "mean": mean(pitches)/mean(bfs),
            "std": max(0.25, pstdev([r.pitches/max(r.bf,1) for r in rows])),
        },
        "training_rows": n,
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
    }
    dataset_hash = _hash([{"bf": r.bf, "pitches": r.pitches} for r in rows])
    checksum = _hash(payload)
    return {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": f"MLB_1IP_EVENT_TREE_EMPIRICAL_V1_{checksum[:12]}",
        "calibrator_version": "MLB_1IP_EMPIRICAL_CAL_V1",
        "sport": SPORT,
        "stat_type": STAT_TYPE,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "training_dataset_hash": dataset_hash,
        "training_code_sha": training_code_sha,
        "artifact_checksum": checksum,
        "artifact_format": "JSON_FITTED_CONSTANTS_V1",
        "artifact_payload": payload,
        "supported_line_min": 8.5,
        "supported_line_max": 35.5,
        "training_rows": n,
        "validation_metrics": {},
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def validate_candidate(candidate: dict[str, Any], validation_actual: list[int], validation_prob: list[float]) -> dict[str, Any]:
    if len(validation_actual) != len(validation_prob) or len(validation_actual) < MIN_VALIDATION_ROWS:
        raise ValueError("MLB_1IP_VALIDATION_ROWS_INSUFFICIENT")
    if any(v not in (0,1) for v in validation_actual):
        raise ValueError("MLB_1IP_VALIDATION_TARGET_INVALID")
    if any((not math.isfinite(v)) or v <= 0 or v >= 1 for v in validation_prob):
        raise ValueError("MLB_1IP_VALIDATION_PROBABILITY_INVALID")
    brier = _brier(validation_actual, validation_prob)
    ece = _ece(validation_actual, validation_prob)
    passed = brier <= MAX_BRIER and ece <= MAX_ECE
    result = dict(candidate)
    result["validation_metrics"] = {"validation_rows": len(validation_actual), "brier": brier, "ece": ece, "gates_passed": passed}
    result["certification_id"] = f"PROP-CERT-MLB-1IP-{result['artifact_checksum'][:16]}" if passed else None
    result["lifecycle_state"] = "PROSPECTIVE_CERTIFIED" if passed else "CANDIDATE"
    result["promoted"] = bool(passed)
    result["active"] = bool(passed)
    # Existing database contract intentionally keeps this false. Publication
    # is determined by governed scoring/calibration lanes, not by this row.
    result["probability_publishable"] = False
    result["can_execute"] = False
    return result
