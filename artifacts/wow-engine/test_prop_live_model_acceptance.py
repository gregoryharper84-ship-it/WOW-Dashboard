from __future__ import annotations

import httpx

from prop_live_model_acceptance import _is_model_path_pass, _snapshot_payload


def _response(body: dict, status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "http://localhost/score-prop")
    return httpx.Response(status, json=body, request=request)


def test_acceptance_validator_requires_real_model_path_and_immutable_prediction():
    body = {
        "ok": True,
        "prediction": {"prediction_id": "9c27f85b-2d00-42e1-a801-37b169c2f59a"},
        "model_evidence": {
            "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
            "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
            "calibration_status": "PRECALIBRATION_SHRINKAGE",
            "probability_publishable": True,
            "can_execute": False,
        },
        "objective_lanes": {
            "MODEL": {
                "status": "PASS",
                "probability_publishable": True,
                "can_execute": False,
            }
        },
        "can_execute": False,
    }
    passed, code, prediction_id = _is_model_path_pass(_response(body))
    assert passed is True
    assert code == "PROP_MODEL_PATH_PASS"
    assert prediction_id == "9c27f85b-2d00-42e1-a801-37b169c2f59a"


def test_acceptance_validator_rejects_wrong_provider_or_execution_leak():
    body = {
        "ok": True,
        "prediction": {"prediction_id": "9c27f85b-2d00-42e1-a801-37b169c2f59a"},
        "model_evidence": {
            "provider_identity": "LLP",
            "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
            "calibration_status": "PRECALIBRATION_SHRINKAGE",
            "probability_publishable": True,
            "can_execute": False,
        },
        "objective_lanes": {
            "MODEL": {
                "status": "PASS",
                "probability_publishable": True,
                "can_execute": False,
            }
        },
        "can_execute": True,
    }
    passed, _, _ = _is_model_path_pass(_response(body))
    assert passed is False


def test_snapshot_payload_is_probability_only_and_server_route_owned():
    payload = _snapshot_payload({
        "event_id": "MLB-2026-08-29-PHI-LAA",
        "event_start_time": "2026-08-30T02:07:00+00:00",
        "sport": "MLB",
        "player": "Cristopher Sánchez",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "source_snapshot_id": "1fb2b3e1-4ae5-4f41-9a4a-4b70ab401879",
    })
    assert payload == {
        "event_id": "MLB-2026-08-29-PHI-LAA",
        "event_start_time": "2026-08-30T02:07:00+00:00",
        "sport": "MLB",
        "player": "Cristopher Sánchez",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "direction": "MORE",
        "source_snapshot_id": "1fb2b3e1-4ae5-4f41-9a4a-4b70ab401879",
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }
    assert "stake" not in payload
    assert "order" not in payload
