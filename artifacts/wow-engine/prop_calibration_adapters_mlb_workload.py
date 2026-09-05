"""Phase-A calibration adapters for certified MLB workload / opportunity props.

These adapters deliberately reuse the same fitted artifact constants and the
same model equations as the corresponding raw-PMF adapters.  The bootstrap
resamples only the candidate's own historical evidence; current-game context
(e.g. batting slot/alignment) remains fixed.  Every realization is converted
to the requested side probability and then through the same Phase-A shrinkage
transform as the point estimate.

This is not a generic confidence haircut and it does not use sportsbook odds.
It exists only for the immutable calibrator versions named by the certified
artifacts below.  Phase A remains MODEL-only: MONEY_QUALIFIED and
FINAL_APPROVED remain prohibited by calibration.py / terminal governance.
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
from prop_distribution_contract import LineProbabilities, mix_failure_paths
from prop_fitted_provider import CertifiedInference
from prop_model_adapters import nb_pmf, shrink, _parse_box_score_log
from prop_model_adapters_pitch_composition import _parse_pitch_composition_log
from prop_model_adapters_plate_appearances import _parse_prior_pa_log

OUTS_CALIBRATOR_VERSION = "MLB_PITCHER_OUTS_CAL_V1"
STRIKES_CALIBRATOR_VERSION = "MLB_PITCHER_STRIKES_THROWN_CAL_V1"
BALLS_CALIBRATOR_VERSION = "MLB_PITCHER_BALLS_THROWN_CAL_V1"
PA_CALIBRATOR_VERSION = "MLB_BATTER_PA_CAL_V1"

OUTS_MODEL_FAMILY = "MLB_PITCHER_OUTS_WORKLOAD_NB_V1"
STRIKES_MODEL_FAMILY = "MLB_PITCHER_STRIKES_THROWN_WORKLOAD_NB_V1"
BALLS_MODEL_FAMILY = "MLB_PITCHER_BALLS_THROWN_WORKLOAD_NB_V1"
PA_MODEL_FAMILY = "MLB_BATTER_PLATE_APPEARANCES_NB_V1"

BOUNDS_METHOD_VERSION = "PRECALIBRATION_SHRINKAGE_EVIDENCE_BOOTSTRAP_V1"


def _direction_more(raw_probability: float, line_probs: LineProbabilities) -> bool:
    if math.isclose(raw_probability, line_probs.probability_more, rel_tol=0.0, abs_tol=1e-9):
        return True
    if math.isclose(raw_probability, line_probs.probability_less, rel_tol=0.0, abs_tol=1e-9):
        return False
    raise PropCalibrationUnavailable(
        "PROP_CALIBRATION_DIRECTION_MISMATCH",
        "Raw probability does not match either side of the direction-free PMF.",
    )


def _side_probability(support: Mapping[int, float], line: float, more: bool) -> float:
    if more:
        return float(sum(float(p) for k, p in support.items() if k > line))
    return float(sum(float(p) for k, p in support.items() if k < line))


def _phase_a_output(
    *,
    raw_probability: float,
    n_eff: float,
    seed: int,
    resample_fn: Callable[[np.random.Generator, int], np.ndarray],
) -> PropCalibrationOutput:
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
            f"Phase A evidence bootstrap failed: {exc}",
        ) from exc
    return PropCalibrationOutput(
        calibration_status=result.calibration_status,
        calibration_method=result.calibration_method,
        calibrated_probability=result.calibrated_probability,
        lower_bound=result.lower_bound,
        upper_bound=result.upper_bound,
        bounds_method_version=BOUNDS_METHOD_VERSION,
        effective_sample_size=float(n_eff),
    )


def _require_family(inference: CertifiedInference, expected: str) -> None:
    actual = str(inference.artifact.model_family or "").strip().upper()
    if actual != expected:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATOR_MODEL_FAMILY_MISMATCH",
            f"Calibration adapter for {expected} cannot calibrate artifact family {actual!r}.",
        )


def _outs_resample_fn(
    inference: CertifiedInference,
    line: float,
    more: bool,
    features: Mapping[str, Any],
    n_eff: float,
):
    payload = inference.artifact.artifact_payload
    try:
        league_normal = float(payload["league_mean_out_normal"])
        league_short = float(payload["league_mean_out_short"])
        league_shortened_rate = float(payload["league_shortened_rate"])
        dispersion_r = float(payload["dispersion_r"])
        threshold = float(payload["shortened_outs_threshold"])
        k_rate = float(payload["shrinkage_k_rate"])
        k_regime = float(payload["shrinkage_k_regime"])
        max_support = int(payload["max_support_k"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_ARTIFACT_INVALID",
            "Pitching-outs calibration artifact is missing fitted constants.",
        ) from exc
    parsed = _parse_box_score_log(features.get("box_score_log"))
    outs = [float(entry.outs) for entry in parsed]
    if not outs:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING",
            "Pitching-outs calibration requires non-empty box_score_log evidence.",
        )
    n = len(outs)
    lam = n_eff / (n_eff + SHRINKAGE_K)

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        values = np.empty(count, dtype=float)
        for i in range(count):
            idx = rng.integers(0, n, size=n)
            sample = [outs[j] for j in idx]
            normal = [o for o in sample if o >= threshold]
            short = [o for o in sample if o < threshold]
            mean_normal = sum(normal) / len(normal) if normal else float("nan")
            mean_short = sum(short) / len(short) if short else float("nan")
            p_short = shrink(len(short) / n, league_shortened_rate, n, k_regime)
            mu_normal = shrink(mean_normal, league_normal, n, k_rate)
            mu_short = shrink(mean_short, league_short, n, k_rate)
            support = mix_failure_paths(
                (
                    (p_short, nb_pmf(mu_short, dispersion_r, max_support)),
                    (1.0 - p_short, nb_pmf(mu_normal, dispersion_r, max_support)),
                )
            )
            p_side = _side_probability(support, line, more)
            values[i] = 0.5 + lam * (p_side - 0.5)
        return values

    return resample_fn


def mlb_pitcher_outs_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    _require_family(inference, OUTS_MODEL_FAMILY)
    try:
        n_eff = float(len(_parse_box_score_log(features.get("box_score_log"))))
    except Exception as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING",
            f"Pitching-outs calibration evidence is invalid: {exc}",
        ) from exc
    if n_eff <= 0:
        raise PropCalibrationUnavailable("PROP_CALIBRATION_EVIDENCE_MISSING", "No prior starts available.")
    more = _direction_more(raw_probability, line_probs)
    return _phase_a_output(
        raw_probability=raw_probability,
        n_eff=n_eff,
        seed=seed,
        resample_fn=_outs_resample_fn(inference, line_probs.line, more, features, n_eff),
    )


def _composition_resample_fn(
    inference: CertifiedInference,
    line: float,
    more: bool,
    features: Mapping[str, Any],
    n_eff: float,
    target: str,
):
    payload = inference.artifact.artifact_payload
    try:
        league_normal = float(payload["league_mean_normal"])
        league_short = float(payload["league_mean_short"])
        league_shortened_rate = float(payload["league_shortened_rate"])
        dispersion_r = float(payload["dispersion_r"])
        threshold = float(payload["shortened_outs_threshold"])
        k_rate = float(payload["shrinkage_k_rate"])
        k_regime = float(payload["shrinkage_k_regime"])
        max_support = int(payload["max_support_k"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_ARTIFACT_INVALID",
            "Pitch-composition calibration artifact is missing fitted constants.",
        ) from exc
    entries = _parse_pitch_composition_log(features.get("box_score_log"))
    if not entries:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING",
            "Pitch-composition calibration requires non-empty box_score_log evidence.",
        )
    n = len(entries)
    lam = n_eff / (n_eff + SHRINKAGE_K)

    def target_value(entry: Any) -> float:
        return float(entry.strikes if target == "strikes" else entry.balls)

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        values = np.empty(count, dtype=float)
        for i in range(count):
            idx = rng.integers(0, n, size=n)
            sample = [entries[j] for j in idx]
            normal = [e for e in sample if e.outs >= threshold]
            short = [e for e in sample if e.outs < threshold]
            normal_vals = [target_value(e) for e in normal]
            short_vals = [target_value(e) for e in short]
            mean_normal = sum(normal_vals) / len(normal_vals) if normal_vals else float("nan")
            mean_short = sum(short_vals) / len(short_vals) if short_vals else float("nan")
            p_short = shrink(len(short) / n, league_shortened_rate, n, k_regime)
            mu_normal = shrink(mean_normal, league_normal, n, k_rate)
            mu_short = shrink(mean_short, league_short, n, k_rate)
            support = mix_failure_paths(
                (
                    (p_short, nb_pmf(mu_short, dispersion_r, max_support)),
                    (1.0 - p_short, nb_pmf(mu_normal, dispersion_r, max_support)),
                )
            )
            p_side = _side_probability(support, line, more)
            values[i] = 0.5 + lam * (p_side - 0.5)
        return values

    return resample_fn


def _composition_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
    *,
    expected_family: str,
    target: str,
) -> PropCalibrationOutput:
    _require_family(inference, expected_family)
    try:
        n_eff = float(len(_parse_pitch_composition_log(features.get("box_score_log"))))
    except Exception as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING",
            f"Pitch-composition calibration evidence is invalid: {exc}",
        ) from exc
    if n_eff <= 0:
        raise PropCalibrationUnavailable("PROP_CALIBRATION_EVIDENCE_MISSING", "No prior starts available.")
    more = _direction_more(raw_probability, line_probs)
    return _phase_a_output(
        raw_probability=raw_probability,
        n_eff=n_eff,
        seed=seed,
        resample_fn=_composition_resample_fn(inference, line_probs.line, more, features, n_eff, target),
    )


def mlb_pitcher_strikes_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    return _composition_adapter(
        inference, raw_probability, line_probs, features, seed,
        expected_family=STRIKES_MODEL_FAMILY, target="strikes",
    )


def mlb_pitcher_balls_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    return _composition_adapter(
        inference, raw_probability, line_probs, features, seed,
        expected_family=BALLS_MODEL_FAMILY, target="balls",
    )


def _pa_resample_fn(
    inference: CertifiedInference,
    line: float,
    more: bool,
    features: Mapping[str, Any],
    n_eff: float,
):
    payload = inference.artifact.artifact_payload
    try:
        cells = {
            tuple(int(x) for x in key.split("_")): float(value)
            for key, value in payload["league_mean_pa_by_cell"].items()
        }
        league_overall = float(payload["league_mean_pa_overall"])
        dispersion_r = float(payload["dispersion_r"])
        k_rate = float(payload["shrinkage_k_rate"])
        max_support = int(payload["max_support_k"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_ARTIFACT_INVALID",
            "Plate-appearances calibration artifact is missing fitted constants.",
        ) from exc
    history = _parse_prior_pa_log(features.get("prior_pa_log", []))
    slot = features.get("batting_slot")
    alignment = features.get("team_alignment")
    if not isinstance(slot, int) or isinstance(slot, bool) or not 1 <= slot <= 9:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING", "Plate-appearances calibration requires a valid batting_slot."
        )
    if not isinstance(alignment, int) or isinstance(alignment, bool) or alignment not in (0, 1):
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING", "Plate-appearances calibration requires team_alignment 0 or 1."
        )
    if not history:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING", "Plate-appearances calibration requires prior_pa_log."
        )
    n = len(history)
    cell_mean = cells.get((slot, alignment), league_overall)
    lam = n_eff / (n_eff + SHRINKAGE_K)

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        values = np.empty(count, dtype=float)
        for i in range(count):
            idx = rng.integers(0, n, size=n)
            sample = [float(history[j]) for j in idx]
            prior_mean = sum(sample) / n
            mu = shrink(prior_mean, cell_mean, n, k_rate)
            support = nb_pmf(mu, dispersion_r, max_support)
            p_side = _side_probability(support, line, more)
            values[i] = 0.5 + lam * (p_side - 0.5)
        return values

    return resample_fn


def mlb_batter_pa_precalibration_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    _require_family(inference, PA_MODEL_FAMILY)
    try:
        n_eff = float(len(_parse_prior_pa_log(features.get("prior_pa_log", []))))
    except Exception as exc:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING",
            f"Plate-appearances calibration evidence is invalid: {exc}",
        ) from exc
    if n_eff <= 0:
        raise PropCalibrationUnavailable("PROP_CALIBRATION_EVIDENCE_MISSING", "No prior games available.")
    more = _direction_more(raw_probability, line_probs)
    return _phase_a_output(
        raw_probability=raw_probability,
        n_eff=n_eff,
        seed=seed,
        resample_fn=_pa_resample_fn(inference, line_probs.line, more, features, n_eff),
    )


def register() -> None:
    """Register only the immutable calibrator versions ratified in artifact metadata."""
    register_prop_calibration_adapter(OUTS_CALIBRATOR_VERSION, mlb_pitcher_outs_precalibration_adapter)
    register_prop_calibration_adapter(STRIKES_CALIBRATOR_VERSION, mlb_pitcher_strikes_precalibration_adapter)
    register_prop_calibration_adapter(BALLS_CALIBRATOR_VERSION, mlb_pitcher_balls_precalibration_adapter)
    register_prop_calibration_adapter(PA_CALIBRATOR_VERSION, mlb_batter_pa_precalibration_adapter)
