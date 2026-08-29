"""Reviewed calibration adapters for WOW_PROP_FITTED_MODEL_V1 discrete props.

Registered by immutable ``calibrator_version`` (resolved from the certified
artifact bundle -- never caller-selected) with
``prop_discrete_engine.register_prop_calibration_adapter``.

wow_predictions / wow_outcomes currently hold zero settled rows for any prop
model family (verified against the wow-engine-validation Supabase project).
There is therefore no forward cohort yet to fit calibration.phase_b_platt or
phase_c_fit_isotonic against, so the only honest calibration_status any real
inference can carry right now is Phase A -- ``PRECALIBRATION_SHRINKAGE``.
That is a recognized, publishable status (see ledger.py's
_RECOGNIZED_CALIBRATION_STATUSES / determine_publishability), just one that
is never MONEY_QUALIFIED or FINAL_APPROVED (calibration.py Section 8B.4).

Phase A's bootstrap bounds require a real resample_fn, not a fabricated
symmetric interval (see MissingResamplerError). This adapter's resample_fn
bootstraps the *same* evidence the model adapter used (this candidate's own
game_log/box_score_log), refits the shrunk rate/regime mixture per
realization, derives the same selected-side probability, and applies the
same 0.5 + lambda*(p-0.5) shrinkage transform (same lambda as the point
estimate) so the resulting bounds are percentiles of the same published
quantity, not a mismatched one.
"""
from __future__ import annotations

import math
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
from prop_model_adapters import nb_pmf, shrink

MLB_PITCHER_SO_CALIBRATOR_VERSION = "MLB_PITCHER_SO_CAL_V1"
BOUNDS_METHOD_VERSION = "PRECALIBRATION_SHRINKAGE_EVIDENCE_BOOTSTRAP_V1"


def _mlb_pitcher_so_resample_fn(inference: CertifiedInference, direction_more: bool, line: float, features: Mapping[str, Any], n_eff: float):
    payload = inference.artifact.artifact_payload
    fitted = payload["fitted_constants"]
    league_so_per_out = float(fitted["league_so_per_out"])
    league_shortened_rate = float(fitted["league_shortened_rate"])
    outs_normal_scale = float(fitted["outs_normal_scale"])
    outs_short_scale = float(fitted["outs_short_scale"])
    dispersion_r = float(fitted["dispersion_r"])
    shrinkage_k_rate = float(payload["shrinkage_k_rate"])
    shrinkage_k_regime = float(payload["shrinkage_k_regime"])
    shortened_outs_threshold = float(payload["shortened_outs_threshold"])
    max_support_k = int(payload["max_support_k"])

    game_log = [float(v) for v in features["game_log"]]
    outs = [float(entry["outs"]) for entry in features["box_score_log"]]
    n = len(game_log)
    lam = n_eff / (n_eff + SHRINKAGE_K)

    def resample_fn(rng: np.random.Generator, count: int) -> np.ndarray:
        results = np.empty(count, dtype=float)
        for i in range(count):
            idx = rng.integers(0, n, size=n)
            so_sample = [game_log[j] for j in idx]
            out_sample = [outs[j] for j in idx]
            total_out = sum(out_sample)
            if total_out <= 0:
                total_out = sum(outs)  # degenerate resample guard; falls back to observed total
                so_sample, out_sample = game_log, outs
            prior_so_per_out = sum(so_sample) / total_out
            prior_shortened_rate = sum(1 for o in out_sample if o < shortened_outs_threshold) / n

            rate = shrink(prior_so_per_out, league_so_per_out, n, shrinkage_k_rate)
            p_short = shrink(prior_shortened_rate, league_shortened_rate, n, shrinkage_k_regime)
            mu_normal = rate * outs_normal_scale
            mu_short = rate * outs_short_scale
            pmf_normal = nb_pmf(mu_normal, dispersion_r, max_support_k)
            pmf_short = nb_pmf(mu_short, dispersion_r, max_support_k)
            mixed = {
                k: p_short * pmf_short.get(k, 0.0) + (1 - p_short) * pmf_normal.get(k, 0.0)
                for k in range(max_support_k + 1)
            }
            p_more = sum(p for k, p in mixed.items() if k > line)
            p_less = sum(p for k, p in mixed.items() if k < line)
            p_side = p_more if direction_more else p_less
            results[i] = 0.5 + lam * (p_side - 0.5)
        return results

    return resample_fn


def mlb_pitcher_so_precalibration_shrinkage_adapter(
    inference: CertifiedInference,
    raw_probability: float,
    line_probs: LineProbabilities,
    features: Mapping[str, Any],
    seed: int,
) -> PropCalibrationOutput:
    game_log = features.get("game_log")
    box_score_log = features.get("box_score_log")
    if not isinstance(game_log, list) or not isinstance(box_score_log, list) or not game_log:
        raise PropCalibrationUnavailable(
            "PROP_CALIBRATION_EVIDENCE_MISSING",
            "MLB pitcher strikeout calibration requires game_log/box_score_log to bootstrap bounds.",
        )
    n_eff = float(len(game_log))
    direction_more = math.isclose(raw_probability, line_probs.probability_more, abs_tol=1e-9)

    resample_fn = _mlb_pitcher_so_resample_fn(inference, direction_more, line_probs.line, features, n_eff)
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
            f"Phase A bootstrap calibration failed: {exc}",
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
    """Production registration seam -- called once at process startup."""
    register_prop_calibration_adapter(MLB_PITCHER_SO_CALIBRATOR_VERSION, mlb_pitcher_so_precalibration_shrinkage_adapter)
