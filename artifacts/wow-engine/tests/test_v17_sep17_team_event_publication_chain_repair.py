from types import SimpleNamespace

from v17.sep17_team_event_publication_chain_repair import (
    install_team_event_publication_chain_repair,
)


def _model(**overrides):
    payload = {
        "score_snapshot_id": "00000000-0000-0000-0000-000000000099",
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
        "model_timestamp": "2026-09-17T17:20:01+00:00",
        "latest_material_update_timestamp": "2026-09-17T17:20:00+00:00",
        "source_snapshot_id": "00000000-0000-0000-0000-000000000001",
        "probability_package_valid": True,
        "dynamic_calibration_complete": True,
        "model_probability_publishable": True,
        "probability_fields_withheld": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    payload.update(overrides)
    return payload


def _runtime():
    def validate(result):
        required = (
            "raw_home_probability",
            "raw_away_probability",
            "calibrated_home_probability",
            "calibrated_away_probability",
            "calibration_method",
            "calibration_version",
            "calibration_health_status",
            "model_version",
            "model_timestamp",
        )
        errors = [f"MODEL_OUTPUT_FIELD_INVALID:{field}" for field in required if result.get(field) in (None, "")]
        return not errors, errors

    return SimpleNamespace(_validate_model_output_lossless=validate)


def _preservation(original):
    return SimpleNamespace(_original_run_mlb_llp_governance=original)


def test_probability_only_valid_model_contract_can_enter_existing_governance_chain():
    seen = {}

    def original(req, route, model_result, envelope=None, *, event_api):
        seen.update(model_result)
        return {
            "probability_publishable": model_result.get("probability_publishable") is True,
            "rank_eligible": model_result.get("probability_publishable") is True,
            "can_execute": False,
        }

    preservation = _preservation(original)
    assert install_team_event_publication_chain_repair(
        preservation=preservation,
        team_runtime=_runtime(),
    ) is True

    out = preservation._original_run_mlb_llp_governance(
        SimpleNamespace(decision_intent="BEST_SIDE"),
        SimpleNamespace(),
        _model(),
        event_api=SimpleNamespace(),
    )

    assert seen["probability_publishable"] is True
    assert seen["probability_only_model_contract_ready"] is True
    assert out["rank_eligible"] is True
    assert out["probability_only_model_publication_repair"]["status"] == "APPLIED"
    assert out["probability_only_model_publication_repair"]["rank_eligibility_mutated_by_repair"] is False
    assert out["can_execute"] is False


def test_market_relative_intent_is_never_repaired():
    seen = {}

    def original(req, route, model_result, envelope=None, *, event_api):
        seen.update(model_result)
        return {"rank_eligible": False, "probability_publishable": False, "can_execute": False}

    preservation = _preservation(original)
    install_team_event_publication_chain_repair(preservation=preservation, team_runtime=_runtime())
    out = preservation._original_run_mlb_llp_governance(
        SimpleNamespace(decision_intent="UPSET"), SimpleNamespace(), _model(), event_api=SimpleNamespace()
    )
    assert seen["probability_publishable"] is False
    assert out["probability_only_model_publication_repair"]["status"] == "NOT_APPLIED"
    assert "MARKET_RELATIVE_INTENT_NOT_ELIGIBLE" in out["probability_only_model_publication_repair"]["blockers"]


def test_stale_post_lineup_model_remains_fail_closed():
    seen = {}

    def original(req, route, model_result, envelope=None, *, event_api):
        seen.update(model_result)
        return {"rank_eligible": False, "probability_publishable": False, "can_execute": False}

    preservation = _preservation(original)
    install_team_event_publication_chain_repair(preservation=preservation, team_runtime=_runtime())
    out = preservation._original_run_mlb_llp_governance(
        SimpleNamespace(decision_intent="WINNER"),
        SimpleNamespace(),
        _model(
            model_timestamp="2026-09-17T17:19:59+00:00",
            latest_material_update_timestamp="2026-09-17T17:20:00+00:00",
        ),
        event_api=SimpleNamespace(),
    )
    assert seen["probability_publishable"] is False
    assert "MODEL_RERUN_REQUIRED" in out["probability_only_model_publication_repair"]["blockers"]
    assert out["rank_eligible"] is False


def test_invalid_model_package_is_not_forwarded_as_publishable():
    seen = {}

    def original(req, route, model_result, envelope=None, *, event_api):
        seen.update(model_result)
        return {"rank_eligible": False, "probability_publishable": False, "can_execute": False}

    preservation = _preservation(original)
    install_team_event_publication_chain_repair(preservation=preservation, team_runtime=_runtime())
    out = preservation._original_run_mlb_llp_governance(
        SimpleNamespace(decision_intent="WINNER"),
        SimpleNamespace(),
        _model(calibrated_home_probability=None),
        event_api=SimpleNamespace(),
    )
    assert seen["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_installer_is_idempotent_and_can_execute_never_changes():
    calls = []

    def original(req, route, model_result, envelope=None, *, event_api):
        calls.append(1)
        return {"rank_eligible": False, "probability_publishable": False, "can_execute": False}

    preservation = _preservation(original)
    assert install_team_event_publication_chain_repair(preservation=preservation, team_runtime=_runtime()) is True
    wrapped = preservation._original_run_mlb_llp_governance
    assert install_team_event_publication_chain_repair(preservation=preservation, team_runtime=_runtime()) is True
    assert preservation._original_run_mlb_llp_governance is wrapped
    out = wrapped(SimpleNamespace(decision_intent="WINNER"), SimpleNamespace(), _model(), event_api=SimpleNamespace())
    assert calls == [1]
    assert out["can_execute"] is False
