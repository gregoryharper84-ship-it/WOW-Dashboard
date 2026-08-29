"""Apply an active governed NCAAF static calibrator to a raw model probability.

This is the static calibration gate only. It never substitutes for failure-path
reconciliation, Dynamic Calibration, final lower-bound publication governance,
or immutable pregame persistence. can_execute remains false.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SUPPORTED_METHOD = "EMPIRICAL_WILSON_BINS_V1"


class NCAAFStaticCalibrationUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StaticCalibrationResult:
    calibrated_probability: float
    calibration_lower_diagnostic: float
    calibration_upper_diagnostic: float
    calibrator_version: str
    calibration_method: str
    calibration_training_n: int
    calibration_health_status: str
    probability_publishable: bool = False
    can_execute: bool = False


def _strict_probability(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NCAAFStaticCalibrationUnavailable(code, "probability must be numeric")
    p = float(value)
    if not isfinite(p) or not 0.0 < p < 1.0:
        raise NCAAFStaticCalibrationUnavailable(code, "probability must satisfy strict 0<p<1")
    return p


def resolve_active_calibrator(client: Any, *, model_artifact_version: str) -> Mapping[str, Any]:
    try:
        result = client.rpc(
            "wow_ncaaf_active_calibrator",
            {"p_model_artifact_version": model_artifact_version},
        ).execute()
    except Exception as exc:
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_REGISTRY_UNAVAILABLE", "calibrator registry read failed") from exc
    payload = getattr(result, "data", None)
    if not isinstance(payload, Mapping):
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_REGISTRY_INVALID_RESPONSE", "invalid calibrator registry response")
    if payload.get("ok") is not True:
        raise NCAAFStaticCalibrationUnavailable(str(payload.get("code") or "NCAAF_CERTIFIED_CALIBRATOR_NOT_FOUND"), "active certified calibrator unavailable")
    if str(payload.get("model_artifact_version") or "") != model_artifact_version:
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_MODEL_VERSION_MISMATCH", "calibrator/model artifact mismatch")
    if str(payload.get("calibration_method") or "") != SUPPORTED_METHOD:
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATION_METHOD_UNSUPPORTED", "unsupported calibration method")
    if str(payload.get("calibration_health_status") or "") != "PASS" or payload.get("probability_publishable") is not True:
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATION_HEALTH_NOT_PASS", "static calibration health has not passed")
    if int(payload.get("training_n") or 0) < 50:
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATION_SAMPLE_INSUFFICIENT", "active calibrator requires >=50 rows")
    return payload


def apply_static_calibration(*, raw_probability: float, calibrator: Mapping[str, Any]) -> StaticCalibrationResult:
    raw = _strict_probability(raw_probability, "NCAAF_RAW_PROBABILITY_INVALID")
    payload = calibrator.get("payload")
    if not isinstance(payload, Mapping):
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_PAYLOAD_INVALID", "payload must be an object")
    if str(payload.get("method") or calibrator.get("calibration_method") or "") != SUPPORTED_METHOD:
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATION_METHOD_UNSUPPORTED", "unsupported calibration method")
    bins = payload.get("bins")
    if not isinstance(bins, list) or not bins:
        raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_EMPTY", "calibrator bins unavailable")

    parsed = []
    for entry in bins:
        if not isinstance(entry, Mapping):
            raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_BIN_INVALID", "bin must be an object")
        try:
            raw_min = float(entry["raw_min"])
            raw_max = float(entry["raw_max"])
            raw_mean = float(entry["raw_mean"])
            point = _strict_probability(entry["calibrated_probability"], "NCAAF_CALIBRATED_PROBABILITY_INVALID")
            lower = float(entry["wilson_lower"])
            upper = float(entry["wilson_upper"])
        except (KeyError, TypeError, ValueError) as exc:
            raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_BIN_INVALID", "bin fields malformed") from exc
        if not all(isfinite(v) for v in (raw_min, raw_max, raw_mean, lower, upper)):
            raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_BIN_INVALID", "bin contains non-finite values")
        if raw_min > raw_max or not 0.0 <= lower <= point <= upper <= 1.0:
            raise NCAAFStaticCalibrationUnavailable("NCAAF_CALIBRATOR_BIN_INVALID", "bin bounds invalid")
        parsed.append((raw_min, raw_max, raw_mean, point, lower, upper))

    containing = [b for b in parsed if b[0] <= raw <= b[1]]
    chosen = min(containing or parsed, key=lambda b: abs(b[2] - raw))
    _, _, _, point, lower, upper = chosen
    # Wilson endpoints are calibration diagnostics, not the Full Model's final
    # Dynamic Calibration lower/upper publication bounds.
    return StaticCalibrationResult(
        calibrated_probability=point,
        calibration_lower_diagnostic=lower,
        calibration_upper_diagnostic=upper,
        calibrator_version=str(calibrator.get("calibrator_version") or ""),
        calibration_method=SUPPORTED_METHOD,
        calibration_training_n=int(calibrator.get("training_n") or 0),
        calibration_health_status="PASS",
    )


def calibrate_from_registry(client: Any, *, model_artifact_version: str, raw_probability: float) -> StaticCalibrationResult:
    calibrator = resolve_active_calibrator(client, model_artifact_version=model_artifact_version)
    return apply_static_calibration(raw_probability=raw_probability, calibrator=calibrator)
