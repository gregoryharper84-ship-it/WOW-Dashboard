from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from calibration import CalibrationResult, CalibrationStatus, PlattCoefficients, PlattFitMetrics
from v17.basketball_team_event_calibration_artifact import (
    BasketballCalibrationArtifactError,
    REVIEW_REQUIRED,
    build_shadow_binary_calibration_artifact,
)
from v17.multisport_team_event_calibration import BINARY_ARTIFACT_TYPE


def _fit():
    return SimpleNamespace(
        result=CalibrationResult(
            calibration_status=CalibrationStatus.PLATT_TIME_SPLIT_V1,
            calibration_method="PLATT_TIME_SPLIT_V1",
            calibrated_probability=float("nan"),
            lower_bound=float("nan"),
            upper_bound=float("nan"),
            money_qualified_allowed=True,
            final_approved_allowed=True,
        ),
        metrics=PlattFitMetrics(brier=0.22, log_loss=0.63, ece=0.04, calibration_bias=0.01),
        coefficients=PlattCoefficients(a=-0.1, b=0.9),
    )


def test_shadow_artifact_is_hash_bound_and_not_self_certified():
    raw = [0.35 + (i % 20) * 0.015 for i in range(240)]
    outcomes = [int((i % 3) != 0) for i in range(240)]
    folds = [min(i // 40, 5) for i in range(240)]
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    timestamps = [(start + timedelta(hours=i)).isoformat() for i in range(240)]

    artifact, fingerprint = build_shadow_binary_calibration_artifact(
        sport="WNBA",
        model_family="BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
        model_version="WNBA_TEST_MODEL",
        calibration_version="WNBA_TEST_PLATT",
        raw_probabilities=raw,
        outcomes=outcomes,
        fold_assignments=folds,
        timestamps=timestamps,
        fit=_fit(),
    )
    assert artifact["artifact_type"] == BINARY_ARTIFACT_TYPE
    assert artifact["health_status"] == "PASS"
    assert artifact["certification_status"] == REVIEW_REQUIRED
    assert artifact["independent_verification_status"] == REVIEW_REQUIRED
    assert artifact["probability_publishable"] is False
    assert artifact["rank_eligible"] is False
    assert artifact["can_execute"] is False
    assert len(artifact["source_data_hash"]) == 64
    assert len(artifact["split_hash"]) == 64
    assert len(fingerprint) == 64
    assert artifact["platt_a"] == pytest.approx(0.9)
    assert artifact["platt_b"] == pytest.approx(-0.1)
    assert 0 < artifact["residual_quantile_90"] < 1


def test_shadow_artifact_rejects_short_history():
    with pytest.raises(BasketballCalibrationArtifactError):
        build_shadow_binary_calibration_artifact(
            sport="NBA",
            model_family="BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
            model_version="NBA_TEST_MODEL",
            calibration_version="NBA_TEST_PLATT",
            raw_probabilities=[0.5] * 20,
            outcomes=[1] * 20,
            fold_assignments=[0] * 20,
            timestamps=["2025-01-01T00:00:00+00:00"] * 20,
            fit=_fit(),
        )



def test_shadow_artifact_rejects_naive_or_malformed_timestamp_provenance():
    raw = [0.5] * 240
    outcomes = [i % 2 for i in range(240)]
    folds = [min(i // 40, 5) for i in range(240)]
    timestamps = ["2025-01-01T00:00:00"] * 240
    with pytest.raises(BasketballCalibrationArtifactError, match="BASKETBALL_CALIBRATION_TIMESTAMP_INVALID"):
        build_shadow_binary_calibration_artifact(
            sport="NBA",
            model_family="BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
            model_version="NBA_TEST_MODEL",
            calibration_version="NBA_TEST_PLATT",
            raw_probabilities=raw,
            outcomes=outcomes,
            fold_assignments=folds,
            timestamps=timestamps,
            fit=_fit(),
        )
