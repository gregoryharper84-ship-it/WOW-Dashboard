"""Training/validation/promotion packet builder for MLB 1IP artifacts.

Validation and promotion are intentionally separate. Empirical validation may
advance a candidate to SHADOW, but cannot make it serving-eligible. Promotion
requires a lineage-bound validation packet plus an independent review context.
This module performs no database writes and never authorizes execution.
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
    return sum((a - b) ** 2 for a, b in zip(y, p)) / len(y)


def _ece(y: list[int], p: list[float], bins: int = 10) -> float:
    total = len(y)
    out = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [j for j, v in enumerate(p) if (lo <= v < hi) or (i == bins - 1 and v == 1.0)]
        if not idx:
            continue
        conf = sum(p[j] for j in idx) / len(idx)
        acc = sum(y[j] for j in idx) / len(idx)
        out += len(idx) / total * abs(conf - acc)
    return out


def fit_candidate(rows: list[TrainingRow], *, training_code_sha: str) -> dict[str, Any]:
    if len(rows) < MIN_TRAINING_ROWS:
        raise ValueError("MLB_1IP_TRAINING_ROWS_INSUFFICIENT")
    if len(str(training_code_sha)) < 40:
        raise ValueError("MLB_1IP_TRAINING_CODE_SHA_INVALID")
    bfs = [r.bf for r in rows]
    pitches = [r.pitches for r in rows]
    n = len(rows)
    payload = {
        "bf_distribution": {
            "p_bf_3": sum(1 for x in bfs if x == 3) / n,
            "p_bf_4": sum(1 for x in bfs if x == 4) / n,
            "p_bf_gte5": sum(1 for x in bfs if x >= 5) / n,
        },
        "pitches_per_batter": {
            "mean": mean(pitches) / mean(bfs),
            "std": max(0.25, pstdev([r.pitches / max(r.bf, 1) for r in rows])),
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
        "validation_lineage": None,
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def validate_candidate(
    candidate: dict[str, Any],
    validation_actual: list[int],
    validation_prob: list[float],
    *,
    scoring_code_sha: str,
    split_hash: str,
    source_snapshot_hashes: list[str],
) -> dict[str, Any]:
    """Record validation only; never certify or activate the artifact.

    The supplied probabilities remain evidence, not authority. Their exact
    vector is hashed and bound to the candidate, temporal split, scorer code,
    and immutable source snapshots so an independent reviewer can reproduce
    the run before promotion.
    """
    if len(validation_actual) != len(validation_prob) or len(validation_actual) < MIN_VALIDATION_ROWS:
        raise ValueError("MLB_1IP_VALIDATION_ROWS_INSUFFICIENT")
    if any(v not in (0, 1) for v in validation_actual):
        raise ValueError("MLB_1IP_VALIDATION_TARGET_INVALID")
    if any((not math.isfinite(v)) or v <= 0 or v >= 1 for v in validation_prob):
        raise ValueError("MLB_1IP_VALIDATION_PROBABILITY_INVALID")
    if len(str(scoring_code_sha)) < 40 or len(str(split_hash)) != 64:
        raise ValueError("MLB_1IP_VALIDATION_LINEAGE_INVALID")
    if not source_snapshot_hashes or any(len(str(v)) != 64 for v in source_snapshot_hashes):
        raise ValueError("MLB_1IP_VALIDATION_SOURCE_LINEAGE_INVALID")

    brier = _brier(validation_actual, validation_prob)
    ece = _ece(validation_actual, validation_prob)
    passed = brier <= MAX_BRIER and ece <= MAX_ECE
    lineage = {
        "artifact_checksum": candidate["artifact_checksum"],
        "model_artifact_version": candidate["model_artifact_version"],
        "training_dataset_hash": candidate["training_dataset_hash"],
        "training_code_sha": candidate["training_code_sha"],
        "scoring_code_sha": scoring_code_sha,
        "split_hash": split_hash,
        "source_snapshot_hashes": sorted(source_snapshot_hashes),
        "validation_targets_hash": _hash(validation_actual),
        "validation_probabilities_hash": _hash(validation_prob),
    }
    lineage["validation_lineage_hash"] = _hash(lineage)

    result = dict(candidate)
    result["validation_metrics"] = {
        "validation_rows": len(validation_actual),
        "brier": brier,
        "ece": ece,
        "gates_passed": passed,
    }
    result["validation_lineage"] = lineage
    result["certification_id"] = None
    result["lifecycle_state"] = "SHADOW" if passed else "CANDIDATE"
    result["promoted"] = False
    result["active"] = False
    result["probability_publishable"] = False
    result["can_execute"] = False
    return result


def promote_validated_candidate(
    validated: dict[str, Any],
    *,
    implementer_context: str,
    reviewer_context: str,
    review_verdict: str,
    review_evidence_hash: str,
) -> dict[str, Any]:
    """Produce a promotion-ready payload after independent review.

    This does not write Supabase. A separately governed migration/data-write
    step must persist the returned row.
    """
    metrics = validated.get("validation_metrics") or {}
    lineage = validated.get("validation_lineage") or {}
    if validated.get("lifecycle_state") != "SHADOW" or metrics.get("gates_passed") is not True:
        raise ValueError("MLB_1IP_EMPIRICAL_VALIDATION_NOT_PASSED")
    if not lineage.get("validation_lineage_hash"):
        raise ValueError("MLB_1IP_VALIDATION_LINEAGE_MISSING")
    if not implementer_context or not reviewer_context or implementer_context == reviewer_context:
        raise ValueError("MLB_1IP_INDEPENDENT_REVIEW_REQUIRED")
    if str(review_verdict).upper() != "APPROVE_FOR_PROMOTION":
        raise ValueError("MLB_1IP_REVIEW_NOT_APPROVED")
    if len(str(review_evidence_hash)) != 64:
        raise ValueError("MLB_1IP_REVIEW_EVIDENCE_INVALID")

    result = dict(validated)
    certification_material = {
        "artifact_checksum": validated["artifact_checksum"],
        "validation_lineage_hash": lineage["validation_lineage_hash"],
        "reviewer_context": reviewer_context,
        "review_evidence_hash": review_evidence_hash,
    }
    result["certification_id"] = f"PROP-CERT-MLB-1IP-{_hash(certification_material)[:16]}"
    result["review_evidence"] = {
        "implementer_context": implementer_context,
        "reviewer_context": reviewer_context,
        "verdict": "APPROVE_FOR_PROMOTION",
        "review_evidence_hash": review_evidence_hash,
    }
    result["lifecycle_state"] = "PROSPECTIVE_CERTIFIED"
    result["promoted"] = True
    result["active"] = True
    result["probability_publishable"] = False
    result["can_execute"] = False
    return result
