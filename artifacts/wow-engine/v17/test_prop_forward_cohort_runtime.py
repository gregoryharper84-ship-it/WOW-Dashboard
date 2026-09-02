from datetime import datetime, timezone

from v17.prop_forward_cohort_runtime import (
    PROVIDER,
    _prediction_payload,
    _readiness,
)


NOW = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)


def _snapshot():
    return {
        "source_snapshot_id": "11111111-1111-1111-1111-111111111111",
        "captured_at": "2026-09-02T19:30:00+00:00",
        "event_id": "MLB:TEST-001",
        "event_start_time": "2026-09-03T00:00:00+00:00",
        "sport": "MLB",
        "player": "Test Pitcher",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "hydration_status": "PASS",
        "blockers": [],
    }


def _research_only():
    return {
        "ok": True,
        "probability_publishable": False,
        "governed_publishable": False,
        "research_only": True,
        "research_model_output": {
            "raw_specialist_probability": 0.61,
            "raw_probability_more": 0.61,
            "raw_probability_less": 0.36,
            "push_probability": 0.03,
            "provider_identity": PROVIDER,
            "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
            "model_artifact_version": "SO-ARTIFACT-V1",
            "model_artifact_checksum": "abc123",
            "specialist_version": "SO-SPECIALIST-V1",
            "certification_id": "CERT-001",
            "distribution_type": "NEGATIVE_BINOMIAL",
            "calibrated_probability": None,
            "calibrated_probability_lower_bound": None,
            "calibrated_probability_upper_bound": None,
            "calibration_status": "UNKNOWN_OR_BLOCKED",
            "model_timestamp": "2026-09-02T19:45:00+00:00",
        },
        "blockers": ["PROP_CALIBRATOR_FORWARD_SAMPLE_INSUFFICIENT"],
    }


def test_research_only_forecast_is_persistable_but_never_relabelled_publishable():
    payload = _prediction_payload(_snapshot(), "MORE", _research_only(), now=NOW)

    assert payload is not None
    assert payload["raw_model_probability"] == 0.61
    assert payload["probability_more"] == 0.61
    assert payload["probability_less"] == 0.36
    assert payload["push_probability"] == 0.03
    assert payload["model_provider_identity"] == PROVIDER
    assert payload["model_family"] == "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"
    assert payload["source_snapshot_id"] == _snapshot()["source_snapshot_id"]
    assert payload["probability_publishable"] is False
    assert payload.get("calibrated_probability") is None


def test_deterministic_prediction_identity_is_direction_specific():
    more = _prediction_payload(_snapshot(), "MORE", _research_only(), now=NOW)
    less = _prediction_payload(_snapshot(), "LESS", _research_only(), now=NOW)
    repeat_more = _prediction_payload(_snapshot(), "MORE", _research_only(), now=NOW)

    assert more is not None and less is not None and repeat_more is not None
    assert more["prediction_id"] != less["prediction_id"]
    assert more["prediction_id"] == repeat_more["prediction_id"]


def test_post_start_model_timestamp_is_not_eligible_for_forward_cohort():
    scored = _research_only()
    scored["research_model_output"]["model_timestamp"] = "2026-09-03T00:00:00+00:00"

    assert _prediction_payload(_snapshot(), "MORE", scored, now=NOW) is None


def test_row_level_publishable_package_is_preserved_when_backend_says_true():
    scored = {
        "probability_publishable": True,
        "prediction": {
            "raw_model_probability": 0.64,
            "calibrated_probability": 0.60,
            "calibrated_probability_lower_bound": 0.55,
            "calibrated_probability_upper_bound": 0.65,
            "calibration_status": "CALIBRATED",
            "calibration_method": "ISOTONIC",
            "calibration_version": "MLB_PITCHER_SO_CAL_V1",
            "model_timestamp": "2026-09-02T19:45:00+00:00",
            "model_provider_identity": PROVIDER,
            "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
        },
    }

    payload = _prediction_payload(_snapshot(), "MORE", scored, now=NOW)

    assert payload is not None
    assert payload["probability_publishable"] is True
    assert payload["calibrated_probability"] == 0.60
    assert payload["calibrated_probability_lower_bound"] == 0.55


def test_readiness_uses_backend_owned_thresholds_and_does_not_self_promote():
    evidence = {"phase_b_min_settled_n": 200, "phase_c_min_settled_n": 500}

    phase_a = _readiness(evidence, prediction_n=250, settled_n=199)
    phase_b = _readiness(evidence, prediction_n=250, settled_n=200)
    phase_c = _readiness(evidence, prediction_n=600, settled_n=500)

    assert phase_a["status"] == "PHASE_A_FORWARD_COHORT_BUILDING"
    assert phase_a["remaining_to_phase_b"] == 1
    assert phase_b["status"] == "PHASE_B_THRESHOLD_REACHED_CALIBRATOR_FIT_REQUIRED"
    assert phase_c["status"] == "PHASE_C_THRESHOLD_REACHED_CALIBRATOR_FIT_REQUIRED"
    assert phase_b["calibrator_fit_allowed"] is False
    assert phase_c["calibrator_fit_allowed"] is False
    assert phase_c["can_execute"] is False


def test_missing_backend_thresholds_fail_closed():
    readiness = _readiness({}, prediction_n=10, settled_n=10)

    assert readiness["status"] == "CALIBRATION_THRESHOLDS_UNAVAILABLE"
    assert readiness["calibrator_fit_allowed"] is False
    assert readiness["can_execute"] is False
