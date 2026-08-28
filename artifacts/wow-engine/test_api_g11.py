from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import uuid

from fastapi.testclient import TestClient

import api_g11


TEST_KEY = "test-g11-action-key"
os.environ["WOW_ACTION_API_KEY"] = TEST_KEY
AUTH = {"Authorization": f"Bearer {TEST_KEY}"}
client = TestClient(api_g11.app)


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.rpc_name = None
        self.rpc_params = None

    def rpc(self, name, params):
        self.rpc_name = name
        self.rpc_params = params
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.payload))


def _request_payload():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return {
        "research_run_id": "g11-regression",
        "requested_slate_date": start.date().isoformat(),
        "requested_timezone": "America/Chicago",
        "scan_stage": "PREGAME",
        "event_key": "MLB:G11:TEST",
        "official_event_id": "999999",
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
        "home_lineup_status": "PROJECTED",
        "away_lineup_status": "PROJECTED",
        "source_snapshot_id": str(uuid.uuid4()),
    }


def _held_payload():
    return {
        "status": "MODEL_SCORED_HELD",
        "code": "REAL_FITTED_MODEL_PATH_PROVEN",
        "controlling_specialist": "wow.mlb-game-win-probability-expert",
        "shadow_event_id": str(uuid.uuid4()),
        "score_snapshot_id": str(uuid.uuid4()),
        "spec_id": str(uuid.uuid4()),
        "server_snapshot_id": str(uuid.uuid4()),
        "server_snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
        "requested_source_snapshot_id": str(uuid.uuid4()),
        "model_timestamp": datetime.now(timezone.utc).isoformat(),
        "model_version": "MLB_V2C_SHARED_NB_2024_R1",
        "training_data_sha256": "a" * 64,
        "score_status": "SHADOW_SCORED_LINEUP_PENDING",
        "feature_hydration_status": "PASS",
        "lineup_status": "NOT_YET_AVAILABLE",
        "calibration_health_status": "BLOCKED",
        "calibration_blockers": [
            "FORWARD_SHADOW_NOT_COMPLETED",
            "FRESH_POST_FREEZE_OUTCOME_HOLDOUT_UNAVAILABLE",
        ],
        "governed_probability_capability": "UNAVAILABLE",
        "blockers": ["CALIBRATION_HEALTH_FORWARD_EVIDENCE_PENDING"],
        "scoring_evidence_produced": True,
        "probability_fields_withheld": True,
        "probability_publishable": False,
        "can_execute": False,
    }


def test_g11_real_fitted_path_returns_held_metadata_without_probability_values(monkeypatch):
    fake = _FakeClient(_held_payload())
    monkeypatch.setattr(api_g11, "get_client", lambda: fake)

    response = client.post("/score-event", json=_request_payload(), headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["code"] == "REAL_FITTED_MODEL_PATH_PROVEN"
    assert body["scoring_evidence_produced"] is True
    assert body["probability_fields_withheld"] is True
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert not api_g11._NUMERIC_PROBABILITY_FIELDS.intersection(body)
    assert fake.rpc_name == "wow_mlb_score_event_bridge"


def test_g11_bridge_block_stays_fail_closed(monkeypatch):
    fake = _FakeClient({
        "status": "BLOCKED",
        "code": "EVENT_IDENTITY_MISMATCH",
        "scoring_evidence_produced": False,
        "probability_publishable": False,
        "can_execute": False,
    })
    monkeypatch.setattr(api_g11, "get_client", lambda: fake)

    response = client.post("/score-event", json=_request_payload(), headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EVENT_IDENTITY_MISMATCH"


def test_g11_held_probability_leak_is_blocked(monkeypatch):
    payload = _held_payload()
    payload["calibrated_home_probability"] = 0.61
    fake = _FakeClient(payload)
    monkeypatch.setattr(api_g11, "get_client", lambda: fake)

    response = client.post("/score-event", json=_request_payload(), headers=AUTH)
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "HELD_PROBABILITY_LEAK_BLOCKED"
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_g11_invalid_contract_rejected_before_bridge(monkeypatch):
    fake = _FakeClient(_held_payload())
    monkeypatch.setattr(api_g11, "get_client", lambda: fake)
    request = _request_payload()
    request["market_family"] = "NOT_OUTRIGHT"

    response = client.post("/score-event", json=request, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EVENT_CONTRACT_INVALID"
    assert fake.rpc_name is None
