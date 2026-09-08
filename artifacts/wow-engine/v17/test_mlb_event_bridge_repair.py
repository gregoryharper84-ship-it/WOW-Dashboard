from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17 import mlb_event_bridge_repair as repair


class _EventApi:
    @staticmethod
    def get_client():
        return object()


def _req():
    return SimpleNamespace(
        official_event_id="mlb-test-1",
        source_snapshot_id="00000000-0000-0000-0000-000000000001",
        research_run_id="rr-test",
        event_key="MLB:mlb-test-1",
    )


def _bridge_ready():
    return {
        "ok": True,
        "bridge_payload": {
            "code": "REAL_FITTED_MODEL_PATH_PROVEN",
            "score_snapshot_id": "00000000-0000-0000-0000-000000000099",
            "shadow_event_id": "00000000-0000-0000-0000-000000000088",
        },
        "client": object(),
        "source_snapshot_id": "00000000-0000-0000-0000-000000000001",
        "source_snapshot_timestamp": "2026-09-08T18:00:00+00:00",
        "latest_material_update_timestamp": "2026-09-08T18:05:00+00:00",
        "sport_model_invoked": False,
    }


def _valid_package():
    return {
        "status": "MODEL_SCORED_PROSPECTIVE",
        "code": "GOVERNED_MODEL_PROBABILITY_PROSPECTIVE",
        "controlling_specialist": "wow.mlb-game-win-probability-expert",
        "raw_home_probability": 0.61,
        "raw_away_probability": 0.39,
        "independent_home_probability": 0.61,
        "independent_away_probability": 0.39,
        "calibrated_home_probability": 0.60,
        "calibrated_away_probability": 0.40,
        "calibrated_home_lower_bound": 0.56,
        "calibrated_home_upper_bound": 0.64,
        "calibrated_away_lower_bound": 0.36,
        "calibrated_away_upper_bound": 0.44,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "mlb-cal-v1",
        "calibration_training_n": 500,
        "calibration_health_status": "PASS",
        "model_version": "MLB_EVENT_TEST_V1",
        "model_timestamp": "2026-09-08T18:06:00+00:00",
        "source_snapshot_id": "00000000-0000-0000-0000-000000000001",
        "model_probability_publishable": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def test_registered_model_bridge_failure_is_model_scorer_failed(monkeypatch):
    monkeypatch.setattr(repair, "_resolve_bridge_payload", lambda req, event_api: _bridge_ready())

    def _raise(**kwargs):
        raise RuntimeError("bridge exploded")

    with pytest.raises(HTTPException) as exc_info:
        repair.score_event_v17_bridge(_req(), event_api=_EventApi, scorer_override=_raise)

    detail = exc_info.value.detail
    assert detail["status"] == "MODEL_SCORER_FAILED"
    assert detail["code"] == "MODEL_SCORER_FAILED"
    assert detail["blocker_code"] == "EVENT_MODEL_BRIDGE_UNAVAILABLE"
    assert detail["sport_model_selected"] is True
    assert detail["sport_model_invoked"] is True
    assert detail["rank_eligible"] is False
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_missing_canonical_inputs_are_model_inputs_insufficient(monkeypatch):
    monkeypatch.setattr(
        repair,
        "_resolve_bridge_payload",
        lambda req, event_api: {
            "ok": False,
            "status": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            "missing_fields": ["venue_name", "home_probable_pitcher"],
            "sport_model_invoked": False,
            "source_snapshot_id": req.source_snapshot_id,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        repair.score_event_v17_bridge(_req(), event_api=_EventApi, scorer_override=lambda **kwargs: {})

    detail = exc_info.value.detail
    assert detail["status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert detail["blocker_code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"
    assert detail["missing_fields"] == ["venue_name", "home_probable_pitcher"]
    assert detail["sport_model_selected"] is True
    assert detail["sport_model_invoked"] is False
    assert detail["rank_eligible"] is False
    assert detail["can_execute"] is False


def test_malformed_scorer_package_is_model_output_invalid(monkeypatch):
    monkeypatch.setattr(repair, "_resolve_bridge_payload", lambda req, event_api: _bridge_ready())
    malformed = _valid_package()
    malformed.pop("calibrated_home_lower_bound")

    with pytest.raises(HTTPException) as exc_info:
        repair.score_event_v17_bridge(
            _req(),
            event_api=_EventApi,
            scorer_override=lambda **kwargs: malformed,
        )

    detail = exc_info.value.detail
    assert detail["status"] == "MODEL_OUTPUT_INVALID"
    assert detail["sport_model_invoked"] is True
    assert detail["probability_package_valid"] is False
    assert "calibrated_home_lower_bound" in detail["failing_fields"]
    assert detail["rank_eligible"] is False
    assert detail["can_execute"] is False


def test_valid_mlb_package_completes_direct_model_stage_chain(monkeypatch):
    monkeypatch.setattr(repair, "_resolve_bridge_payload", lambda req, event_api: _bridge_ready())
    result = repair.score_event_v17_bridge(
        _req(),
        event_api=_EventApi,
        scorer_override=lambda **kwargs: _valid_package(),
    )

    assert result["event_identity_complete"] is True
    assert result["sport_model_selected"] is True
    assert result["sport_model_invoked"] is True
    assert result["probability_package_valid"] is True
    assert result["dynamic_calibration_complete"] is True
    assert result["probability_audit_passed"] is False
    assert result["event_governor_complete"] is False
    assert result["rank_eligible"] is False
    assert result["normalized_outcome_space"] == {"HOME": 0.60, "AWAY": 0.40}
    assert result["can_execute"] is False


def test_import_resolution_failure_is_scorer_failed_not_model_unavailable(monkeypatch):
    monkeypatch.setattr(repair, "_resolve_direct_scorer", lambda: (_ for _ in ()).throw(ImportError("no scorer")))

    with pytest.raises(HTTPException) as exc_info:
        repair.score_event_v17_bridge(_req(), event_api=_EventApi)

    detail = exc_info.value.detail
    assert detail["status"] == "MODEL_SCORER_FAILED"
    assert detail["blocker_code"] == "EVENT_MODEL_BRIDGE_UNAVAILABLE"
    assert detail["sport_model_selected"] is True
    assert detail["sport_model_invoked"] is False
    assert detail["can_execute"] is False


def test_health_is_down_when_registered_bridge_cannot_resolve_scorer(monkeypatch):
    monkeypatch.setattr(repair, "_resolve_direct_scorer", lambda: (_ for _ in ()).throw(ImportError("no scorer")))
    state = repair.mlb_team_event_bridge_readiness(event_api=_EventApi)
    assert state["registered_capability"] is True
    assert state["adapter_importable"] is True
    assert state["scorer_resolvable"] is False
    assert state["status"] == "DOWN"
    assert state["can_execute"] is False


def test_health_is_up_when_direct_adapter_and_scorer_resolve(monkeypatch):
    monkeypatch.setattr(repair, "_resolve_direct_scorer", lambda: (lambda **kwargs: {}))
    state = repair.mlb_team_event_bridge_readiness(event_api=_EventApi)
    assert state["registered_capability"] is True
    assert state["adapter_importable"] is True
    assert state["scorer_resolvable"] is True
    assert state["canonical_snapshot_provider_ready"] is True
    assert state["status"] == "UP"
    assert state["can_execute"] is False


def test_governance_stage_audit_marks_probability_and_event_governor_pass():
    model = {
        **_valid_package(),
        "probability_package_valid": True,
        "dynamic_calibration_complete": True,
        "event_identity_complete": True,
        "sport_model_selected": True,
        "sport_model_invoked": True,
        "source_snapshot_timestamp": "2026-09-08T18:00:00+00:00",
    }

    def _governed(*args, **kwargs):
        return {
            **model,
            "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "llp_governance": {"status": "PASS"},
            "rank_eligible": True,
            "can_execute": False,
        }

    result = repair.governance_with_stage_audit(
        _req(),
        SimpleNamespace(),
        model,
        None,
        event_api=_EventApi,
        original=_governed,
    )
    assert result["probability_audit_passed"] is True
    assert result["event_governor_complete"] is True
    assert result["rank_eligible"] is True
    assert result["last_completed_stage"] == "EVENT_GOVERNOR"
    assert result["can_execute"] is False
