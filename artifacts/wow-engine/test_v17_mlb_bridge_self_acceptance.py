from __future__ import annotations

from v17_mlb_bridge_self_acceptance import evaluate_bridge_response


def test_accepts_complete_governed_probability_package():
    payload = {
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "calibrated_home_probability": 0.61,
        "calibrated_away_probability": 0.39,
        "calibrated_home_lower_bound": 0.56,
        "calibrated_away_lower_bound": 0.34,
        "model_version": "MLB_TEST",
        "model_timestamp": "2026-09-08T23:00:00+00:00",
        "can_execute": False,
    }
    result = evaluate_bridge_response(200, payload)
    assert result["accepted"] is True
    assert result["complete_probability_package"] is True
    assert result["sole_legacy_bridge_diagnosis"] is False
    assert result["can_execute"] is False


def test_accepts_canonical_scorer_failure_with_legacy_bridge_only_as_blocker_detail():
    payload = {
        "detail": {
            "code": "MODEL_SCORER_FAILED",
            "blocker_code": "EVENT_MODEL_BRIDGE_UNAVAILABLE",
            "can_execute": False,
        }
    }
    result = evaluate_bridge_response(503, payload)
    assert result["accepted"] is True
    assert result["canonical_failure"] is True
    assert result["code"] == "MODEL_SCORER_FAILED"
    assert result["blocker_code"] == "EVENT_MODEL_BRIDGE_UNAVAILABLE"
    assert result["sole_legacy_bridge_diagnosis"] is False


def test_rejects_legacy_bridge_unavailable_as_only_diagnosis():
    payload = {"detail": {"code": "EVENT_MODEL_BRIDGE_UNAVAILABLE", "can_execute": False}}
    result = evaluate_bridge_response(503, payload)
    assert result["accepted"] is False
    assert result["sole_legacy_bridge_diagnosis"] is True


def test_rejects_any_response_that_claims_execution_capability():
    payload = {
        "detail": {
            "code": "MODEL_INPUTS_INSUFFICIENT",
            "can_execute": True,
        }
    }
    result = evaluate_bridge_response(422, payload)
    assert result["accepted"] is False
    assert result["can_execute_false"] is False
