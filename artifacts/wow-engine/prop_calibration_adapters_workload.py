"""Phase-A calibration adapters for promoted MLB workload and PA prop models.

These adapters do not invent a second model. Each bootstrap realization
resamples the exact historical evidence used by the certified fitted-model
adapter, invokes that same reviewed adapter, derives the exact-line side
probability from its direction-free PMF, and applies the same conservative
Phase-A shrinkage transform as the point estimate.

Current-game context (for example PA batting slot/team alignment) is held fixed
across bootstrap realizations because it describes tonight's event rather than
historical sampling uncertainty. No market probability enters this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import numpy as np

from calibration import SHRINKAGE_K, phase_a_shrinkage
from prop_discrete_engine import (
    PropCalibrationOutput,
    PropCalibrationUnavailable,
    register_prop_calibration_adapter,
)
from prop_distribution_contract import LineProbabilities, derive_line_probabilities
from prop_fitted_provider import CertifiedInference, ResolvedArtifact
from prop_model_adapters_pitching_outs import mlb_pitcher_outs_workload_nb_v1_adapter
from prop_model_adapters_pitch_composition import (
    mlb_pitcher_balls_thrown_workload_nb_v1_adapter,
    mlb_pitcher_strikes_thrown_workload_nb_v1_adapter,
)
from prop_model_adapters_plate_appearances import mlb_batter_plate_appearances_nb_v1_adapter


MLB_PITCHER_OUTS_CALIBRATOR_VERSION = "MLB_PITCHER_OUTS_CAL_V1"
MLB_PITCHER_STRIKES_THROWN_CALIBRATOR_VERSION = "MLB_PITCHER_STRIKES_THROWN_CAL_V1"
MLB_PITCHER_BALLS_THROWN_CALIBRATOR_VERSION = "MLB_PITCHER_BALLS_THROWN_CAL_V1"
MLB_BATTER_PA_CALIBRATOR_VERSION = "MLB_BATTER_PA_CAL_V1"
BOUNDS_METHOD_VERSION = "PRECALIBRATION_SHRINKAGE_EVIDENCE_BOOTSTRAP_V1"

ModelAdapter = Callable[[ResolvedArtifact, Any, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class _BootstrapRequestIdentity:
    """Only the request field consumed by these four fitted adapters.

    The request identity cannot alter their PMF math; it is included solely in
    the adapter's feature-snapshot audit hash. Reusing the certified point
    inference's feature hash binds each bootstrap realization to that inference
    without fabricating an event/player/market identity.
    """

    evidence_snapshot_id: str


def _selected_side_probability(
    *,
    raw_probability: float,
    line_probs: LineProbabilities,
    resampled_line_probs: LineProbabilities,
) -> float:
    direction_more = math.isclose(
        raw_probability,
        line_probs.probability_more,
        rel_tol=0.0,
        abs_tol=1e-9,
    )
    return (
        resampled_line_probs.probability_more
        if direction_more
        else resampled_line_probs.probability_less
    )


def _aligned_history(features: Mapping[str, Any]) -> tuple[list[Any], list[Any]]:
    game_log = features.get("game_log")
    box_score_log = features.get("box_score_log")
    if not isinstance(game_log, list) or not isinstance(box_score_log, list):
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING",
            "calibration requires aligned game_log and box_score_log histories",
        )
    if not game_log or len(game_log) != len(box_score_log):
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISALIGNED",
            "calibration requires non-empty 1:1 game_log/box_score_log histories",
        )
    return game_log, box_score_log


def _bootstrap_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
    *,
    model_adapter: ModelAdapter,
) -> PropCalibrationOutput:
    game_log, box_score_log = _aligned_history(features)
    n_eff = float(len(game_log))
    lam = n_eff / (n_eff + SHRINKAGE_K)
    bootstrap_request = _BootstrapRequestIdentity(
        evidence_snapshot_id=inference.distribution.feature_snapshot_hash
    )

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        values = np.empty(count, dtype=float)
        n = len(game_log)
        for i in range(count):
            indices = rng.integers(0, n, size=n)
            sampled_features = dict(features)
            # The persisted evidence contract declares these histories 1:1.
            # Resample aligned rows together so no synthetic mismatch is made.
            sampled_features["game_log"] = [game_log[j] for j in indices]
            sampled_features["box_score_log"] = [box_score_log[j] for j in indices]

            distribution = model_adapter(
                inference.artifact,
                bootstrap_request,
                sampled_features,
            )
            if not distribution.coverage.in_distribution:
                raise PropCalibrationUnavailable(
                    "PROP_CALIBRATION_BOOTSTRAP_OOD",
                    "A resampled realization left the certified model coverage envelope.",
                )
            resampled_line = derive_line_probabilities(distribution, line_probs.line)
            p_side = _selected_side_probability(
                raw_probability=raw_probability,
                line_probs=line_probs,
                resampled_line_probs=resampled_line,
            )
            values[i] = 0.5 + lam * (p_side - 0.5)
        return values

    try:
        result = phase_a_shrinkage(
            p_raw=raw_probability,
            n_eff=n_eff,
            rng_seed=seed,
            resample_fn=resample_fn,
        )
    except PropCalibrationUnavailable:
        raise
    except Exception as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_BOOTSTRAP_FAILED",
            f"Phase A evidence bootstrap calibration failed: {exc}",
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


def mlb_pitcher_outs_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    return _bootstrap_adapter(
        inference,
        raw_probability,
        line_probs,
        features,
        seed,
        model_adapter=mlb_pitcher_outs_workload_nb_v1_adapter,
    )


def mlb_pitcher_strikes_thrown_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    return _bootstrap_adapter(
        inference,
        raw_probability,
        line_probs,
        features,
        seed,
        model_adapter=mlb_pitcher_strikes_thrown_workload_nb_v1_adapter,
    )


def mlb_pitcher_balls_thrown_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    return _bootstrap_adapter(
        inference,
        raw_probability,
        line_probs,
        features,
        seed,
        model_adapter=mlb_pitcher_balls_thrown_workload_nb_v1_adapter,
    )


def mlb_batter_pa_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    return _bootstrap_adapter(
        inference,
        raw_probability,
        line_probs,
        features,
        seed,
        model_adapter=mlb_batter_plate_appearances_nb_v1_adapter,
    )


def register() -> None:
    """Register only calibrator versions already promoted in the artifact registry."""
    register_prop_calibration_adapter(
        MLB_PITCHER_OUTS_CALIBRATOR_VERSION,
        mlb_pitcher_outs_precalibration_adapter,
    )
    register_prop_calibration_adapter(
        MLB_PITCHER_STRIKES_THROWN_CALIBRATOR_VERSION,
        mlb_pitcher_strikes_thrown_precalibration_adapter,
    )
    register_prop_calibration_adapter(
        MLB_PITCHER_BALLS_THROWN_CALIBRATOR_VERSION,
        mlb_pitcher_balls_thrown_precalibration_adapter,
    )
    register_prop_calibration_adapter(
        MLB_BATTER_PA_CALIBRATOR_VERSION,
        mlb_batter_pa_precalibration_adapter,
    )
