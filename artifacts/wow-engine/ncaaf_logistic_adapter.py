"""Reviewed runtime adapter for NCAAF_LOGISTIC_V1."""
from __future__ import annotations

from math import exp, isfinite
from typing import Any, Mapping

from ncaaf_feature_transform import model_features_from_snapshot
from ncaaf_fitted_provider import (
    NCAAFFittedProviderUnavailable,
    NCAAFInferenceRequest,
    RawNCAAFWinProbability,
    ResolvedNCAAFArtifact,
    register_model_family_adapter,
)
from ncaaf_trainer import FEATURES

CAN_EXECUTE = False
MODEL_FAMILY = "NCAAF_LOGISTIC_V1"
ARTIFACT_FORMAT = "STANDARDIZED_LOGISTIC_JSON_V1"


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value)
        return 1.0 / (1.0 + z)
    z = exp(value)
    return z / (1.0 + z)


def logistic_adapter(artifact: ResolvedNCAAFArtifact, request: NCAAFInferenceRequest, features: Mapping[str, Any]) -> RawNCAAFWinProbability:
    if artifact.artifact_format != ARTIFACT_FORMAT:
        raise NCAAFFittedProviderUnavailable("NCAAF_MODEL_ARTIFACT_FORMAT_UNSUPPORTED", artifact.artifact_format)
    if str(features.get("official_event_id") or "") != request.official_event_id:
        raise NCAAFFittedProviderUnavailable("NCAAF_EVENT_IDENTITY_MISMATCH", "feature snapshot event mismatch")
    if str(features.get("home_team") or "") != request.home_team or str(features.get("away_team") or "") != request.away_team:
        raise NCAAFFittedProviderUnavailable("NCAAF_TEAM_IDENTITY_MISMATCH", "feature snapshot team mismatch")
    if str(features.get("feature_schema_version") or "") != request.feature_schema_version:
        raise NCAAFFittedProviderUnavailable("NCAAF_FEATURE_SCHEMA_MISMATCH", "feature snapshot schema mismatch")
    if str(features.get("feature_as_of") or "") != request.feature_as_of:
        raise NCAAFFittedProviderUnavailable("NCAAF_FEATURE_TIMESTAMP_MISMATCH", "feature snapshot timestamp mismatch")

    payload = artifact.artifact_payload
    names = payload.get("feature_names")
    means = payload.get("scaler_mean")
    scales = payload.get("scaler_scale")
    coefs = payload.get("coefficients")
    intercept = payload.get("intercept")
    if names != list(FEATURES) or not all(isinstance(v, list) for v in (means, scales, coefs)):
        raise NCAAFFittedProviderUnavailable("NCAAF_MODEL_ARTIFACT_PAYLOAD_INVALID", "feature schema or vectors invalid")
    if len(means) != len(FEATURES) or len(scales) != len(FEATURES) or len(coefs) != len(FEATURES):
        raise NCAAFFittedProviderUnavailable("NCAAF_MODEL_ARTIFACT_PAYLOAD_INVALID", "vector lengths invalid")
    try:
        b0 = float(intercept)
        transformed = model_features_from_snapshot(features)
        logit = b0
        for i, name in enumerate(FEATURES):
            mean = float(means[i])
            scale = float(scales[i])
            coef = float(coefs[i])
            if not all(isfinite(v) for v in (mean, scale, coef)) or scale <= 0:
                raise ValueError("invalid scaler/model coefficient")
            logit += coef * ((float(transformed[name]) - mean) / scale)
    except (TypeError, ValueError) as exc:
        raise NCAAFFittedProviderUnavailable("NCAAF_MODEL_ARTIFACT_PAYLOAD_INVALID", "non-finite or invalid model parameters") from exc
    if not isfinite(logit):
        raise NCAAFFittedProviderUnavailable("NCAAF_MODEL_OUTPUT_INVALID", "non-finite model logit")
    home = _sigmoid(logit)
    result = RawNCAAFWinProbability(
        home_probability=home,
        away_probability=1.0 - home,
        model_artifact_version=artifact.model_artifact_version,
        artifact_id=artifact.artifact_id,
    )
    result.validate()
    return result


register_model_family_adapter(MODEL_FAMILY, logistic_adapter)
