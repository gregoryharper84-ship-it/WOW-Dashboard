"""Phase-A calibration adapter for certified WNBA player-prop models.

Bounds come from a real moving-block bootstrap over the same aligned prior-game
stat/minutes evidence consumed by the fitted model. The effective sample size is
conservatively reduced for positive lag-1 autocorrelation. This adapter cannot
create money qualification, final approval, or execution authority.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Mapping

import numpy as np

from calibration import SHRINKAGE_K, phase_a_shrinkage
from prop_discrete_engine import (
    PropCalibrationOutput,
    PropCalibrationUnavailable,
    register_prop_calibration_adapter,
)
from prop_distribution_contract import LineProbabilities
from prop_fitted_provider import CertifiedInference
from wnba_prop_model_adapter import expected_count, feature_vector, poisson_pmf

CALIBRATOR_VERSION = "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1"
BOUNDS_METHOD_VERSION = "WNBA_PRECALIBRATION_MOVING_BLOCK_BOOTSTRAP_V1"
BOOTSTRAP_BLOCK_LENGTH = 3


def _ordered_history(features: Mapping[str, Any]) -> list[tuple[str, float, float]]:
    game_log = features.get("game_log")
    box_score_log = features.get("box_score_log")
    if not isinstance(game_log, list) or not isinstance(box_score_log, list):
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING",
            "WNBA calibration requires aligned game_log and box_score_log histories.",
        )
    if len(game_log) != len(box_score_log) or len(game_log) < 10:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_INSUFFICIENT",
            "WNBA calibration requires at least ten aligned prior games.",
        )
    ordered: list[tuple[str, float, float]] = []
    for i, (stat_value, box) in enumerate(zip(game_log, box_score_log)):
        if not isinstance(box, Mapping):
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_EVIDENCE_INVALID", "WNBA box_score_log rows must be objects."
            )
        try:
            game_date = str(box["date"])[:10]
            stat = float(stat_value)
            minutes = float(box["minutes"])
            date.fromisoformat(game_date)
        except (KeyError, TypeError, ValueError) as exc:
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_EVIDENCE_INVALID",
                f"WNBA prior-game row {i} lacks valid date/stat/minutes evidence.",
            ) from exc
        if not math.isfinite(stat) or stat < 0 or stat != int(stat):
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_EVIDENCE_INVALID", "WNBA stat history must be non-negative integer counts."
            )
        if not math.isfinite(minutes) or not 0 < minutes <= 60:
            raise PropCalibrationUnavailable(
                "PROP_CALIBRATION_EVIDENCE_INVALID", "WNBA minutes history must be within (0,60]."
            )
        ordered.append((game_date, stat, minutes))
    ordered.sort(key=lambda row: row[0])
    if len({row[0] for row in ordered}) != len(ordered):
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_INVALID", "WNBA calibration history contains duplicate game dates."
        )
    return ordered[-10:]


def effective_sample_size(history: list[tuple[str, float, float]]) -> float:
    values = np.asarray([row[1] for row in history], dtype=float)
    n = len(values)
    if n < 3 or float(np.var(values)) <= 1e-12:
        return float(n)
    left = values[:-1] - float(np.mean(values[:-1]))
    right = values[1:] - float(np.mean(values[1:]))
    denom = math.sqrt(float(np.dot(left, left)) * float(np.dot(right, right)))
    rho = float(np.dot(left, right) / denom) if denom > 0 else 0.0
    rho = min(max(rho, 0.0), 0.90)
    ess = n * (1.0 - rho) / (1.0 + rho)
    return min(float(n), max(1.0, float(ess)))


def _resample_history(
    rng: np.random.Generator,
    history: list[tuple[str, float, float]],
) -> Mapping[str, Any]:
    n = len(history)
    sampled: list[tuple[str, float, float]] = []
    while len(sampled) < n:
        start = int(rng.integers(0, n))
        for offset in range(BOOTSTRAP_BLOCK_LENGTH):
            sampled.append(history[(start + offset) % n])
            if len(sampled) == n:
                break
    synthetic_start = date(2026, 1, 1)
    return {
        "game_log": [row[1] for row in sampled],
        "box_score_log": [
            {
                "date": (synthetic_start + timedelta(days=i)).isoformat(),
                "minutes": row[2],
            }
            for i, row in enumerate(sampled)
        ],
    }


def _side_probability(
    inference: CertifiedInference,
    sample_features: Mapping[str, Any],
    *,
    line: float,
    direction_more: bool,
) -> float:
    vector = feature_vector(sample_features)
    mu, _ = expected_count(inference.artifact.artifact_payload, vector)
    max_support = int(inference.artifact.artifact_payload["max_support_k"])
    pmf = poisson_pmf(mu, max_support)
    if direction_more:
        return float(sum(p for outcome, p in pmf.items() if outcome > line))
    return float(sum(p for outcome, p in pmf.items() if outcome < line))


def wnba_precalibration_bootstrap_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    history = _ordered_history(features)
    n_eff = effective_sample_size(history)
    more_match = math.isclose(raw_probability, line_probs.probability_more, rel_tol=0.0, abs_tol=1e-12)
    less_match = math.isclose(raw_probability, line_probs.probability_less, rel_tol=0.0, abs_tol=1e-12)
    if more_match == less_match:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_DIRECTION_AMBIGUOUS",
            "Could not bind the selected WNBA side to the direction-free line probabilities.",
        )
    direction_more = more_match
    lam = n_eff / (n_eff + SHRINKAGE_K)

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        results = np.empty(count, dtype=float)
        for i in range(count):
            sample = _resample_history(rng, history)
            p_side = _side_probability(
                inference,
                sample,
                line=line_probs.line,
                direction_more=direction_more,
            )
            results[i] = 0.5 + lam * (p_side - 0.5)
        return results

    try:
        result = phase_a_shrinkage(
            p_raw=raw_probability,
            n_eff=n_eff,
            rng_seed=seed,
            resample_fn=resample_fn,
        )
    except Exception as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_BOOTSTRAP_FAILED",
            f"WNBA Phase-A evidence bootstrap failed: {exc}",
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


def register() -> None:
    register_prop_calibration_adapter(CALIBRATOR_VERSION, wnba_precalibration_bootstrap_adapter)
