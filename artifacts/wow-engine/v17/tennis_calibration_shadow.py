"""ATP/WTA research-only calibration challenger for an EXISTING frozen fit.

Replays the original logistic artifact and evaluates a predeclared shrinkage
grid using the calibration partition only. The untouched test partition is
used strictly for reporting, never for calibrator/weight selection. This
does not grant provenance, certification, probability publication or execution.
Historical reconstructions are not immutable pregame prediction receipts.
"""
from __future__ import annotations

from hashlib import sha256
from json import dumps
from math import isclose
from typing import Sequence

import numpy as np
from v17.binary_candidate_lifecycle import (
    BinaryCandidate,
    BinaryCandidateError,
    BinaryTrainingRow,
    _calibration_metrics,
    _dataset_hash,
    _fit_calibrator,
    _map_calibrator,
    _matrix,
)

CAN_EXECUTE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PROBABILITY_PUBLISHABLE = False
CONTRACT = "TENNIS_CALIBRATION_SHRINKAGE_SHADOW_V1"
ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)


class TennisCalibrationShadowError(ValueError):
    """Fail-closed research input error with a stable reason code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _raw_predictions(features: np.ndarray, artifact: dict, names: tuple[str, ...]) -> np.ndarray:
    """Score a stored logistic fit; never refit the sporting model."""
    if tuple(artifact.get("feature_names", ())) != names:
        raise TennisCalibrationShadowError("TENNIS_ARTIFACT_FEATURE_IDENTITY_MISMATCH")
    try:
        means = np.asarray(artifact["scaler_mean"], dtype=float)
        scales = np.asarray(artifact["scaler_scale"], dtype=float)
        coefficients = np.asarray(artifact["coefficients"], dtype=float)
        intercept = float(artifact["intercept"])
    except (ValueError, TypeError, KeyError) as exc:
        raise TennisCalibrationShadowError("TENNIS_FROZEN_ARTIFACT_INVALID") from exc
    n = len(names)
    if (means.shape != (n,) or scales.shape != (n,)
            or coefficients.shape != (n,)
            or not np.all(np.isfinite(means))
            or not np.all(np.isfinite(scales))
            or not np.all(np.isfinite(coefficients))
            or not np.isfinite(intercept)
            or np.any(scales <= 0)):
        raise TennisCalibrationShadowError("TENNIS_FROZEN_ARTIFACT_INVALID")
    logits = intercept + ((features - means) / scales) @ coefficients
    if not np.all(np.isfinite(logits)):
        raise TennisCalibrationShadowError("TENNIS_FROZEN_ARTIFACT_INVALID")
    return np.clip(1.0 / (1.0 + np.exp(-np.clip(logits, -80.0, 80.0))), 1e-12, 1 - 1e-12)


def replay_tennis_calibration_shadow(
    rows: Sequence[BinaryTrainingRow],
    candidate: BinaryCandidate,
) -> dict[str, object]:
    """Evaluate one frozen ATP/WTA artifact, without updating or selecting it.

    Select alpha only on chronological later calibration rows, using a Wilson
    fit from earlier calibration rows. Refit only the calibration map on the
    complete calibration partition. The test block cannot influence alpha.
    """
    family = str(candidate.model_family)
    if not family.startswith("TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1_"):
        raise TennisCalibrationShadowError("TENNIS_MODEL_FAMILY_UNSUPPORTED")
    tour = family.removeprefix("TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1_")
    if tour not in ("ATP", "WTA"):
        raise TennisCalibrationShadowError("TENNIS_TOUR_UNSUPPORTED")
    if not rows or any(not r.event_id.startswith(f"TENNIS:{tour}:") for r in rows):
        raise TennisCalibrationShadowError("TENNIS_EVENT_TOUR_MISMATCH")
    if list(rows) != sorted(rows, key=lambda r: r.event_start_time):
        raise TennisCalibrationShadowError("TENNIS_ROWS_NOT_CHRONOLOGICAL")

    names = tuple(str(n) for n in candidate.feature_names)
    try:
        features, targets = _matrix(rows, names)
        digest = _dataset_hash(rows, names, family)
    except (ValueError, TypeError, KeyError, BinaryCandidateError) as exc:
        raise TennisCalibrationShadowError("TENNIS_FROZEN_CORPUS_INVALID") from exc
    if digest != candidate.dataset_hash:
        raise TennisCalibrationShadowError("TENNIS_FROZEN_DATASET_HASH_MISMATCH")

    artifact = dict(candidate.artifact_payload)
    a = artifact.get("train_end_index")
    b = artifact.get("calibration_end_index")
    if (not isinstance(a, int) or isinstance(a, bool)
            or not isinstance(b, int) or isinstance(b, bool)
            or a < 1 or b - a < 50 or len(rows) - b < 50
            or a != candidate.metrics.train_n
            or b - a != candidate.metrics.calibration_n
            or len(rows) - b != candidate.metrics.test_n):
        raise TennisCalibrationShadowError("TENNIS_FROZEN_PARTITION_MISMATCH")
    cal_p = _raw_predictions(features[a:b], artifact, names)
    test_p = _raw_predictions(features[b:], artifact, names)
    cal_y, test_y = targets[a:b], targets[b:]
    test_raw_brier, test_raw_ll = _calibration_metrics(test_p, test_y)
    if (not isclose(test_raw_brier, candidate.metrics.raw_brier, abs_tol=1e-9, rel_tol=0)
            or not isclose(test_raw_ll, candidate.metrics.raw_log_loss, abs_tol=1e-9, rel_tol=0)):
        raise TennisCalibrationShadowError("TENNIS_FROZEN_RAW_REPLAY_MISMATCH")

    fit_n = max(50, int(len(cal_p) * 0.60))
    if len(cal_p) - fit_n < 20:
        raise TennisCalibrationShadowError("TENNIS_CALIBRATION_SELECTION_SAMPLE_INSUFFICIENT")
    inner_map = _fit_calibrator(cal_p[:fit_n], cal_y[:fit_n])
    raw_eval = cal_p[fit_n:]
    mapped_eval = _map_calibrator(raw_eval, inner_map)
    y_eval = cal_y[fit_n:]
    reference_brier, reference_ll = _calibration_metrics(raw_eval, y_eval)
    eligible: list[tuple[float, float]] = []
    trace = []
    for alpha in ALPHAS:
        blended = (1 - alpha) * raw_eval + alpha * mapped_eval
        brier, ll = _calibration_metrics(blended, y_eval)
        accepted = brier <= reference_brier + 1e-12 and ll <= reference_ll + 1e-12
        trace.append({"alpha": alpha, "selection_brier": brier,
                      "selection_log_loss": ll, "eligible": accepted})
        if accepted:
            eligible.append((brier / reference_brier + ll / reference_ll, alpha))
    if not eligible:
        raise TennisCalibrationShadowError("TENNIS_IDENTITY_SELECTION_MISSING")
    # Deterministic tie-break: prefer less correction when fit is indistinguishable.
    alpha = min(eligible)[1]
    full_map = _fit_calibrator(cal_p, cal_y) if alpha > 0 else None
    mapped_test = _map_calibrator(test_p, full_map) if full_map is not None else test_p
    predicted_test = (1 - alpha) * test_p + alpha * mapped_test
    test_brier, test_ll = _calibration_metrics(predicted_test, test_y)
    raw_baseline_brier = float(candidate.metrics.baseline_brier)
    raw_baseline_ll = float(candidate.metrics.baseline_log_loss)
    research_pass = (
        test_brier <= test_raw_brier + 1e-12
        and test_ll <= test_raw_ll + 1e-12
        and test_brier < raw_baseline_brier
        and test_ll <= raw_baseline_ll
    )
    receipt = {
        "contract": CONTRACT,
        "model_family": family,
        "frozen_training_dataset_hash": digest,
        "training_n": int(a),
        "calibration_n": int(b - a),
        "test_n": int(len(test_y)),
        "selected_alpha": alpha,
        "selection_fit_n": fit_n,
        "selection_eval_n": len(cal_p) - fit_n,
        "selection_trace": trace,
        "selection_used_untouched_test": False,
        "raw_test_brier": test_raw_brier,
        "shadow_test_brier": test_brier,
        "raw_test_log_loss": test_raw_ll,
        "shadow_test_log_loss": test_ll,
        "research_screen_pass": research_pass,
        "historical_reconstruction_only": True,
        "archived_pregame_receipt_verified": False,
        "certification_eligible": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    receipt["receipt_sha256"] = sha256(
        dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return receipt


__all__ = ["CAN_EXECUTE", "CONTRACT", "TennisCalibrationShadowError",
           "replay_tennis_calibration_shadow"]
