"""Immutable shadow calibration artifact for NBA/WNBA fitted team-event models.

This module converts the existing leakage-safe Phase-B Platt replay into the
standard V17 binary calibration artifact shape. It does not certify or promote
the artifact: certification_status remains REVIEW_REQUIRED until independent
replay/review approves the exact payload hash.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np

from calibration import CalibrationStatus, PlattFitOutcome
from v17.multisport_team_event_calibration import BINARY_ARTIFACT_TYPE

CAN_EXECUTE = False
REVIEW_REQUIRED = "REVIEW_REQUIRED"


class BasketballCalibrationArtifactError(ValueError):
    pass


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def build_shadow_binary_calibration_artifact(
    *,
    sport: str,
    model_family: str,
    model_version: str,
    calibration_version: str,
    raw_probabilities: Sequence[float],
    outcomes: Sequence[int],
    fold_assignments: Sequence[int],
    timestamps: Sequence[str],
    fit: PlattFitOutcome,
) -> tuple[dict[str, Any], str]:
    normalized = str(sport or "").strip().upper()
    if normalized not in {"NBA", "WNBA"}:
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_SPORT_UNSUPPORTED")
    n = len(raw_probabilities)
    if not (n == len(outcomes) == len(fold_assignments) == len(timestamps)):
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_ROW_LENGTH_MISMATCH")
    if n < 200:
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_HISTORY_INSUFFICIENT")
    if fit.result is None or fit.result.calibration_status != CalibrationStatus.PLATT_TIME_SPLIT_V1:
        raise BasketballCalibrationArtifactError("BASKETBALL_PLATT_CALIBRATION_NOT_PROMOTABLE")

    normalized_timestamps: list[str] = []
    parsed_timestamps: list[datetime] = []
    for raw_ts in timestamps:
        try:
            parsed = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_TIMESTAMP_INVALID") from exc
        if parsed.utcoffset() is None:
            raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_TIMESTAMP_INVALID")
        parsed = parsed.astimezone(timezone.utc)
        parsed_timestamps.append(parsed)
        normalized_timestamps.append(parsed.isoformat())

    raw = np.asarray(raw_probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    calibrated = np.asarray([fit.coefficients.apply(float(p)) for p in raw], dtype=float)
    if not np.all(np.isfinite(calibrated)):
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATED_HISTORY_NONFINITE")

    residual_q90 = float(np.quantile(np.abs(calibrated - y), 0.90))
    if not 0.0 < residual_q90 < 1.0:
        raise BasketballCalibrationArtifactError("BASKETBALL_RESIDUAL_Q90_INVALID")

    source_rows = [
        {
            "raw_probability": float(p),
            "outcome": int(o),
            "timestamp": str(ts),
        }
        for p, o, ts in zip(raw_probabilities, outcomes, normalized_timestamps)
    ]
    split_rows = [
        {"timestamp": str(ts), "fold": int(fold)}
        for ts, fold in zip(normalized_timestamps, fold_assignments)
    ]
    fit_end = max(parsed_timestamps).isoformat()

    # calibration.PlattCoefficients applies sigmoid(intercept + slope*logit(p)).
    # multisport_team_event_calibration expects platt_a=slope, platt_b=intercept.
    artifact = {
        "artifact_type": BINARY_ARTIFACT_TYPE,
        "sport": normalized,
        "model_family": str(model_family),
        "model_version": str(model_version),
        "calibration_method": CalibrationStatus.PLATT_TIME_SPLIT_V1,
        "calibration_version": str(calibration_version),
        "training_n": n,
        "platt_a": float(fit.coefficients.b),
        "platt_b": float(fit.coefficients.a),
        "residual_quantile_90": residual_q90,
        "brier_score": float(fit.metrics.brier),
        "log_loss": float(fit.metrics.log_loss),
        "calibration_error": float(fit.metrics.ece),
        "calibration_bias": float(fit.metrics.calibration_bias),
        "source_data_hash": _sha(source_rows),
        "split_hash": _sha(split_rows),
        "fit_end": fit_end,
        "health_status": "PASS",
        "certification_status": REVIEW_REQUIRED,
        "independent_verification_status": REVIEW_REQUIRED,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    return artifact, _sha(artifact)


__all__ = [
    "BasketballCalibrationArtifactError",
    "CAN_EXECUTE",
    "REVIEW_REQUIRED",
    "build_shadow_binary_calibration_artifact",
]
