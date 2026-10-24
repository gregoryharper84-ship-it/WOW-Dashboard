"""Immutable shadow calibration artifact for NBA/WNBA fitted team-event models.

This module converts the existing leakage-safe Phase-B Platt replay into the
standard V17 binary calibration artifact shape. It does not certify or promote
the artifact: certification_status remains REVIEW_REQUIRED until independent
replay/review approves the exact payload hash.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

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

    # A correctly fitted calibration status does not validate source row shapes:
    # these fields enter durable review receipts and must be independently
    # reproducible, chronological, binary-labelled and finite.
    if any(
        isinstance(p, bool) or not isinstance(p, (int, float, np.number))
        or not math.isfinite(float(p)) or not 0.0 < float(p) < 1.0
        for p in raw_probabilities
    ):
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_RAW_PROBABILITY_INVALID")
    if any(
        type(y) not in (int, np.int64, np.int32) or int(y) not in (0, 1)
        for y in outcomes
    ):
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_OUTCOME_NOT_BINARY")
    if any(
        type(fold) not in (int, np.int64, np.int32) or int(fold) < 0
        for fold in fold_assignments
    ):
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_FOLD_INVALID")
    seen_folds = list(dict.fromkeys(int(fold) for fold in fold_assignments))
    if (len(seen_folds) < 6 or seen_folds != list(range(len(seen_folds)))
        or any(left > right for left, right in zip(fold_assignments, fold_assignments[1:]))):
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_FOLD_NOT_CHRONOLOGICAL")
    audit = getattr(fit, "fold_train_audit", None)
    if not isinstance(audit, Mapping) or set(audit) != set(seen_folds[1:]):
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_FOLD_TRAIN_AUDIT_MISSING")
    for fold in seen_folds[1:]:
        # Expanding-window fits train on every earlier fold, never on their
        # own or any future fold. This guards fake PASS status decorations.
        trained_on = audit.get(fold)
        if not isinstance(trained_on, (tuple, list)) or tuple(trained_on) != tuple(range(fold)):
            raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_FOLD_TRAIN_AUDIT_INVALID")

    metrics = fit.metrics
    checks = (
        ("brier", 0.0, 1.0), ("log_loss", 0.0, None),
        ("ece", 0.0, 1.0), ("calibration_bias", -1.0, 1.0),
    )
    for key, minimum, maximum in checks:
        try:
            value = float(getattr(metrics, key))
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_METRIC_INVALID") from exc
        if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
            raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_METRIC_INVALID")

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

    if any(left > right for left, right in zip(parsed_timestamps, parsed_timestamps[1:])):
        raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_TIMESTAMP_NOT_CHRONOLOGICAL")
    # Exact UTC timestamp/date groups must never straddle model training and
    # validation folds. Multiple sporting results on the same date are a
    # chronological batch, not independent knowledge for each other.
    fold_by_day: dict[str, int] = {}
    for ts, fold in zip(parsed_timestamps, fold_assignments):
        day = ts.date().isoformat()
        if day in fold_by_day and fold_by_day[day] != int(fold):
            raise BasketballCalibrationArtifactError("BASKETBALL_CALIBRATION_DATE_SPLIT_ACROSS_FOLDS")
        fold_by_day[day] = int(fold)

    raw = np.asarray(raw_probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    calibrated = np.asarray([fit.coefficients.apply(float(p)) for p in raw], dtype=float)
    if not np.all(np.isfinite(calibrated)) or not np.all((calibrated > 0.0) & (calibrated < 1.0)):
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
        "fold_train_audit_hash": _sha({str(k): list(audit[k]) for k in seen_folds[1:]}),
        "residual_basis": "FULL_DATA_IN_SAMPLE_NOT_CERTIFIED_FOR_BOUNDS",
        "bounds_preapproval_status": REVIEW_REQUIRED,
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
