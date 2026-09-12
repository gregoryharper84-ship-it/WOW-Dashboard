from __future__ import annotations

from v17_mlb_bridge_self_acceptance import evaluate_bridge_response


def _complete_package() -> dict:
    return {
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "calibrated_home_probability": 0.61,
        "calibrated_away_probability": 0.39,
        "calibrated_home_lower_bound": 0.56,
        "calibrated_away_lower_bound": 0.34,
        "model_version": "MLB_TEST",
        "model_timestamp": "2026-09-08T23:00:00+00:00",
        "can_execute": False,
    }


def test_rejects_complete_probability_package_when_llp_governance_not_proven():
    result = evaluate_bridge_response(200, _complete_package())
    assert result["accepted"] is False
    assert result["complete_probability_package"] is True
    assert result["governance_reached"] is False
    assert result["can_execute"] is False


def test_accepts_complete_package_with_governed_no_pick():
    payload = {
        **_complete_package(),
        "llp_governance": {
            "status": "HOLD",
            "probability_audit_result": "PROBABILITY_AUDIT_FAILURE",
            "event_decision": "NO_PICK_UNCALIBRATED",
            "event_mutex_status": "PASS",
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        },
    }
    result = evaluate_bridge_response(200, payload)
    assert result["accepted"] is True
    assert result["complete_probability_package"] is True
    assert result["governance_reached"] is True
    assert result["bridge_unavailable"] is False


def test_rejects_canonical_failure_when_governance_bridge_unavailable_is_blocker():
    payload = {
        "detail": {
            "code": "MODEL_SCORER_FAILED",
            "blocker_code": "V17_EVENT_GOVERNANCE_BRIDGE_UNAVAILABLE",
            "can_execute": False,
        }
    }
    result = evaluate_bridge_response(503, payload)
    assert result["accepted"] is False
    assert result["canonical_failure"] is True
    assert result["bridge_unavailable"] is True


def test_accepts_canonical_model_failure_without_bridge_unavailable_diagnosis():
    payload = {
        "detail": {
            "code": "MODEL_SCORER_FAILED",
            "can_execute": False,
        }
    }
    result = evaluate_bridge_response(503, payload)
    assert result["accepted"] is True
    assert result["canonical_failure"] is True
    assert result["bridge_unavailable"] is False


def test_rejects_legacy_bridge_unavailable_as_only_diagnosis():
    payload = {"detail": {"code": "EVENT_MODEL_BRIDGE_UNAVAILABLE", "can_execute": False}}
    result = evaluate_bridge_response(503, payload)
    assert result["accepted"] is False
    assert result["sole_legacy_bridge_diagnosis"] is True
    assert result["bridge_unavailable"] is True


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
