"""Fix B: NHL certification-bound calibrator produces bounds; identity fails closed."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from v17 import nhl_bound_calibrator as bound
from v17.binary_candidate_lifecycle import (
    EMPIRICAL_CALIBRATION_METHOD,
    IDENTITY_CALIBRATION_METHOD,
)


FIT_END = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)


def _synthetic_calibration(n: int = 200, *, seed: int = 7) -> tuple[list[float], list[int]]:
    rng = np.random.default_rng(seed)
    # Mildly miscalibrated raw scores so empirical bins have work to do.
    logits = rng.normal(loc=0.2, scale=1.0, size=n)
    raw = 1.0 / (1.0 + np.exp(-logits))
    # Clip away from exact 0/1 for open-interval input validation.
    raw = np.clip(raw, 1e-3, 1.0 - 1e-3)
    outcomes = (rng.uniform(size=n) < np.clip(raw * 0.9 + 0.05, 1e-3, 1 - 1e-3)).astype(int)
    # Guarantee both classes.
    outcomes[0] = 0
    outcomes[1] = 1
    return raw.tolist(), outcomes.tolist()


def test_bound_calibrator_never_authorizes_execution_or_publication():
    assert bound.CAN_EXECUTE is False
    assert bound.PROBABILITY_PUBLISHABLE is False


def test_build_produces_empirical_bins_fingerprint_and_quality_metrics():
    raw, y = _synthetic_calibration()
    result = bound.build_nhl_certification_bound_calibrator(
        raw, y, fit_end_timestamp=FIT_END
    )
    assert result["status"] == "NHL_CERTIFICATION_BOUND_CALIBRATOR_READY"
    assert result["certification_calibrator_ready"] is True
    assert result["calibration_method"] == EMPIRICAL_CALIBRATION_METHOD
    assert result["identity_calibrator_forbidden"] is True
    assert result["can_execute"] is False
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["disposition"] == "WORKING_NOT_COMPLETE"

    payload = result["calibrator_payload"]
    assert payload["method"] == EMPIRICAL_CALIBRATION_METHOD
    assert payload["bins"]
    assert payload["fit_end_timestamp"] == FIT_END.isoformat()
    quality = result["chronological_quality"]
    for key in (
        "pre_brier",
        "post_brier",
        "pre_log_loss",
        "post_log_loss",
        "calibration_bias",
        "lower_bound_reliability_gap",
        "evaluation_n",
    ):
        assert key in quality
    assert len(result["calibration_artifact_fingerprint"]) == 64

    # Fingerprint is stable for identical payload.
    again = bound.build_nhl_certification_bound_calibrator(
        raw, y, fit_end_timestamp=FIT_END
    )
    assert again["calibration_artifact_fingerprint"] == result["calibration_artifact_fingerprint"]


def test_assess_rejects_identity_calibrator():
    report = bound.assess_nhl_certification_bound_calibrator(
        {
            "method": IDENTITY_CALIBRATION_METHOD,
            "training_n": 1226,
            "selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
            "bins": [],
        }
    )
    assert report["status"] == "BLOCKED"
    assert report["code"] == "NHL_IDENTITY_CALIBRATOR_FORBIDDEN"
    assert report["g3_ready"] is False
    assert report["can_execute"] is False


def test_assess_accepts_built_bound_calibrator():
    raw, y = _synthetic_calibration()
    built = bound.build_nhl_certification_bound_calibrator(
        raw, y, fit_end_timestamp=FIT_END
    )
    report = bound.assess_nhl_certification_bound_calibrator(built["calibrator_payload"])
    assert report["status"] == "G3_BOUND_CALIBRATOR_READY"
    assert report["g3_ready"] is True
    assert report["calibration_artifact_fingerprint"] == built["calibration_artifact_fingerprint"]
    assert report["probability_publishable"] is False


def test_build_fails_closed_on_insufficient_sample():
    with pytest.raises(bound.NHLBoundCalibratorError) as exc:
        bound.build_nhl_certification_bound_calibrator(
            [0.4, 0.6] * 10, [0, 1] * 10, fit_end_timestamp=FIT_END
        )
    assert exc.value.code == "NHL_BOUND_CALIBRATOR_SAMPLE_INSUFFICIENT"


def test_assess_rejects_missing_chronological_quality():
    report = bound.assess_nhl_certification_bound_calibrator(
        {
            "method": EMPIRICAL_CALIBRATION_METHOD,
            "training_n": 100,
            "bins": [
                {
                    "raw_mean": 0.5,
                    "calibrated_probability": 0.5,
                    "wilson_lower": 0.4,
                    "wilson_upper": 0.6,
                }
            ],
            "fit_end_timestamp": FIT_END.isoformat(),
        }
    )
    assert report["code"] == "NHL_BOUND_CALIBRATOR_QUALITY_METRICS_MISSING"
    assert report["g3_ready"] is False


def test_build_normalizes_homogeneous_wilson_endpoints_for_scoring_contract():
    # Force several low-probability all-loss observations so Wilson lower hits 0.
    raw = [0.05 + 0.001 * i for i in range(60)] + [0.80 + 0.001 * i for i in range(60)]
    y = [0] * 60 + [1] * 60
    result = bound.build_nhl_certification_bound_calibrator(
        raw, y, fit_end_timestamp=FIT_END
    )
    assert result["certification_calibrator_ready"] is True
    for entry in result["calibrator_payload"]["bins"]:
        assert 0.0 < entry["wilson_lower"] <= entry["calibrated_probability"] <= entry["wilson_upper"] < 1.0
        assert 0.0 <= entry["raw_mean"] <= 1.0
    report = bound.assess_nhl_certification_bound_calibrator(result["calibrator_payload"])
    assert report["g3_ready"] is True


def test_assess_rejects_missing_raw_mean_and_unusable_fit_end():
    base_bin = {
        "calibrated_probability": 0.5,
        "wilson_lower": 0.4,
        "wilson_upper": 0.6,
    }
    missing_center = bound.assess_nhl_certification_bound_calibrator(
        {
            "method": EMPIRICAL_CALIBRATION_METHOD,
            "training_n": 100,
            "bins": [base_bin],
            "fit_end_timestamp": FIT_END.isoformat(),
            "chronological_quality": {
                "evaluation_n": 40,
                "pre_brier": 0.2,
                "post_brier": 0.19,
                "pre_log_loss": 0.5,
                "post_log_loss": 0.49,
                "calibration_bias": 0.01,
                "lower_bound_reliability_gap": 0.02,
            },
        }
    )
    assert missing_center["code"] == "NHL_BOUND_CALIBRATOR_BINS_INVALID"

    bad_fit_end = bound.assess_nhl_certification_bound_calibrator(
        {
            "method": EMPIRICAL_CALIBRATION_METHOD,
            "training_n": 100,
            "bins": [{**base_bin, "raw_mean": 0.5}],
            "fit_end_timestamp": "not-a-date",
            "chronological_quality": {
                "evaluation_n": 40,
                "pre_brier": 0.2,
                "post_brier": 0.19,
                "pre_log_loss": 0.5,
                "post_log_loss": 0.49,
                "calibration_bias": 0.01,
                "lower_bound_reliability_gap": 0.02,
            },
        }
    )
    assert bad_fit_end["code"] == "NHL_BOUND_CALIBRATOR_FIT_END_INVALID"


def test_assess_rejects_nonfinite_quality_and_boundary_raw_inputs():
    with pytest.raises(bound.NHLBoundCalibratorError) as exc:
        bound.build_nhl_certification_bound_calibrator(
            [0.0] + [0.4] * 60 + [0.7] * 60,
            [0] + [0] * 60 + [1] * 60,
            fit_end_timestamp=FIT_END,
        )
    assert exc.value.code == "NHL_BOUND_CALIBRATOR_INPUT_OUT_OF_RANGE"

    report = bound.assess_nhl_certification_bound_calibrator(
        {
            "method": EMPIRICAL_CALIBRATION_METHOD,
            "training_n": 100,
            "bins": [
                {
                    "raw_mean": 0.5,
                    "calibrated_probability": 0.5,
                    "wilson_lower": 0.4,
                    "wilson_upper": 0.6,
                }
            ],
            "fit_end_timestamp": FIT_END.isoformat(),
            "chronological_quality": {
                "evaluation_n": 40,
                "pre_brier": float("nan"),
                "post_brier": 0.19,
                "pre_log_loss": 0.5,
                "post_log_loss": 0.49,
                "calibration_bias": 0.01,
                "lower_bound_reliability_gap": 0.02,
            },
        }
    )
    assert report["code"] == "NHL_BOUND_CALIBRATOR_QUALITY_METRICS_MISSING"
