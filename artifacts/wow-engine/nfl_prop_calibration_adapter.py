"""Phase-A governed calibration/bounds for certified NFL player-prop artifacts.

The exact fitted artifact produces the raw sporting distribution. Calibration
bootstraps only the immutable pregame player history, reruns that same fitted
artifact, and applies the repository's governed phase-A shrinkage. It never
uses market odds or narrative judgment.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from calibration import SHRINKAGE_K, phase_a_shrinkage
from nfl_prop_model_adapter import _vector_from_pairs, distribution_for_vector, history_pairs
from prop_discrete_engine import PropCalibrationOutput, PropCalibrationUnavailable
from prop_distribution_contract import LineProbabilities
from prop_fitted_provider import CertifiedInference

CALIBRATOR_VERSION = "NFL_PROP_PRECALIBRATION_BOOTSTRAP_V1"
BOUNDS_METHOD_VERSION = "NFL_PROP_EVIDENCE_BOOTSTRAP_SHRINKAGE_V1"


def _side_probability(support: Mapping[int, float], *, line: float, more: bool) -> float:
    if more:
        return float(sum(p for k, p in support.items() if float(k) > line))
    return float(sum(p for k, p in support.items() if float(k) < line))


def nfl_prop_precalibration_bootstrap_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    try:
        pairs = history_pairs(features)
    except Exception as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING", f"NFL prop calibration history invalid: {exc}"
        ) from exc
    n_eff = float(len(pairs))
    if n_eff < 10:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING", "NFL prop calibration requires ten prior games"
        )
    # Direction is derived only from the fitted PMF result already produced by
    # the model package, never from the caller's prose.
    more = math.isclose(raw_probability, line_probs.probability_more, rel_tol=0.0, abs_tol=1e-10)
    payload = inference.artifact.artifact_payload
    lam = n_eff / (n_eff + SHRINKAGE_K)

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        output = np.empty(count, dtype=float)
        n = len(pairs)
        for i in range(count):
            idx = rng.integers(0, n, size=n)
            sampled = [pairs[int(j)] for j in idx]
            vector = _vector_from_pairs(sampled)
            support, _ = distribution_for_vector(payload, vector)
            side_raw = _side_probability(support, line=line_probs.line, more=more)
            output[i] = 0.5 + lam * (side_raw - 0.5)
        return output

    try:
        result = phase_a_shrinkage(
            p_raw=float(raw_probability),
            n_eff=n_eff,
            rng_seed=int(seed),
            resample_fn=resample_fn,
        )
    except Exception as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_BOOTSTRAP_FAILED", f"NFL Phase-A bootstrap failed: {exc}"
        ) from exc

    return PropCalibrationOutput(
        calibration_status=result.calibration_status,
        calibration_method=result.calibration_method,
        calibrated_probability=result.calibrated_probability,
        lower_bound=result.lower_bound,
        upper_bound=result.upper_bound,
        bounds_method_version=BOUNDS_METHOD_VERSION,
        effective_sample_size=n_eff,
    )


__all__ = [
    "CALIBRATOR_VERSION",
    "BOUNDS_METHOD_VERSION",
    "nfl_prop_precalibration_bootstrap_adapter",
]
