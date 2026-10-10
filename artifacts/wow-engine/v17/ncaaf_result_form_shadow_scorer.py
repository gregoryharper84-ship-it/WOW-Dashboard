"""Research-only fitted NCAAF result/form candidate shadow evaluator.

Consumes exact prior-form features and the persisted fitted logistic candidate
without creating a governed team/event probability. The input artifact is
uncertified; the output must never enter rankings, market-value, or V17 terminal
publishing. The distinct research_raw_probability key is diagnostic only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import exp, isfinite
from typing import Any, Mapping

from v17.ncaaf_result_form_candidate import (
    FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SHADOW_SCORER_VERSION = "NCAAF_RESULT_FORM_FITTED_SHADOW_V1"


class NCAAFShadowScoreBlocked(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def _artifact_digest(value: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _aware(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_TIME_INVALID") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_TIME_INVALID")
    return dt.astimezone(timezone.utc)


def _number(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NCAAFShadowScoreBlocked(code)
    number = float(value)
    if not isfinite(number):
        raise NCAAFShadowScoreBlocked(code)
    return number


def _vector(value: Any, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != len(FEATURE_NAMES):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_ARTIFACT_VECTOR_INVALID", name)
    return [_number(item, "NCAAF_SHADOW_ARTIFACT_VECTOR_INVALID") for item in value]


def score_ncaaf_research_shadow(
    candidate: Mapping[str, Any],
    forward_features: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate fitted research coefficients without certification or calibration.

    Treat BOTH inputs as untrusted. This function has no DB access, model
    registry write, model promotion, market, ranking or trading capability.
    """
    if not isinstance(candidate, Mapping) or not isinstance(forward_features, Mapping):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_INPUT_INVALID")
    if (candidate.get("sport") != "NCAAF"
            or candidate.get("model_family") != MODEL_FAMILY
            or candidate.get("feature_schema_version") != FEATURE_SCHEMA_VERSION):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_MODEL_ROUTE_MISMATCH")
    if (candidate.get("lifecycle_state") != "CANDIDATE"
            or candidate.get("research_screen_pass") is not True
            or any(candidate.get(key) is not False for key in (
                "promoted", "active", "probability_publishable", "can_execute",
                "automatic_certification", "automatic_promotion",
            ))):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_CANDIDATE_NOT_INERT")
    version = str(candidate.get("model_artifact_version") or "").strip()
    if not version or not version.startswith("NCAAF_RESULT_FORM_LOGIT_V1_"):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_ARTIFACT_IDENTITY_INVALID")
    dataset_hash = str(candidate.get("training_dataset_hash") or "").strip().lower()
    if len(dataset_hash) != 64 or any(ch not in "0123456789abcdef" for ch in dataset_hash):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_ARTIFACT_IDENTITY_INVALID")
    artifact = candidate.get("artifact_payload")
    if not isinstance(artifact, Mapping):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_ARTIFACT_INVALID")
    checksum = str(candidate.get("artifact_checksum") or "").strip().lower()
    if len(checksum) != 64 or _artifact_digest(artifact) != checksum:
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_ARTIFACT_CHECKSUM_MISMATCH")
    if (artifact.get("artifact_format") != "STANDARDIZED_LOGISTIC_JSON_V1"
            or artifact.get("model_family") != MODEL_FAMILY
            or tuple(artifact.get("feature_names") or ()) != FEATURE_NAMES):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_ARTIFACT_SCHEMA_MISMATCH")
    if (forward_features.get("status") != "RESEARCH_FORWARD_FEATURES_ONLY"
            or forward_features.get("candidate_model_family") != MODEL_FAMILY
            or forward_features.get("feature_schema_version") != FEATURE_SCHEMA_VERSION
            or forward_features.get("probability_publishable") is not False
            or forward_features.get("can_execute") is not False):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_FORWARD_PACKAGE_INVALID")
    manifest = forward_features.get("source_manifest")
    if not isinstance(manifest, Mapping):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_MANIFEST_INVALID")
    if (manifest.get("canonical_identity_verified_by_caller") is not True
            or manifest.get("canonical_identity_source") != "CFBD:/games"
            or manifest.get("canonical_identity_resolution") != "CFBD_EXACT_PARTICIPANTS_START_MATCH"
            or manifest.get("market_features_used") is not False
            or manifest.get("archived_pregame_snapshot") is not False
            or manifest.get("official_event_id") != forward_features.get("official_event_id")):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_MANIFEST_INVALID")
    manifest_hash = str(forward_features.get("source_manifest_sha256") or "").lower()
    if len(manifest_hash) != 64 or _artifact_digest(manifest) != manifest_hash:
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_MANIFEST_CHECKSUM_MISMATCH")
    event_start = _aware(manifest.get("event_start_time"))
    source_start = _aware(manifest.get("canonical_event_start_time"))
    if abs((source_start - event_start).total_seconds()) > 3600:
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_CANONICAL_START_MISMATCH")
    as_of = _aware(forward_features.get("feature_as_of"))
    if as_of >= event_start or as_of != _aware(manifest.get("feature_as_of")):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_FEATURE_TIME_INVALID")
    features = forward_features.get("features")
    if not isinstance(features, Mapping) or tuple(features.keys()) != FEATURE_NAMES:
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_FEATURE_SCHEMA_MISMATCH")
    values = [_number(features[name], "NCAAF_SHADOW_FEATURE_VALUE_INVALID") for name in FEATURE_NAMES]
    canonical_neutral = manifest.get("canonical_neutral_site")
    if type(canonical_neutral) is not bool:
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_NEUTRAL_SITE_SOURCE_MISSING")
    if features["neutral_site"] != float(canonical_neutral):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_NEUTRAL_SITE_SOURCE_CONTRADICTION")
    # Confirm these exact numeric features, not merely the event/source labels,
    # are bound into the previously verified immutable research manifest.
    feature_digest = _artifact_digest(features)
    if (str(manifest.get("features_sha256") or "").lower() != feature_digest
            or str(forward_features.get("features_sha256") or "").lower() != feature_digest):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_FEATURE_HASH_MISMATCH")
    mean = _vector(artifact.get("scaler_mean"), "scaler_mean")
    scales = _vector(artifact.get("scaler_scale"), "scaler_scale")
    coefs = _vector(artifact.get("coefficients"), "coefficients")
    if any(scale <= 0 for scale in scales):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_ARTIFACT_SCALE_INVALID")
    intercept = _number(artifact.get("intercept"), "NCAAF_SHADOW_ARTIFACT_VECTOR_INVALID")
    linear = intercept + sum(
        coef * (value - average) / scale
        for value, average, scale, coef in zip(values, mean, scales, coefs)
    )
    if not isfinite(linear):
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_NUMERIC_OVERFLOW")
    if linear >= 0:
        raw = 1.0 / (1.0 + exp(-linear))
    else:
        e = exp(linear)
        raw = e / (1.0 + e)
    if not isfinite(raw) or not 0.0 <= raw <= 1.0:
        raise NCAAFShadowScoreBlocked("NCAAF_SHADOW_NUMERIC_OVERFLOW")
    return {
        "status": "RESEARCH_SHADOW_UNCERTIFIED",
        "shadow_scorer_version": SHADOW_SCORER_VERSION,
        "official_event_id": forward_features["official_event_id"],
        "model_artifact_version": version,
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "training_dataset_hash": dataset_hash,
        "artifact_checksum": checksum,
        "source_manifest_sha256": manifest_hash,
        "features_sha256": feature_digest,
        "research_raw_probability": raw,
        "model_probability": None,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "calibration_status": "NOT_EVALUATED",
        "certification_status": "UNCERTIFIED_RESEARCH_CANDIDATE",
        "rank_eligible": False,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "SHADOW_SCORER_VERSION",
    "NCAAFShadowScoreBlocked", "score_ncaaf_research_shadow",
]
