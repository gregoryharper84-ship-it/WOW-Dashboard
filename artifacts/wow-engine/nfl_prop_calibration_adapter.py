"""Phase-A evidence-bootstrap calibration for certified NFL direct props."""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from calibration import SHRINKAGE_K, phase_a_shrinkage
from nfl_prop_model_adapter import YARD_MODEL_FAMILY, TD_MODEL_FAMILY, feature_vector, _standardized_prediction, _yard_support
from prop_discrete_engine import PropCalibrationOutput, PropCalibrationUnavailable, register_prop_calibration_adapter
from prop_distribution_contract import LineProbabilities
from prop_fitted_provider import CertifiedInference

CALIBRATOR_VERSION = "NFL_DIRECT_PROP_PRECALIBRATION_V1"
BOUNDS_METHOD_VERSION = "NFL_DIRECT_PROP_PRECALIBRATION_EVIDENCE_BOOTSTRAP_V1"


def _side_probability(inference: CertifiedInference, route: str, game_log: list[float], box_score_log: list[dict[str, Any]], line: float, more: bool) -> float:
    x = feature_vector(route, game_log, box_score_log)
    score = _standardized_prediction(inference.artifact.artifact_payload, x)
    if inference.artifact.model_family == YARD_MODEL_FAMILY:
        support = _yard_support(inference.artifact.artifact_payload, max(score, 0.0))
    elif inference.artifact.model_family == TD_MODEL_FAMILY:
        p = 1.0 / (1.0 + math.exp(-max(min(score, 35.0), -35.0)))
        support = {0: 1.0 - p, 1: p}
    else:
        raise PropCalibrationUnavailable("NFL_PROP_CALIBRATOR_MODEL_FAMILY_UNSUPPORTED", inference.artifact.model_family)
    if more:
        return float(sum(p for k, p in support.items() if k > line))
    return float(sum(p for k, p in support.items() if k < line))


def nfl_direct_prop_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    raw_log = features.get("game_log")
    raw_box = features.get("box_score_log")
    if not isinstance(raw_log, list) or not isinstance(raw_box, list) or len(raw_log) < 10 or len(raw_box) < 10:
        raise PropCalibrationUnavailable("PROP_CALIBRATION_EVIDENCE_MISSING", "NFL direct-prop Phase-A calibration requires at least ten aligned prior games")
    game_log = [float(v) for v in raw_log[-10:]]
    box_score_log = [dict(v) for v in raw_box[-10:]]
    n = min(len(game_log), len(box_score_log))
    n_eff = float(n)
    route = inference.artifact.bundle.supported_stat_type.upper()
    direction_more = math.isclose(raw_probability, line_probs.probability_more, abs_tol=1e-9)
    lam = n_eff / (n_eff + SHRINKAGE_K)

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        result = np.empty(count, dtype=float)
        for i in range(count):
            idx = rng.integers(0, n, size=n)
            sample_log = [game_log[j] for j in idx]
            sample_box = [box_score_log[j] for j in idx]
            p_raw = _side_probability(inference, route, sample_log, sample_box, line_probs.line, direction_more)
            result[i] = 0.5 + lam * (p_raw - 0.5)
        return result

    try:
        calibrated = phase_a_shrinkage(
            p_raw=float(raw_probability), n_eff=n_eff, rng_seed=int(seed), resample_fn=resample_fn,
        )
    except Exception as exc:
        raise PropCalibrationUnavailable("PROP_CALIBRATION_BOOTSTRAP_FAILED", f"NFL direct-prop bootstrap failed: {exc}") from exc
    return PropCalibrationOutput(
        calibration_status=calibrated.calibration_status,
        calibration_method=calibrated.calibration_method,
        calibrated_probability=calibrated.calibrated_probability,
        lower_bound=calibrated.lower_bound,
        upper_bound=calibrated.upper_bound,
        bounds_method_version=BOUNDS_METHOD_VERSION,
        effective_sample_size=n_eff,
    )


def register() -> None:
    register_prop_calibration_adapter(CALIBRATOR_VERSION, nfl_direct_prop_precalibration_adapter)
