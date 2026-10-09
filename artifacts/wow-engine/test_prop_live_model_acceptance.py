from __future__ import annotations

import json

import httpx

from prop_live_model_acceptance import (
    _bootstrap_pick_payload,
    _expected_bootstrap_model_family,
    _is_model_path_pass,
    _snapshot_payload,
)


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
    passed, code, prediction_id, mode = _is_model_path_pass(_response(body))
    assert passed is True
    assert code == "PROP_MODEL_PATH_PASS"
    assert prediction_id == "9c27f85b-2d00-42e1-a801-37b169c2f59a"
    assert mode == "GOVERNED_PUBLISHABLE"


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
    passed, _, _, mode = _is_model_path_pass(_response(body))
    assert passed is False
    assert mode == "UNRECOGNIZED_200"


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


def test_bootstrap_request_id_is_stable_per_candidate_and_distinct_across_candidates():
    base = {
        "event_id": "2026_04_ARI_NYG",
        "event_start_time": "2099-10-04T17:00:00Z",
        "sport": "NFL",
        "player": "Marvin Harrison Jr.",
        "stat_type": "RECEIVING_YARDS",
        "line": 33.5,
        "direction": "MORE",
        "league": "NFL",
        "opponent": "NYG",
    }

    first = _bootstrap_pick_payload(json.dumps(base))
    semantic_retry = _bootstrap_pick_payload(json.dumps({
        **base,
        "event_start_time": "2099-10-04T17:00:00+00:00",
    }))
    different_candidate = _bootstrap_pick_payload(json.dumps({
        **base,
        "event_id": "2026_04_ARI_NYG_ALT",
    }))

    assert first["request_id"].startswith("wow-prop-live-e2e-acceptance-")
    assert first["request_id"] == semantic_retry["request_id"]
    assert first["request_id"] != different_candidate["request_id"]
    assert first["rows"][0]["row_key"] == "prop-live-e2e-acceptance"


def test_live_bootstrap_expected_nfl_model_family_matches_scout_player_yardage_aliases():
    for stat in ("PLAYER_PASSING_YARDS", "PLAYER_RUSHING_YARDS", "PLAYER_RECEIVING_YARDS"):
        payload = {"rows": [{"sport": "NFL", "stat_type": stat}]}
        assert _expected_bootstrap_model_family(payload) == "NFL_PROP_ROLLING_FITTED_V1"
        spaced = {"rows": [{"sport": "nfl", "stat_type": stat.lower().replace("_", " ")}]}
        assert _expected_bootstrap_model_family(spaced) == "NFL_PROP_ROLLING_FITTED_V1"

    # Do not grant model identity to another sport or unsupported stat.
    assert _expected_bootstrap_model_family(
        {"rows": [{"sport": "NCAAF", "stat_type": "PLAYER_RECEIVING_YARDS"}]}
    ) != "NFL_PROP_ROLLING_FITTED_V1"
    assert _expected_bootstrap_model_family(
        {"rows": [{"sport": "NFL", "stat_type": "PLAYER_RECEPTIONS"}]}
    ) != "NFL_PROP_ROLLING_FITTED_V1"
