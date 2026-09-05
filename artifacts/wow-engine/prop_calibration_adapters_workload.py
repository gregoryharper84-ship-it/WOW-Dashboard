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
            "workload calibration requires aligned game_log and box_score_log histories",
        )
    if not game_log or len(game_log) != len(box_score_log):
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISALIGNED",
            "workload calibration requires non-empty 1:1 game_log/box_score_log histories",
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
    history_mode: str,
) -> PropCalibrationOutput:
    game_log, box_score_log = _aligned_history(features)
    n_eff = float(len(game_log))
    lam = n_eff / (n_eff + SHRINKAGE_K)

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        values = np.empty(count, dtype=float)
        n = len(game_log)
        for i in range(count):
            indices = rng.integers(0, n, size=n)
            sampled_features = dict(features)

            if history_mode == "ALIGNED":
                sampled_features["game_log"] = [game_log[j] for j in indices]
                sampled_features["box_score_log"] = [box_score_log[j] for j in indices]
            elif history_mode == "GAME_LOG_ONLY":
                sampled_features["game_log"] = [game_log[j] for j in indices]
                # PA's fitted model uses the PA history plus current lineup
                # context. Keep box_score_log available for audit identity but
                # do not let its contents drive the fitted distribution.
                sampled_features["box_score_log"] = [box_score_log[j] for j in indices]
            else:  # code-controlled invariant, never caller-selected
                raise RuntimeError(f"unsupported calibration history mode: {history_mode}")

            distribution = model_adapter(
                inference.artifact,
                inference_request := getattr(inference, "request", None),
                sampled_features,
            ) if getattr(inference, "request", None) is not None else model_adapter(
                inference.artifact,
                _request_from_distribution_context(inference),
                sampled_features,
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


def _request_from_distribution_context(inference: CertifiedInference):
    """Calibration must re-run the exact fitted adapter with stable identity.

    CertifiedInference intentionally carries only the resolved artifact and raw
    distribution. Adapter math uses request identity only to bind the feature
    snapshot hash, not to change the PMF. A minimal immutable request with the
    already-certified bundle identity is therefore constructed solely for
    bootstrap hash provenance; no caller-controlled model field is introduced.
    """
    from prop_distribution_contract import PropInferenceRequest

    artifact = inference.artifact
    bundle = artifact.bundle
    return PropInferenceRequest(
        event_id="CALIBRATION_BOOTSTRAP",
        player_id="CALIBRATION_BOOTSTRAP",
        sport=bundle.supported_sport,
        league_season="CALIBRATION_BOOTSTRAP",
        stat_type=bundle.supported_stat_type,
        evidence_snapshot_id=inference.distribution.feature_snapshot_hash,
        market_identity_id="CALIBRATION_BOOTSTRAP",
        as_of_timestamp=inference.distribution.inference_timestamp,
        request_id="CALIBRATION_BOOTSTRAP",
        feature_schema_version=bundle.feature_schema_version,
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
        history_mode="ALIGNED",
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
        history_mode="ALIGNED",
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
        history_mode="ALIGNED",
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
        history_mode="GAME_LOG_ONLY",
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
