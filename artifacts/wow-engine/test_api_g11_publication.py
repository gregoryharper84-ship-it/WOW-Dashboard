from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import uuid

from fastapi.testclient import TestClient

import api_g11


# Keep one suite-wide G11 test credential. api._require_action_api_key reads the
# process environment at request time, so competing module-level values would
# make otherwise-independent tests fail based on collection/import order.
TEST_KEY = "test-g11-action-key"
os.environ["WOW_ACTION_API_KEY"] = TEST_KEY
AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
client = TestClient(api_g11.app)


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def rpc(self, name, params):
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.payload))


def _request_payload():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "research_run_id": "g11-publication-regression",
        "requested_slate_date": start.date().isoformat(),
        "requested_timezone": "America/Chicago",
        "scan_stage": "PREGAME",
        "event_key": "MLB:G11:PUBLISH",
        "official_event_id": "999998",
        "event_start_time_utc": start.isoformat(),
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "venue": "Test Park",
        "home_starting_pitcher": "Home Pitcher",
        "away_starting_pitcher": "Away Pitcher",
        "home_starter_status": "PROBABLE",
        "away_starter_status": "PROBABLE",
        "home_lineup_status": "CONFIRMED",
        "away_lineup_status": "CONFIRMED",
        "source_snapshot_id": str(uuid.uuid4()),
    }


def _held_payload():
    return {
        "status": "MODEL_SCORED_HELD",
        "code": "REAL_FITTED_MODEL_PATH_PROVEN",
        "scoring_evidence_produced": True,
        "probability_fields_withheld": True,
        "probability_publishable": False,
        "can_execute": False,
        "current_publication_blockers": ["PUBLICATION_NOT_RATIFIED"],
        "score_time_blockers": ["CALIBRATION_HEALTH_FORWARD_EVIDENCE_PENDING"],
    }


def _published_payload():
    return {
        "status": "MODEL_SCORED_PUBLISHABLE",
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "scoring_evidence_produced": True,
        "probability_fields_withheld": False,
        "probability_publishable": True,
        "can_execute": False,
        "raw_home_probability": 0.57,
        "raw_away_probability": 0.43,
        "calibrated_home_probability": 0.55,
        "calibrated_away_probability": 0.45,
        "calibrated_home_lower_bound": 0.50,
        "calibrated_home_upper_bound": 0.60,
        "calibrated_away_lower_bound": 0.40,
        "calibrated_away_upper_bound": 0.50,
        "projected_runs_home": 4.8,
        "projected_runs_away": 4.1,
        "tie_after_9_probability": 0.08,
        "current_publication_blockers": [],
        "score_time_blockers": ["CALIBRATION_HEALTH_FORWARD_EVIDENCE_PENDING"],
    }


def test_held_mode_stays_numeric_free(monkeypatch):
    monkeypatch.setattr(api_g11, "get_client", lambda: _FakeClient(_held_payload()))
    response = client.post("/score-event", json=_request_payload(), headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "REAL_FITTED_MODEL_PATH_PROVEN"
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert not api_g11._NUMERIC_PROBABILITY_FIELDS.intersection(body)


def test_ratified_publishable_mode_returns_validated_probability_but_never_execution(monkeypatch):
    monkeypatch.setattr(api_g11, "get_client", lambda: _FakeClient(_published_payload()))
    response = client.post("/score-event", json=_request_payload(), headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "GOVERNED_PROBABILITY_PUBLISHED"
    assert body["probability_publishable"] is True
    assert body["probability_fields_withheld"] is False
    assert body["can_execute"] is False
    assert body["calibrated_home_probability"] == 0.55


def test_publishable_mode_fails_closed_when_numeric_contract_is_incomplete(monkeypatch):
    payload = _published_payload()
    del payload["calibrated_home_lower_bound"]
    monkeypatch.setattr(api_g11, "get_client", lambda: _FakeClient(payload))
    response = client.post("/score-event", json=_request_payload(), headers=AUTH)
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "PUBLISHED_PROBABILITY_VALIDATION_FAILED"
    assert "calibrated_home_lower_bound" in detail["missing_fields"]
    assert detail["can_execute"] is False


def test_publishable_mode_fails_closed_on_probability_normalization_error(monkeypatch):
    payload = _published_payload()
    payload["raw_away_probability"] = 0.50
    monkeypatch.setattr(api_g11, "get_client", lambda: _FakeClient(payload))
    response = client.post("/score-event", json=_request_payload(), headers=AUTH)
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "PUBLISHED_PROBABILITY_VALIDATION_FAILED"
    assert "raw_probability_sum" in detail["invalid_fields"]


def test_governance_surfaces_all_independent_publication_latches(monkeypatch):
    gate_state = {
        "governed_probability_capability": "UNAVAILABLE",
        "governed_probability_status": "NOT_PRODUCED",
        "deployment_contract_status": "PASS",
        "calibration_health_status": "BLOCKED",
        "runtime_capability_status": "UNAVAILABLE",
        "ratification_status": "NOT_RATIFIED",
        "production_feature_ready": False,
        "probability_publishable": False,
        "deployment_gates": [{"id": "G01", "status": "PASS"}],
    }
    monkeypatch.setattr(api_g11, "get_client", lambda: _FakeClient(gate_state))
    monkeypatch.setattr(api_g11.base_api, "_query_calibration_health", lambda: {"calibration_health_status": "BLOCKED"})
    response = client.get("/governance")
    assert response.status_code == 200
    body = response.json()
    assert body["deployment_contract_status"] == "PASS"
    assert body["calibration_health_status"] == "BLOCKED"
    assert body["runtime_capability_status"] == "UNAVAILABLE"
    assert body["ratification_status"] == "NOT_RATIFIED"
    assert body["production_feature_ready"] is False
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
