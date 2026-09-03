from types import SimpleNamespace

from v17 import team_event_request_runtime as base
import v17.team_event_probability_preservation as repair


def _req():
    return SimpleNamespace()


def _route():
    return SimpleNamespace(
        requester_host_identity="WOW_BETTING_ENGINE",
        candidate_family="MONEYLINE",
    )


def test_governance_hold_preserves_completed_fitted_probabilities():
    model_result = {
        "code": "MODEL_SCORED_HELD",
        "raw_home_probability": 0.512048901182395,
        "raw_away_probability": 0.487951098817605,
        "calibrated_home_probability": 0.538375441724871,
        "calibrated_away_probability": 0.461624558275129,
        "calibrated_home_lower_bound": 0.513554159273017,
        "calibrated_away_lower_bound": 0.258955562534601,
        "model_version": "mlb-test",
        "score_snapshot_id": "snapshot-1",
    }

    out = repair._preserve_completed_probability_hold(
        _req(),
        _route(),
        model_result,
        governance_detail={
            "status": "HOLD",
            "blockers": ["MARKET_ROLE_NOT_LOCKED"],
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        },
    )

    assert out["raw_home_probability"] == model_result["raw_home_probability"]
    assert out["calibrated_home_probability"] == model_result["calibrated_home_probability"]
    assert out["calibrated_home_lower_bound"] == model_result["calibrated_home_lower_bound"]
    assert out["sporting_probability_completed"] is True
    assert out["sporting_probability_status"] == "COMPLETED_HELD_DOWNSTREAM"
    assert out["probability_fields_withheld"] is False
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert out["can_execute"] is False
    assert "MARKET_ROLE_NOT_LOCKED" in out["blockers"]


def test_governance_hold_does_not_manufacture_probability_when_scorer_has_none():
    out = repair._preserve_completed_probability_hold(
        _req(),
        _route(),
        {"code": "MODEL_SCORER_FAILED"},
        governance_detail={"status": "HOLD"},
    )

    for field in base._MLB_NUMERIC_MODEL_FIELDS:
        assert field not in out
    assert out["sporting_probability_completed"] is False
    assert out["sporting_probability_status"] == "NOT_COMPLETED"
    assert out["probability_fields_withheld"] is True
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_import_replaces_base_hold_serializer_without_terminal_override():
    assert base._llp_governance_hold is repair._preserve_completed_probability_hold
    assert base.CAN_EXECUTE is False
