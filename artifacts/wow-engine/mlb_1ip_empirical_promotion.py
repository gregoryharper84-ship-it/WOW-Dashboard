"""Review-gated promotion bundle for the MLB 1IP empirical PMF candidate.

This module is deliberately a packet builder, not a deployment mechanism. It
binds the selected empirical artifact to temporal validation lineage and an
independent-review attestation. It performs no Supabase writes, does not alter
Render, and never makes probability publication or bet execution permissible.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from mlb_1ip_empirical_pmf import (
    ARTIFACT_FORMAT,
    CALIBRATOR_VERSION,
    FEATURE_TRANSFORM_VERSION,
    MODEL_FAMILY,
)

SPORT = "MLB"
STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
FEATURE_SCHEMA_VERSION = "MLB_1IP_FEATURES_V1"
SPECIALIST_VERSION = "wow.mlb-first-inning-pitch-count-expert@1"
SUPPORTED_LINES = (11.5, 13.5, 15.5, 17.5, 19.5, 21.5)
MAX_BRIER = 0.25
MAX_ECE = 0.06
CAN_EXECUTE = False


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _require_sha(value: Any, code: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ValueError(code)
    return text.lower()


def _verify_artifact(artifact: dict[str, Any]) -> str:
    if artifact.get("model_family") != MODEL_FAMILY:
        raise ValueError("MLB_1IP_PROMOTION_MODEL_FAMILY_MISMATCH")
    if artifact.get("artifact_format") != ARTIFACT_FORMAT:
        raise ValueError("MLB_1IP_PROMOTION_ARTIFACT_FORMAT_MISMATCH")
    if artifact.get("probability_publishable") is not False or artifact.get("can_execute") is not False:
        raise ValueError("MLB_1IP_PROMOTION_SAFETY_INVARIANT_VIOLATION")
    claimed = _require_sha(artifact.get("artifact_checksum"), "MLB_1IP_PROMOTION_ARTIFACT_CHECKSUM_INVALID")
    material = dict(artifact)
    material.pop("artifact_checksum", None)
    actual = _sha(material)
    if actual != claimed:
        raise ValueError("MLB_1IP_PROMOTION_ARTIFACT_CHECKSUM_MISMATCH")
    if int(artifact.get("training_rows") or 0) < 1000:
        raise ValueError("MLB_1IP_PROMOTION_TRAINING_ROWS_INSUFFICIENT")
    return claimed


def build_empirical_shadow_candidate(
    *,
    artifact: dict[str, Any],
    training_dataset_hash: str,
    training_code_sha: str,
    scoring_code_sha: str,
    split_hash: str,
    source_snapshot_hashes: list[str],
    validation_metrics: dict[str, Any],
    validated_lines: list[float] | tuple[float, ...],
) -> dict[str, Any]:
    """Bind one fitted empirical artifact to its immutable validation evidence."""
    checksum = _verify_artifact(artifact)
    dataset_hash = _require_sha(training_dataset_hash, "MLB_1IP_PROMOTION_TRAINING_DATASET_HASH_INVALID")
    split = _require_sha(split_hash, "MLB_1IP_PROMOTION_SPLIT_HASH_INVALID")
    if len(str(training_code_sha or "")) < 40 or len(str(scoring_code_sha or "")) < 40:
        raise ValueError("MLB_1IP_PROMOTION_CODE_SHA_INVALID")
    if not source_snapshot_hashes:
        raise ValueError("MLB_1IP_PROMOTION_SOURCE_LINEAGE_MISSING")
    snapshots = sorted(_require_sha(v, "MLB_1IP_PROMOTION_SOURCE_HASH_INVALID") for v in source_snapshot_hashes)

    rows = int(validation_metrics.get("validation_rows") or 0)
    brier = float(validation_metrics.get("brier", math.inf))
    ece = float(validation_metrics.get("ece", math.inf))
    gates = validation_metrics.get("gates_passed") is True
    if rows < 250 or not math.isfinite(brier) or not math.isfinite(ece) or not gates:
        raise ValueError("MLB_1IP_PROMOTION_VALIDATION_NOT_PASSED")
    if brier > MAX_BRIER or ece > MAX_ECE:
        raise ValueError("MLB_1IP_PROMOTION_VALIDATION_METRICS_OUT_OF_BOUNDS")

    lines = tuple(float(v) for v in validated_lines)
    if lines != SUPPORTED_LINES:
        raise ValueError("MLB_1IP_PROMOTION_VALIDATED_LINE_SUPPORT_MISMATCH")

    lineage = {
        "artifact_checksum": checksum,
        "training_dataset_hash": dataset_hash,
        "training_code_sha": str(training_code_sha),
        "scoring_code_sha": str(scoring_code_sha),
        "split_hash": split,
        "source_snapshot_hashes": snapshots,
        "validated_lines": list(SUPPORTED_LINES),
        "validation_metrics": {
            "validation_rows": rows,
            "brier": brier,
            "ece": ece,
            "gates_passed": True,
        },
    }
    lineage["validation_lineage_hash"] = _sha(lineage)

    return {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": f"MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1_{checksum[:12]}",
        "calibrator_version": CALIBRATOR_VERSION,
        "sport": SPORT,
        "stat_type": STAT_TYPE,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "training_dataset_hash": dataset_hash,
        "training_code_sha": str(training_code_sha),
        "artifact_checksum": checksum,
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_payload": artifact,
        "supported_line_min": SUPPORTED_LINES[0],
        "supported_line_max": SUPPORTED_LINES[-1],
        "supported_lines": list(SUPPORTED_LINES),
        "training_rows": int(artifact["training_rows"]),
        "validation_metrics": lineage["validation_metrics"],
        "validation_lineage": lineage,
        "certification_id": None,
        "review_evidence": None,
        "lifecycle_state": "SHADOW",
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def build_empirical_promotion_payload(
    shadow: dict[str, Any],
    *,
    implementer_context: str,
    reviewer_context: str,
    review_verdict: str,
    review_evidence_hash: str,
    expected_artifact_checksum: str,
    expected_split_hash: str,
    expected_brier: float,
    expected_ece: float,
) -> dict[str, Any]:
    """Create a persistence-ready payload only after distinct reviewer approval.

    The returned payload is still non-publishable and non-executable. Persisting
    or deploying it is a separate governed action.
    """
    if shadow.get("lifecycle_state") != "SHADOW" or shadow.get("promoted") is not False or shadow.get("active") is not False:
        raise ValueError("MLB_1IP_PROMOTION_SHADOW_STATE_REQUIRED")
    if shadow.get("probability_publishable") is not False or shadow.get("can_execute") is not False:
        raise ValueError("MLB_1IP_PROMOTION_SAFETY_INVARIANT_VIOLATION")

    lineage = shadow.get("validation_lineage") or {}
    metrics = shadow.get("validation_metrics") or {}
    if shadow.get("artifact_checksum") != _require_sha(expected_artifact_checksum, "MLB_1IP_PROMOTION_EXPECTED_CHECKSUM_INVALID"):
        raise ValueError("MLB_1IP_PROMOTION_ARTIFACT_CHECKSUM_MISMATCH")
    if lineage.get("split_hash") != _require_sha(expected_split_hash, "MLB_1IP_PROMOTION_EXPECTED_SPLIT_HASH_INVALID"):
        raise ValueError("MLB_1IP_PROMOTION_SPLIT_HASH_MISMATCH")
    if not math.isclose(float(metrics.get("brier", math.inf)), float(expected_brier), rel_tol=0.0, abs_tol=1e-15):
        raise ValueError("MLB_1IP_PROMOTION_BRIER_MISMATCH")
    if not math.isclose(float(metrics.get("ece", math.inf)), float(expected_ece), rel_tol=0.0, abs_tol=1e-15):
        raise ValueError("MLB_1IP_PROMOTION_ECE_MISMATCH")
    if metrics.get("gates_passed") is not True:
        raise ValueError("MLB_1IP_PROMOTION_VALIDATION_NOT_PASSED")
    if not lineage.get("validation_lineage_hash"):
        raise ValueError("MLB_1IP_PROMOTION_VALIDATION_LINEAGE_MISSING")

    if not implementer_context or not reviewer_context or str(implementer_context) == str(reviewer_context):
        raise ValueError("MLB_1IP_INDEPENDENT_REVIEW_REQUIRED")
    if str(review_verdict or "").upper() != "APPROVE_FOR_PROMOTION":
        raise ValueError("MLB_1IP_REVIEW_NOT_APPROVED")
    review_hash = _require_sha(review_evidence_hash, "MLB_1IP_REVIEW_EVIDENCE_INVALID")

    certification_material = {
        "artifact_checksum": shadow["artifact_checksum"],
        "validation_lineage_hash": lineage["validation_lineage_hash"],
        "reviewer_context": str(reviewer_context),
        "review_evidence_hash": review_hash,
    }
    result = dict(shadow)
    result["certification_id"] = f"PROP-CERT-MLB-1IP-EMP-{_sha(certification_material)[:16]}"
    result["review_evidence"] = {
        "implementer_context": str(implementer_context),
        "reviewer_context": str(reviewer_context),
        "verdict": "APPROVE_FOR_PROMOTION",
        "review_evidence_hash": review_hash,
    }
    result["lifecycle_state"] = "PROSPECTIVE_CERTIFIED"
    result["promoted"] = True
    result["active"] = True
    result["probability_publishable"] = False
    result["can_execute"] = False
    return result
