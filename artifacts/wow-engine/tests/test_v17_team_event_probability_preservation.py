from types import SimpleNamespace

from v17 import team_event_request_runtime as base
import v17.team_event_probability_preservation as repair


def _req(**evidence):
    return SimpleNamespace(sport_specific_evidence=evidence, decision_intent="BEST_SIDE")


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
        _req(), _route(), model_result,
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
    assert out["probability_fields_withheld"] is False
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert out["can_execute"] is False
    assert "MARKET_ROLE_NOT_LOCKED" in out["blockers"]


def test_governance_hold_does_not_manufacture_probability_when_scorer_has_none():
    out = repair._preserve_completed_probability_hold(
        _req(), _route(), {"code": "MODEL_SCORER_FAILED"},
        governance_detail={"status": "HOLD"},
    )
    for field in base._MLB_NUMERIC_MODEL_FIELDS:
        assert field not in out
    assert out["sporting_probability_completed"] is False
    assert out["probability_fields_withheld"] is True
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_shared_environment_and_evidence_handoff_replay_same_governance_once(monkeypatch):
    governance_calls = []
    rpc_calls = []

    def fake_governance(req, route, model_result, envelope=None, *, event_api):
        governance_calls.append("governance")
        if len(governance_calls) == 1:
            return {
                "probability_publishable": False,
                "rank_eligible": False,
                "llp_governance": {
                    "status": "HOLD",
                    "event_prediction_id": "event-pred-1",
                    "score_snapshot_id": "score-1",
                },
                "can_execute": False,
            }
        return {
            "probability_publishable": False,
            "rank_eligible": False,
            "llp_governance": {"status": "HOLD"},
            "can_execute": False,
        }

    class Result:
        def __init__(self, data):
            self.data = data

    class RpcCall:
        def __init__(self, data):
            self._data = data

        def execute(self):
            return Result(self._data)

    class Client:
        def rpc(self, name, payload):
            rpc_calls.append(name)
            assert payload["p_event_prediction_id"] == "event-pred-1"
            assert payload["p_score_snapshot_id"] == "score-1"
            if name == "wow_v17_hydrate_shared_environmental_evidence":
                return RpcCall({
                    "status": "PASS",
                    "environmental_evidence_produced": True,
                    "weather": {"condition": "Clear", "temp": "73", "wind": "7 mph"},
                    "probability_adjustment_applied": False,
                    "can_execute": False,
                })
            assert name == "wow_v17_hydrate_mlb_event_governance_evidence"
            assert payload["p_evidence"]["weather_status"] == "CLEAR"
            assert payload["p_decision_intent"] == "BEST_SIDE"
            return RpcCall({
                "status": "PASS",
                "evidence_rows_hydrated": 10,
                "scoring_evidence_row_count": 10,
                "complete_scoring_evidence_snapshot": True,
                "can_execute": False,
            })

    monkeypatch.setattr(repair, "_original_run_mlb_llp_governance", fake_governance)
    event_api = SimpleNamespace(get_client=lambda: Client())

    out = repair._run_mlb_llp_governance_with_evidence_handoff(
        _req(weather_status="CLEAR"),
        _route(),
        {"score_snapshot_id": "score-1", "raw_home_probability": 0.52},
        event_api=event_api,
    )

    assert governance_calls == ["governance", "governance"]
    assert rpc_calls == [
        "wow_v17_hydrate_shared_environmental_evidence",
        "wow_v17_hydrate_mlb_event_governance_evidence",
    ]
    assert out["governance_replayed_after_evidence_handoff"] is True
    assert out["shared_environmental_evidence"]["environmental_evidence_produced"] is True
    assert out["shared_environmental_evidence"]["probability_adjustment_applied"] is False
    assert out["evidence_handoff_repair"]["complete_scoring_evidence_snapshot"] is True
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_import_does_not_mutate_base_helpers_or_terminal_override():
    assert base._llp_governance_hold is repair._original_hold
    assert base._run_mlb_llp_governance is repair._original_run_mlb_llp_governance
    assert base.CAN_EXECUTE is False
