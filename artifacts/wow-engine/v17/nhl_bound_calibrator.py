"""NHL Design A Fix B — certification-bound, bound-producing calibrator.

Builds ``EMPIRICAL_WILSON_BINS_V1`` from a chronological calibration block with
documented pre/post proper scores, calibration bias, and lower-bound
reliability. Identity/raw calibrators never clear G3. This module does not
promote, publish, certify, mutate candidate rows, or enable execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np

from v17 import binary_candidate_lifecycle as binary
from v17.binary_candidate_lifecycle import (
    BinaryCandidateError,
    CALIBRATOR_SELECTION_VERSION,
    EMPIRICAL_CALIBRATION_METHOD,
    IDENTITY_CALIBRATION_METHOD,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
CERTIFICATION_CALIBRATOR_VERSION = "NHL_CERTIFICATION_BOUND_CALIBRATOR_V1"
MIN_CALIBRATION_N = 50
MIN_SELECTION_EVAL_N = 20


class NHLBoundCalibratorError(RuntimeError):
    def __init__(self, code: str, detail: Any = None):
        self.code = str(code)
        self.detail = detail
        super().__init__(f"{self.code}:{detail}" if detail is not None else self.code)


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _finite_unit_interval(values: Sequence[Any], *, label: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or len(arr) == 0:
        raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_INPUT_INVALID", label)
    if not np.all(np.isfinite(arr)):
        raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_INPUT_NONFINITE", label)
    if np.any((arr <= 0.0) | (arr >= 1.0)):
        # Allow exact 0/1 only after clip at map time; raw inputs must be open.
        if np.any((arr < 0.0) | (arr > 1.0)):
            raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_INPUT_OUT_OF_RANGE", label)
    return arr


def _binary_outcomes(values: Sequence[Any]) -> np.ndarray:
    arr = np.asarray(values)
    if arr.ndim != 1 or len(arr) == 0:
        raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_OUTCOME_INVALID", "empty")
    out: list[int] = []
    for item in arr.tolist():
        if isinstance(item, bool):
            out.append(1 if item else 0)
            continue
        if type(item) is int and item in (0, 1):
            out.append(item)
            continue
        raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_OUTCOME_INVALID", type(item).__name__)
    result = np.asarray(out, dtype=int)
    if len(np.unique(result)) < 2:
        raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_CLASS_DEGENERATE")
    return result


def _map_bounds(probabilities: np.ndarray, calibrator: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    bins = list(calibrator.get("bins") or [])
    if not bins:
        raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_BINS_MISSING")
    centers = np.asarray([float(row["raw_mean"]) for row in bins], dtype=float)
    calibrated: list[float] = []
    lowers: list[float] = []
    uppers: list[float] = []
    for value in probabilities:
        idx = int(np.argmin(np.abs(centers - float(value))))
        row = bins[idx]
        calibrated.append(float(row["calibrated_probability"]))
        lowers.append(float(row["wilson_lower"]))
        uppers.append(float(row["wilson_upper"]))
    return (
        np.clip(np.asarray(calibrated, dtype=float), 1e-6, 1.0 - 1e-6),
        np.clip(np.asarray(lowers, dtype=float), 1e-6, 1.0 - 1e-6),
        np.clip(np.asarray(uppers, dtype=float), 1e-6, 1.0 - 1e-6),
    )


def _quality_metrics(
    *,
    raw: np.ndarray,
    calibrated: np.ndarray,
    lowers: np.ndarray,
    outcomes: np.ndarray,
) -> dict[str, float]:
    pre_brier, pre_ll = binary._calibration_metrics(raw, outcomes)
    post_brier, post_ll = binary._calibration_metrics(calibrated, outcomes)
    bias = float(np.mean(calibrated - outcomes))
    # Positive gap => outcomes exceed lower bounds on average (conservative).
    lower_gap = float(np.mean(outcomes - lowers))
    return {
        "pre_brier": float(pre_brier),
        "post_brier": float(post_brier),
        "pre_log_loss": float(pre_ll),
        "post_log_loss": float(post_ll),
        "calibration_bias": bias,
        "lower_bound_reliability_gap": lower_gap,
    }


def build_nhl_certification_bound_calibrator(
    raw_probabilities: Sequence[Any],
    outcomes: Sequence[Any],
    *,
    fit_end_timestamp: str | datetime | None = None,
) -> dict[str, Any]:
    """Fit a non-identity Wilson-bin calibrator for NHL certification binding.

    Uses an earlier chronological fit sub-block and a later evaluation
    sub-block for quality metrics, then refits bins on the full calibration
    block. Identity is never returned as certification-ready.
    """
    probs = _finite_unit_interval(raw_probabilities, label="raw_probabilities")
    y = _binary_outcomes(outcomes)
    if len(probs) != len(y):
        raise NHLBoundCalibratorError(
            "NHL_BOUND_CALIBRATOR_LENGTH_MISMATCH",
            {"probabilities": len(probs), "outcomes": len(y)},
        )
    n = int(len(probs))
    if n < MIN_CALIBRATION_N:
        raise NHLBoundCalibratorError(
            "NHL_BOUND_CALIBRATOR_SAMPLE_INSUFFICIENT", str(n)
        )

    fit_n = max(MIN_CALIBRATION_N, int(n * 0.60))
    eval_n = n - fit_n
    if eval_n < MIN_SELECTION_EVAL_N:
        fit_n = n - MIN_SELECTION_EVAL_N
        eval_n = MIN_SELECTION_EVAL_N
    if fit_n < MIN_CALIBRATION_N:
        raise NHLBoundCalibratorError(
            "NHL_BOUND_CALIBRATOR_FORWARD_SPLIT_INSUFFICIENT",
            {"fit_n": fit_n, "evaluation_n": eval_n},
        )

    fit_p, fit_y = probs[:fit_n], y[:fit_n]
    eval_p, eval_y = probs[fit_n:], y[fit_n:]

    try:
        selection_fit = binary._fit_calibrator(fit_p, fit_y)
        full_fit = binary._fit_calibrator(probs, y)
    except BinaryCandidateError as exc:
        raise NHLBoundCalibratorError(exc.code, str(exc)) from exc

    cal_eval, lower_eval, upper_eval = _map_bounds(eval_p, selection_fit)
    # Bound order invariant on the evaluation mapping.
    if not np.all((lower_eval <= cal_eval) & (cal_eval <= upper_eval)):
        raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_BOUNDS_ORDER_INVALID")

    metrics = _quality_metrics(
        raw=eval_p,
        calibrated=cal_eval,
        lowers=lower_eval,
        outcomes=eval_y,
    )
    identity_brier, identity_ll = binary._calibration_metrics(eval_p, eval_y)

    if str(full_fit.get("method") or "") == IDENTITY_CALIBRATION_METHOD:
        return {
            "status": "BLOCKED",
            "code": "NHL_IDENTITY_CALIBRATOR_FORBIDDEN",
            "certification_calibrator_ready": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "can_execute": CAN_EXECUTE,
            "disposition": "WORKING_NOT_COMPLETE",
        }

    if not isinstance(full_fit.get("bins"), list) or not full_fit["bins"]:
        return {
            "status": "BLOCKED",
            "code": "NHL_BOUND_CALIBRATOR_BINS_MISSING",
            "certification_calibrator_ready": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "can_execute": CAN_EXECUTE,
            "disposition": "WORKING_NOT_COMPLETE",
        }

    fit_end = fit_end_timestamp
    if fit_end is None:
        fit_end = datetime.now(timezone.utc).isoformat()
    elif isinstance(fit_end, datetime):
        if fit_end.tzinfo is None:
            raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_FIT_END_TIMEZONE_REQUIRED")
        fit_end = fit_end.isoformat()
    else:
        fit_end = str(fit_end).strip()
        if not fit_end:
            raise NHLBoundCalibratorError("NHL_BOUND_CALIBRATOR_FIT_END_INVALID")

    payload = {
        "method": EMPIRICAL_CALIBRATION_METHOD,
        "training_n": int(full_fit["training_n"]),
        "binning": str(full_fit.get("binning") or "CHRONOLOGICAL_CALIBRATION_BLOCK_EQUAL_COUNT"),
        "bins": list(full_fit["bins"]),
        "selection_version": CALIBRATOR_SELECTION_VERSION,
        "selection": {
            "decision": "EMPIRICAL_WILSON_CERTIFICATION_BOUND",
            "reason": "NHL_CERTIFICATION_REQUIRES_BOUND_PRODUCING_CALIBRATOR",
            "fit_n": int(fit_n),
            "evaluation_n": int(eval_n),
            "identity_brier": float(identity_brier),
            "identity_log_loss": float(identity_ll),
            "empirical_brier": float(metrics["post_brier"]),
            "empirical_log_loss": float(metrics["post_log_loss"]),
            "test_block_used_for_selection": False,
            "research_forward_selection_overridden": True,
        },
        "certification_calibrator_version": CERTIFICATION_CALIBRATOR_VERSION,
        "fit_end_timestamp": fit_end,
        "chronological_quality": {
            "evaluation_n": int(eval_n),
            **metrics,
        },
        "can_execute": CAN_EXECUTE,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
    }
    fingerprint = _sha256(payload)
    return {
        "status": "NHL_CERTIFICATION_BOUND_CALIBRATOR_READY",
        "certification_calibrator_ready": True,
        "calibration_method": EMPIRICAL_CALIBRATION_METHOD,
        "calibration_artifact_fingerprint": fingerprint,
        "calibrator_payload": payload,
        "fit_end_timestamp": fit_end,
        "chronological_quality": dict(payload["chronological_quality"]),
        "identity_calibrator_forbidden": True,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
        "disposition": "WORKING_NOT_COMPLETE",
    }


def assess_nhl_certification_bound_calibrator(
    calibrator_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fail-closed G3 assessment of an existing calibrator payload."""
    if not isinstance(calibrator_payload, Mapping):
        return {
            "status": "BLOCKED",
            "code": "NHL_BOUND_CALIBRATOR_PAYLOAD_MISSING",
            "g3_ready": False,
            "certification_calibrator_ready": False,
            "can_execute": CAN_EXECUTE,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "disposition": "WORKING_NOT_COMPLETE",
        }

    method = str(calibrator_payload.get("method") or "")
    if method == IDENTITY_CALIBRATION_METHOD or method == "":
        return {
            "status": "BLOCKED",
            "code": "NHL_IDENTITY_CALIBRATOR_FORBIDDEN",
            "g3_ready": False,
            "certification_calibrator_ready": False,
            "calibration_method": method or None,
            "can_execute": CAN_EXECUTE,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "disposition": "WORKING_NOT_COMPLETE",
        }
    if method != EMPIRICAL_CALIBRATION_METHOD:
        return {
            "status": "BLOCKED",
            "code": "NHL_BOUND_CALIBRATOR_METHOD_UNSUPPORTED",
            "g3_ready": False,
            "certification_calibrator_ready": False,
            "calibration_method": method,
            "can_execute": CAN_EXECUTE,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "disposition": "WORKING_NOT_COMPLETE",
        }

    bins = calibrator_payload.get("bins")
    if not isinstance(bins, list) or not bins:
        return {
            "status": "BLOCKED",
            "code": "NHL_BOUND_CALIBRATOR_BINS_MISSING",
            "g3_ready": False,
            "certification_calibrator_ready": False,
            "can_execute": CAN_EXECUTE,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "disposition": "WORKING_NOT_COMPLETE",
        }

    for entry in bins:
        if not isinstance(entry, Mapping):
            return {
                "status": "BLOCKED",
                "code": "NHL_BOUND_CALIBRATOR_BINS_INVALID",
                "g3_ready": False,
                "certification_calibrator_ready": False,
                "can_execute": CAN_EXECUTE,
                "probability_publishable": PROBABILITY_PUBLISHABLE,
                "disposition": "WORKING_NOT_COMPLETE",
            }
        try:
            calibrated = float(entry["calibrated_probability"])
            lower = float(entry["wilson_lower"])
            upper = float(entry["wilson_upper"])
        except (KeyError, TypeError, ValueError):
            return {
                "status": "BLOCKED",
                "code": "NHL_BOUND_CALIBRATOR_BINS_INVALID",
                "g3_ready": False,
                "certification_calibrator_ready": False,
                "can_execute": CAN_EXECUTE,
                "probability_publishable": PROBABILITY_PUBLISHABLE,
                "disposition": "WORKING_NOT_COMPLETE",
            }
        if not (
            math.isfinite(calibrated)
            and math.isfinite(lower)
            and math.isfinite(upper)
            and 0.0 < lower <= calibrated <= upper < 1.0
        ):
            return {
                "status": "BLOCKED",
                "code": "NHL_BOUND_CALIBRATOR_BOUNDS_ORDER_INVALID",
                "g3_ready": False,
                "certification_calibrator_ready": False,
                "can_execute": CAN_EXECUTE,
                "probability_publishable": PROBABILITY_PUBLISHABLE,
                "disposition": "WORKING_NOT_COMPLETE",
            }

    quality = calibrator_payload.get("chronological_quality")
    required_quality = (
        "pre_brier",
        "post_brier",
        "pre_log_loss",
        "post_log_loss",
        "calibration_bias",
        "lower_bound_reliability_gap",
        "evaluation_n",
    )
    if not isinstance(quality, Mapping) or any(
        not isinstance(quality.get(name), (int, float)) for name in required_quality
    ):
        return {
            "status": "BLOCKED",
            "code": "NHL_BOUND_CALIBRATOR_QUALITY_METRICS_MISSING",
            "g3_ready": False,
            "certification_calibrator_ready": False,
            "can_execute": CAN_EXECUTE,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "disposition": "WORKING_NOT_COMPLETE",
        }

    fit_end = str(calibrator_payload.get("fit_end_timestamp") or "").strip()
    if not fit_end:
        return {
            "status": "BLOCKED",
            "code": "NHL_BOUND_CALIBRATOR_FIT_END_INVALID",
            "g3_ready": False,
            "certification_calibrator_ready": False,
            "can_execute": CAN_EXECUTE,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "disposition": "WORKING_NOT_COMPLETE",
        }

    fingerprint = _sha256(dict(calibrator_payload))
    return {
        "status": "G3_BOUND_CALIBRATOR_READY",
        "g3_ready": True,
        "certification_calibrator_ready": True,
        "calibration_method": EMPIRICAL_CALIBRATION_METHOD,
        "calibration_artifact_fingerprint": fingerprint,
        "fit_end_timestamp": fit_end,
        "chronological_quality": dict(quality),
        "identity_calibrator_forbidden": True,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
        "disposition": "WORKING_NOT_COMPLETE",
    }


__all__ = [
    "CAN_EXECUTE",
    "CERTIFICATION_CALIBRATOR_VERSION",
    "EMPIRICAL_CALIBRATION_METHOD",
    "IDENTITY_CALIBRATION_METHOD",
    "NHLBoundCalibratorError",
    "PROBABILITY_PUBLISHABLE",
    "assess_nhl_certification_bound_calibrator",
    "build_nhl_certification_bound_calibrator",
]
