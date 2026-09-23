"""Runtime scoring for separately certified V17 team/event artifacts.

The provider accepts a server-hydrated feature vector only.  It never reads market
prices, never manufactures inputs, and never grants execution authority.
"""
from __future__ import annotations

from math import exp, isfinite
from typing import Any, Mapping

CAN_EXECUTE = False


class CertifiedArtifactUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def resolve_certified_artifact(db: Any, *, sport: str, league: str, model_family: str | None = None) -> dict[str, Any]:
    result = db.rpc("wow_resolve_team_event_certified_artifact", {
        "p_sport": str(sport).upper(),
        "p_league": str(league).upper(),
        "p_model_family": model_family,
    }).execute()
    rows = list(result.data or [])
    if not rows:
        raise CertifiedArtifactUnavailable("CERTIFIED_ARTIFACT_REQUIRED", f"{sport}:{league}")
    row = dict(rows[0])
    if row.get("probability_publishable") is not True or row.get("can_execute") is not False:
        raise CertifiedArtifactUnavailable("CERTIFIED_ARTIFACT_ROUTE_INVALID", f"{sport}:{league}")
    return row


def _vector(artifact: Mapping[str, Any], features: Mapping[str, Any]) -> list[float]:
    names = [str(v) for v in artifact.get("feature_names") or []]
    if not names:
        raise CertifiedArtifactUnavailable("CERTIFIED_FEATURE_SCHEMA_EMPTY", "feature names missing")
    values: list[float] = []
    missing: list[str] = []
    for name in names:
        value = features.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
            missing.append(name)
        else:
            values.append(float(value))
    if missing:
        raise CertifiedArtifactUnavailable("MODEL_INPUTS_INSUFFICIENT", ",".join(missing[:20]))
    return values


def _standardize(values: list[float], artifact: Mapping[str, Any]) -> list[float]:
    means = [float(v) for v in artifact.get("scaler_mean") or []]
    scales = [float(v) for v in artifact.get("scaler_scale") or []]
    if len(means) != len(values) or len(scales) != len(values):
        raise CertifiedArtifactUnavailable("CERTIFIED_ARTIFACT_DIMENSION_INVALID", "scaler dimensions")
    return [(value - mean) / (scale if abs(scale) > 1e-12 else 1.0) for value, mean, scale in zip(values, means, scales)]


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = exp(-value); return 1.0 / (1.0 + z)
    z = exp(value); return z / (1.0 + z)


def _softmax(values: list[float]) -> list[float]:
    top = max(values)
    weights = [exp(v - top) for v in values]
    total = sum(weights)
    return [v / total for v in weights]


def _nearest_bin(probability: float, bins: list[Mapping[str, Any]], *, key: str = "raw_mean") -> Mapping[str, Any]:
    if not bins:
        raise CertifiedArtifactUnavailable("CALIBRATED_BOUNDS_UNAVAILABLE", "reliability bins missing")
    return min(bins, key=lambda row: abs(float(row.get(key) or 0.0) - probability))


def _binary_score(artifact: Mapping[str, Any], calibrator: Mapping[str, Any], features: Mapping[str, Any]) -> dict[str, Any]:
    x = _standardize(_vector(artifact, features), artifact)
    coef = [float(v) for v in artifact.get("coefficients") or []]
    if len(coef) != len(x):
        raise CertifiedArtifactUnavailable("CERTIFIED_ARTIFACT_DIMENSION_INVALID", "coefficient dimensions")
    raw = _sigmoid(float(artifact.get("intercept") or 0.0) + sum(a*b for a,b in zip(coef, x)))
    method = str(calibrator.get("method") or "")
    if method == "EMPIRICAL_WILSON_BINS_V1":
        selected = _nearest_bin(raw, list(calibrator.get("bins") or []))
        point = float(selected["calibrated_probability"])
        lower, upper = float(selected["wilson_lower"]), float(selected["wilson_upper"])
    elif method == "IDENTITY_RAW_PROBABILITY_V1":
        point = raw
        selected = _nearest_bin(raw, list(calibrator.get("uncertainty_bins") or []))
        lower, upper = float(selected["wilson_lower"]), float(selected["wilson_upper"])
    else:
        raise CertifiedArtifactUnavailable("CERTIFIED_CALIBRATOR_UNSUPPORTED", method)
    return {
        "raw_probability": raw,
        "probabilities": {"HOME": point, "AWAY": 1.0-point},
        "lower_bounds": {"HOME": lower, "AWAY": max(0.0, 1.0-upper)},
        "upper_bounds": {"HOME": upper, "AWAY": min(1.0, 1.0-lower)},
        "calibration_method": method,
    }


