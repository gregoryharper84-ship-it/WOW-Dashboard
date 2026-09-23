"""Fitted calibration artifact contract for V17 multisport team/event models.

The sport adapters may produce a raw sporting probability, but official
publication requires calibration history. This module validates and applies a
pre-fitted calibration artifact; it never derives calibration from market prices
and never creates execution authority.

WNBA/NHL are held to the MLB-equivalent probability-quality evidence shape: a
plain stored ``health_status=PASS`` is not quantitative proof. Their binary
artifacts must also carry proper-score/calibration diagnostics and explicit
passed checks tied to a versioned/hash-identified quality policy.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
MIN_CALIBRATION_N = 30
BINARY_ARTIFACT_TYPE = "BINARY_PLATT_CALIBRATOR"
MULTICLASS_ARTIFACT_TYPE = "MULTICLASS_PLATT_CALIBRATOR"
STRICT_BINARY_QUALITY_SPORTS = frozenset({"WNBA", "NHL"})
REQUIRED_BINARY_QUALITY_CHECKS = (
    "brier_pass",
    "log_loss_pass",
    "ece_pass",
    "calibration_intercept_pass",
    "calibration_slope_pass",
    "max_calibration_bin_gap_pass",
)


class CalibrationArtifactInvalid(ValueError):
    code = "MODEL_INPUTS_INSUFFICIENT"

    def __init__(self, blockers: Iterable[str]):
        self.blockers = tuple(dict.fromkeys(str(v) for v in blockers if v))
        super().__init__(",".join(self.blockers))


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _prob(value: Any) -> float | None:
    parsed = _num(value)
    return parsed if parsed is not None and 0.0 < parsed < 1.0 else None


def _aware(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _hash64(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(ch in "0123456789abcdef" for ch in token)


def _logit(p: float) -> float:
    safe = min(max(float(p), 1e-6), 1.0 - 1e-6)
    return math.log(safe / (1.0 - safe))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def _platt(raw_probability: float, a: float, b: float) -> float:
    return min(max(_sigmoid(a * _logit(raw_probability) + b), 0.001), 0.999)


def _common_blockers(artifact: Mapping[str, Any], sport: str) -> list[str]:
    blockers: list[str] = []
    if str(artifact.get("sport") or "").upper() != sport.upper():
        blockers.append("CALIBRATION_ARTIFACT_SPORT_MISMATCH")
    if str(artifact.get("health_status") or "").upper() != "PASS":
        blockers.append("CALIBRATION_HEALTH_NOT_PASS")
    if str(artifact.get("certification_status") or "").upper() != "PASS":
        blockers.append("CALIBRATION_CERTIFICATION_NOT_PASS")
    training_n = _num(artifact.get("training_n"))
    if training_n is None or training_n < MIN_CALIBRATION_N:
        blockers.append("CALIBRATION_TRAINING_N_INSUFFICIENT")
    if not str(artifact.get("calibration_method") or "").strip():
        blockers.append("CALIBRATION_METHOD_MISSING")
    if not str(artifact.get("calibration_version") or "").strip():
        blockers.append("CALIBRATION_VERSION_MISSING")
    if not _hash64(artifact.get("source_data_hash")):
        blockers.append("CALIBRATION_SOURCE_DATA_HASH_INVALID")
    if not _hash64(artifact.get("split_hash")):
        blockers.append("CALIBRATION_SPLIT_HASH_INVALID")
    if _aware(artifact.get("fit_end")) is None:
        blockers.append("CALIBRATION_FIT_END_INVALID")
    brier = _num(artifact.get("brier_score"))
    if brier is None or not 0.0 <= brier <= 1.0:
        blockers.append("CALIBRATION_BRIER_INVALID")
    error = _num(artifact.get("calibration_error"))
    if error is None or not 0.0 <= error <= 1.0:
        blockers.append("CALIBRATION_ERROR_INVALID")
    return blockers


def _quantitative_binary_quality_blockers(
    artifact: Mapping[str, Any], sport: str
) -> list[str]:
    if sport.upper() not in STRICT_BINARY_QUALITY_SPORTS:
        return []
    blockers: list[str] = []
    if str(artifact.get("quantitative_quality_status") or "").upper() != "PASS":
        blockers.append("CALIBRATION_QUANTITATIVE_QUALITY_NOT_PASS")
    if not str(artifact.get("quality_policy_version") or "").strip():
        blockers.append("CALIBRATION_QUALITY_POLICY_VERSION_MISSING")
    if not _hash64(artifact.get("quality_policy_hash")):
        blockers.append("CALIBRATION_QUALITY_POLICY_HASH_INVALID")

    bounded_metrics = (
        ("log_loss", "CALIBRATION_LOG_LOSS_INVALID", False),
        ("ece", "CALIBRATION_ECE_INVALID", True),
        ("max_calibration_bin_gap", "CALIBRATION_MAX_BIN_GAP_INVALID", True),
    )
    for key, code, unit_interval in bounded_metrics:
        value = _num(artifact.get(key))
        if value is None or value < 0.0 or (unit_interval and value > 1.0):
            blockers.append(code)

    intercept = _num(artifact.get("calibration_intercept"))
    if intercept is None:
        blockers.append("CALIBRATION_INTERCEPT_INVALID")
    slope = _num(artifact.get("calibration_slope"))
    if slope is None or slope <= 0.0:
        blockers.append("CALIBRATION_SLOPE_INVALID")

    checks = artifact.get("quality_checks")
    if not isinstance(checks, Mapping):
        blockers.append("CALIBRATION_QUALITY_CHECKS_MISSING")
    else:
        for key in REQUIRED_BINARY_QUALITY_CHECKS:
            if checks.get(key) is not True:
                blockers.append(f"CALIBRATION_QUALITY_CHECK_NOT_PASS:{key}")
    return blockers


def validate_binary_artifact(artifact: Mapping[str, Any], sport: str) -> dict[str, Any]:
    blockers = _common_blockers(artifact, sport)
    blockers.extend(_quantitative_binary_quality_blockers(artifact, sport))
    if str(artifact.get("artifact_type") or "") != BINARY_ARTIFACT_TYPE:
        blockers.append("BINARY_CALIBRATION_ARTIFACT_TYPE_INVALID")
    a = _num(artifact.get("platt_a"))
    b = _num(artifact.get("platt_b"))
    residual = _num(artifact.get("residual_quantile_90"))
    if a is None or b is None:
        blockers.append("PLATT_COEFFICIENTS_INVALID")
    if residual is None or not 0.0 < residual < 1.0:
        blockers.append("CALIBRATION_RESIDUAL_QUANTILE_INVALID")
    if blockers:
        raise CalibrationArtifactInvalid(blockers)
    return dict(artifact)


def validate_multiclass_artifact(artifact: Mapping[str, Any], sport: str = "SOCCER") -> dict[str, Any]:
    blockers = _common_blockers(artifact, sport)
    if str(artifact.get("artifact_type") or "") != MULTICLASS_ARTIFACT_TYPE:
        blockers.append("MULTICLASS_CALIBRATION_ARTIFACT_TYPE_INVALID")
    outcomes = artifact.get("outcomes")
    if not isinstance(outcomes, Mapping):
        blockers.append("MULTICLASS_CALIBRATION_OUTCOMES_MISSING")
    else:
        for outcome in ("HOME", "DRAW", "AWAY"):
            row = outcomes.get(outcome)
            if not isinstance(row, Mapping):
                blockers.append(f"{outcome}_CALIBRATION_RECORD_MISSING")
                continue
            if _num(row.get("platt_a")) is None or _num(row.get("platt_b")) is None:
                blockers.append(f"{outcome}_PLATT_COEFFICIENTS_INVALID")
            residual = _num(row.get("residual_quantile_90"))
            if residual is None or not 0.0 < residual < 1.0:
                blockers.append(f"{outcome}_RESIDUAL_QUANTILE_INVALID")
    if blockers:
        raise CalibrationArtifactInvalid(blockers)
    return dict(artifact)


def apply_binary_artifact(raw_probability: float, artifact: Mapping[str, Any], sport: str) -> dict[str, Any]:
    valid = validate_binary_artifact(artifact, sport)
    calibrated = _platt(
        raw_probability,
        float(valid["platt_a"]),
        float(valid["platt_b"]),
    )
    payload = {
        "calibrated_probability": calibrated,
        "historical_residual_quantile_90": float(valid["residual_quantile_90"]),
        "calibration_method": str(valid["calibration_method"]),
        "calibration_version": str(valid["calibration_version"]),
        "calibration_training_n": int(valid["training_n"]),
        "calibration_brier_score": float(valid["brier_score"]),
        "calibration_error": float(valid["calibration_error"]),
        "calibration_source_data_hash": str(valid["source_data_hash"]),
        "calibration_split_hash": str(valid["split_hash"]),
        "calibration_fit_end": str(valid["fit_end"]),
        "calibration_health_status": "PASS",
    }
    if sport.upper() in STRICT_BINARY_QUALITY_SPORTS:
        payload.update(
            {
                "calibration_log_loss": float(valid["log_loss"]),
                "calibration_ece": float(valid["ece"]),
                "calibration_intercept": float(valid["calibration_intercept"]),
                "calibration_slope": float(valid["calibration_slope"]),
                "calibration_max_bin_gap": float(valid["max_calibration_bin_gap"]),
                "calibration_quantitative_quality_status": "PASS",
                "calibration_quality_policy_version": str(valid["quality_policy_version"]),
                "calibration_quality_policy_hash": str(valid["quality_policy_hash"]),
                "calibration_quality_checks": dict(valid["quality_checks"]),
            }
        )
    return payload


def apply_multiclass_artifact(raw: Mapping[str, float], artifact: Mapping[str, Any]) -> dict[str, Any]:
    valid = validate_multiclass_artifact(artifact, "SOCCER")
    outcomes = valid["outcomes"]
    transformed: dict[str, float] = {}
    for outcome in ("HOME", "DRAW", "AWAY"):
        row = outcomes[outcome]
        transformed[outcome] = _platt(
            float(raw[outcome]),
            float(row["platt_a"]),
            float(row["platt_b"]),
        )
    total = sum(transformed.values())
    calibrated = {key: value / total for key, value in transformed.items()}
    residuals = {
        key: float(outcomes[key]["residual_quantile_90"])
        for key in ("HOME", "DRAW", "AWAY")
    }
    return {
        "calibrated_outcomes": calibrated,
        "historical_residual_quantiles_90": residuals,
        "calibration_method": str(valid["calibration_method"]),
        "calibration_version": str(valid["calibration_version"]),
        "calibration_training_n": int(valid["training_n"]),
        "calibration_brier_score": float(valid["brier_score"]),
        "calibration_error": float(valid["calibration_error"]),
        "calibration_source_data_hash": str(valid["source_data_hash"]),
        "calibration_split_hash": str(valid["split_hash"]),
        "calibration_fit_end": str(valid["fit_end"]),
        "calibration_health_status": "PASS",
    }


def artifact_fingerprint(artifact: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(artifact), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "BINARY_ARTIFACT_TYPE",
    "CAN_EXECUTE",
    "CalibrationArtifactInvalid",
    "MIN_CALIBRATION_N",
    "MULTICLASS_ARTIFACT_TYPE",
    "REQUIRED_BINARY_QUALITY_CHECKS",
    "STRICT_BINARY_QUALITY_SPORTS",
    "apply_binary_artifact",
    "apply_multiclass_artifact",
    "artifact_fingerprint",
    "validate_binary_artifact",
    "validate_multiclass_artifact",
]
