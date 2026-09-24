from __future__ import annotations

import pytest

import v17.multisport_team_event_bridges as bridges
from v17.llp_governed_package_scoring import MODEL_INPUTS_INSUFFICIENT
from v17.multisport_team_event_calibration import (
    CalibrationArtifactInvalid,
    validate_binary_artifact,
)
from v17.multisport_team_event_models import MODEL_SPECS
from v17.team_event_request_runtime import TeamEventRequest


def _artifact(sport: str, *, quantitative: bool) -> dict:
    spec = MODEL_SPECS[sport]
    artifact = {
        "sport": sport,
        "model_family": spec["model_family"],
        "model_version": spec["model_version"],
        "artifact_type": "BINARY_PLATT_CALIBRATOR",
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": f"{sport.lower()}-cal-v1",
        "training_n": 120,
        "platt_a": 1.03,
        "platt_b": -0.01,
        "residual_quantile_90": 0.085,
        "brier_score": 0.19,
        "calibration_error": 0.035,
        "source_data_hash": "a" * 64,
        "split_hash": "b" * 64,
        "fit_end": "2026-09-17T23:59:59Z",
        "health_status": "PASS",
        "certification_status": "PASS",
    }
    if quantitative:
        artifact.update(
            {
                "quantitative_quality_status": "PASS",
                "quality_policy_version": "V17_TEAM_EVENT_PROBABILITY_QUALITY_V1",
                "quality_policy_hash": "c" * 64,
                "log_loss": 0.58,
                "ece": 0.04,
                "calibration_intercept": 0.01,
                "calibration_slope": 1.02,
                "max_calibration_bin_gap": 0.08,
                "quality_checks": {
                    "brier_pass": True,
                    "log_loss_pass": True,
                    "ece_pass": True,
                    "calibration_intercept_pass": True,
                    "calibration_slope_pass": True,
                    "max_calibration_bin_gap_pass": True,
                },
            }
        )
    return artifact


def _req(sport: str) -> TeamEventRequest:
    return TeamEventRequest(
        requester_host_identity="WOW_BETTING_ENGINE",
        research_run_id=f"quality-{sport.lower()}",
        requested_slate_date="2026-12-31",
        requested_timezone="UTC",
        scan_stage="PREGAME",
        candidate_family="OUTRIGHT_WINNER",
        decision_intent="WINNER",
        event_key=f"{sport}:alpha-beta",
        official_event_id=f"official-{sport.lower()}-quality-1",
        event_start_time_utc="2026-12-31T20:00:00Z",
        sport=sport,
        league=sport,
        market_family="OUTRIGHT_WINNER",
        settlement_basis="OFFICIAL_FINAL_RESULT",
        home_team="Alpha",
        away_team="Beta",
        source_snapshot_id=f"snapshot-{sport.lower()}-quality-1",
        latest_material_update_timestamp="2026-09-18T12:00:00Z",
        sport_specific_evidence={},
    )


@pytest.mark.parametrize("sport", ["WNBA", "NHL"])
def test_plain_pass_label_is_not_quantitative_calibration_proof(sport: str):
    legacy = _artifact(sport, quantitative=False)
    assert legacy["health_status"] == "PASS"
    assert legacy["certification_status"] == "PASS"

    with pytest.raises(CalibrationArtifactInvalid) as exc:
        validate_binary_artifact(legacy, sport)

    assert "CALIBRATION_QUANTITATIVE_QUALITY_NOT_PASS" in exc.value.blockers
    assert "CALIBRATION_QUALITY_POLICY_VERSION_MISSING" in exc.value.blockers
    assert "CALIBRATION_QUALITY_CHECKS_MISSING" in exc.value.blockers


@pytest.mark.parametrize("sport", ["WNBA", "NHL"])
def test_complete_quantitative_receipt_passes_artifact_validation(sport: str):
    validated = validate_binary_artifact(_artifact(sport, quantitative=True), sport)
    assert validated["quantitative_quality_status"] == "PASS"
    assert validated["quality_checks"]["calibration_slope_pass"] is True


def test_quantitative_calibration_failure_preserves_typed_inputs_insufficient():
    failure = CalibrationArtifactInvalid(
        [
            "CALIBRATION_QUANTITATIVE_QUALITY_NOT_PASS",
            "CALIBRATION_SLOPE_INVALID",
        ]
    )
    http_error = bridges._typed_failure(_req("WNBA"), failure, model_invoked=True)

    assert http_error.status_code == 422
    assert http_error.detail["code"] == MODEL_INPUTS_INSUFFICIENT
    assert http_error.detail["blockers"] == ["CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE"]
    assert http_error.detail["calibration_blockers"] == [
        "CALIBRATION_QUANTITATIVE_QUALITY_NOT_PASS",
        "CALIBRATION_SLOPE_INVALID",
    ]
    assert http_error.detail["model_invoked"] is True
    assert http_error.detail["can_execute"] is False