def _multiclass_score(artifact: Mapping[str, Any], calibrator: Mapping[str, Any], features: Mapping[str, Any]) -> dict[str, Any]:
    x = _standardize(_vector(artifact, features), artifact)
    classes = [str(v) for v in artifact.get("classes") or []]
    coefs = [[float(v) for v in row] for row in artifact.get("coefficients") or []]
    intercepts = [float(v) for v in artifact.get("intercepts") or []]
    if not classes or len(classes) != len(coefs) or len(classes) != len(intercepts) or any(len(row) != len(x) for row in coefs):
        raise CertifiedArtifactUnavailable("CERTIFIED_ARTIFACT_DIMENSION_INVALID", "multiclass dimensions")
    logits = [bias + sum(a*b for a,b in zip(row,x)) for bias,row in zip(intercepts,coefs)]
    raw = _softmax(logits)
    method = str(calibrator.get("method") or "")
    if method != "TEMPERATURE_SCALE_CHRONOLOGICAL_V1":
        raise CertifiedArtifactUnavailable("CERTIFIED_CALIBRATOR_UNSUPPORTED", method)
    temperature = float(calibrator.get("temperature") or 0.0)
    if temperature <= 0:
        raise CertifiedArtifactUnavailable("CERTIFIED_CALIBRATOR_INVALID", "temperature")
    calibrated = _softmax([__import__("math").log(max(p,1e-12))/temperature for p in raw])
    reliability = calibrator.get("calibration_reliability") if isinstance(calibrator.get("calibration_reliability"), Mapping) else {}
    lower: dict[str,float] = {}; upper: dict[str,float] = {}
    for label, probability in zip(classes, calibrated):
        selected = _nearest_bin(probability, list(reliability.get(label) or []), key="probability_mean")
        lower[label], upper[label] = float(selected["wilson_lower"]), float(selected["wilson_upper"])
    return {
        "raw_probabilities": dict(zip(classes,raw)),
        "probabilities": dict(zip(classes,calibrated)),
        "lower_bounds": lower, "upper_bounds": upper,
        "calibration_method": method,
    }


def score_certified_feature_vector(certified: Mapping[str, Any], features: Mapping[str, Any]) -> dict[str, Any]:
    artifact = dict(certified.get("artifact_payload") or {})
    calibrator = dict(certified.get("calibrator_payload") or {})
    fmt = str(artifact.get("artifact_format") or "")
    if fmt == "STANDARDIZED_LOGISTIC_JSON_V1":
        result = _binary_score(artifact, calibrator, features)
    elif fmt == "STANDARDIZED_MULTINOMIAL_LOGISTIC_JSON_V1":
        result = _multiclass_score(artifact, calibrator, features)
    else:
        raise CertifiedArtifactUnavailable("CERTIFIED_ARTIFACT_FORMAT_UNSUPPORTED", fmt)
    result.update({
        "model_family": certified.get("model_family"),
        "model_artifact_version": certified.get("model_artifact_version"),
        "feature_schema_version": certified.get("feature_schema_version"),
        "lifecycle_state": certified.get("lifecycle_state"),
        "probability_publishable": True,
        "can_execute": False,
    })
    return result


__all__ = ["CAN_EXECUTE", "CertifiedArtifactUnavailable", "resolve_certified_artifact", "score_certified_feature_vector"]
