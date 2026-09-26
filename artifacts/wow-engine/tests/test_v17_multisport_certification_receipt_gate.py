from __future__ import annotations

from types import SimpleNamespace

from v17.fitted_team_event_certification import parse_certification
from v17.multisport_team_event_governance import (
    FINAL_APPROVED,
    MODEL_QUALIFIED_HOLD,
    reduce_multisport_team_event,
)


SPECIALIST = "WNBA_GAME_WIN_V17_TEST"
ARTIFACT_ID = "wnba-artifact-test-1"
MODEL_VERSION = "wnba-fitted-test-v1"


def _req():
    return SimpleNamespace(
        sport="WNBA",
        league="WNBA",
        official_event_id="wnba-event-1",
        sport_specific_evidence={
            "expected_starters_rotation": "CONFIRMED",
        },
    )


def _package():
    return {
        "candidate_id": "candidate-wnba-1",
        "sport": "WNBA",
        "league": "WNBA",
        "event_key": "WNBA:wnba-event-1",
        "official_event_id": "wnba-event-1",
        "event_start_time_utc": "2026-12-31T20:00:00Z",
        "controlling_specialist": SPECIALIST,
        "artifact_id": ARTIFACT_ID,
        "model_version": MODEL_VERSION,
        "immutable_model_timestamp": "2026-12-31T18:00:00Z",
        "latest_material_update_timestamp": "2026-12-31T17:30:00Z",
        "source_snapshot_id": "snapshot-wnba-1",
        "source_snapshot_timestamp": "2026-12-31T17:30:00Z",
        "outcome_space": "HOME_AWAY",
        "raw_model_probability": 0.66,
        "calibrated_probability": 0.64,
        "calibrated_lower_bound": 0.58,
        "calibrated_upper_bound": 0.70,
        "calibrated_home_probability": 0.64,
        "calibrated_away_probability": 0.36,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "wnba-cal-test-v1",
        "calibration_history_present": True,
        "calibration_artifact_certified": True,
        "calibration_health_status": "PASS",
        "calibration_status": "PASS",
        "calibration_training_n": 120,
        "calibration_artifact_fingerprint": "b" * 64,
        "calibration_fit_end": "2026-12-30T23:59:59Z",
        "calibration_brier_score": 0.19,
        "calibration_error": 0.03,
        "market_probability_used_as_model": False,
        "generic_reasoning_used_as_model": False,
        "can_execute": False,
    }


def _certification(**overrides):
    payload = {
        "certification_id": "cert-wnba-test-1",
        "sport": "WNBA",
        "league_scope": "WNBA",
        "controlling_specialist": SPECIALIST,
        "model_family": "BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
        "model_version": MODEL_VERSION,
        "artifact_id": ARTIFACT_ID,
        "artifact_sha256": "a" * 64,
        "feature_schema_version": "BASKETBALL_TEAM_EVENT_FEATURES_V2",
        "input_contract_version": "WNBA_GAME_WIN_INPUTS_V1",
        "calibration_artifact_id": "wnba-cal-test-v1",
        "calibration_sha256": "b" * 64,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "independent_verification_status": "PASS",
        "certification_status": "CERTIFIED",
        "certified_at": "2026-12-31T16:00:00Z",
        "can_execute": False,
    }
    payload.update(overrides)
    return parse_certification(payload)


def test_registered_style_package_without_resolved_receipt_cannot_publish():
    result = reduce_multisport_team_event(_req(), _package())
    assert result["status"] == "HOLD"
    assert result["terminal_label"] == MODEL_QUALIFIED_HOLD
    assert "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_explicit_resolved_fitted_receipt_can_clear_only_the_certification_gate():
    result = reduce_multisport_team_event(
        _req(),
        _package(),
        certification_receipt=_certification(),
    )
    assert result["status"] == "PASS"
    assert result["terminal_label"] == FINAL_APPROVED
    assert result["certification_id"] == "cert-wnba-test-1"
    assert result["certified_controlling_specialist"] == SPECIALIST
    assert result["blockers"] == []
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is True
    assert result["can_execute"] is False


def test_receipt_artifact_mismatch_holds_even_when_other_probability_gates_pass():
    result = reduce_multisport_team_event(
        _req(),
        _package(),
        certification_receipt=_certification(artifact_id="different-artifact"),
    )
    assert result["status"] == "HOLD"
    assert "TEAM_EVENT_CERTIFICATION_ARTIFACT_MISMATCH" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False


def test_receipt_model_version_mismatch_holds():
    result = reduce_multisport_team_event(
        _req(),
        _package(),
        certification_receipt=_certification(model_version="different-model-version"),
    )
    assert result["status"] == "HOLD"
    assert "TEAM_EVENT_CERTIFICATION_MODEL_VERSION_MISMATCH" in result["blockers"]


def test_receipt_specialist_mismatch_holds():
    result = reduce_multisport_team_event(
        _req(),
        _package(),
        certification_receipt=_certification(controlling_specialist="OTHER_SPECIALIST"),
    )
    assert result["status"] == "HOLD"
    assert "TEAM_EVENT_CERTIFICATION_SPECIALIST_MISMATCH" in result["blockers"]
