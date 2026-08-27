from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api


VALID_KEY = "score-event-test-key"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", VALID_KEY)
    return TestClient(api.app)


@pytest.fixture
def valid_payload():
    return {
        "research_run_id": "routing-validation-mlb-001",
        "requested_slate_date": "2099-08-27",
        "requested_timezone": "America/Chicago",
        "scan_stage": "PREGAME",
        "event_key": "MLB:2099-08-27:AWAY@HOME",
        "official_event_id": "mlb-fixture-20990827-001",
        "event_start_time_utc": "2099-08-28T00:10:00Z",
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "home_team": "HOME",
        "away_team": "AWAY",
        "venue": "Fixture Park",
        "home_starting_pitcher": "Home Starter",
        "away_starting_pitcher": "Away Starter",
        "home_starter_status": "CONFIRMED",
        "away_starter_status": "CONFIRMED",
        "home_lineup_status": "CONFIRMED",
        "away_lineup_status": "CONFIRMED",
        "latest_material_update_timestamp": "2099-08-27T22:00:00Z",
        "source_snapshot_id": "11111111-1111-4111-8111-111111111111",
        "market_prior": {
            "home_probability": 0.54,
            "away_probability": 0.46,
            "timestamp": "2099-08-27T22:05:00Z",
            "quality": "EXACT_TWO_SIDED_MULTI_SOURCE",
            "source": "fixture",
        },
    }


def auth_header(value: str = VALID_KEY):
    return {"Authorization": f"Bearer {value}"}


def test_score_event_requires_auth(valid_payload, monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", VALID_KEY)
    client = TestClient(api.app)
    response = client.post("/score-event", json=valid_payload)
    assert response.status_code == 401


def test_score_event_rejects_wrong_auth(client, valid_payload):
    response = client.post(
        "/score-event",
        headers=auth_header("wrong-key"),
        json=valid_payload,
    )
    assert response.status_code == 401
    assert VALID_KEY not in response.text
    assert "wrong-key" not in response.text


def test_score_event_valid_mlb_contract_fails_closed_409(client, valid_payload, monkeypatch):
    persist_calls = []

    def unexpected_persist(*args, **kwargs):
        persist_calls.append((args, kwargs))
        raise AssertionError("/score-event v1 must not persist while the fitted MLB model is unavailable")

    monkeypatch.setattr(api, "_persist_fn", unexpected_persist)

    response = client.post(
        "/score-event",
        headers=auth_header(),
        json=valid_payload,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "GOVERNED_EVENT_MODEL_UNAVAILABLE"
    assert detail["sport"] == "MLB"
    assert detail["market_family"] == "OUTRIGHT_WINNER"
    assert detail["controlling_specialist"] == "wow.mlb-game-win-probability-expert"
    assert detail["governed_probability_capability"] == "UNAVAILABLE"
    assert detail["governed_probability_status"] == "NOT_PRODUCED"
    assert detail["probability_publishable"] is False
    assert detail["fallback"] == "SECTION_8A_MANUAL_ESTIMATE_LANE"
    assert detail["can_execute"] is False
    assert "MLB_FITTED_MODEL_ARTIFACT_UNAVAILABLE" in detail["blockers"]
    assert "MLB_EVENT_CALIBRATOR_UNAVAILABLE" in detail["blockers"]
    assert persist_calls == []

    forbidden_probability_keys = {
        "raw_probability",
        "raw_model_probability",
        "raw_home_probability",
        "raw_away_probability",
        "calibrated_probability",
        "calibrated_home_probability",
        "calibrated_away_probability",
        "lower_bound",
        "upper_bound",
        "model_probability",
    }
    assert forbidden_probability_keys.isdisjoint(detail.keys())


@pytest.mark.parametrize(
    ("field", "value", "expected_fragment"),
    [
        ("sport", "WNBA", "sport must be MLB"),
        ("league", "NCAA", "league must be MLB"),
        ("market_family", "PLAYER_PROP", "market_family must be OUTRIGHT_WINNER"),
        ("settlement_basis", "REGULATION_ONLY", "FULL_GAME_INCLUDING_EXTRA_INNINGS"),
        ("scan_stage", "LIVE", "scan_stage must be PREGAME"),
    ],
)
def test_score_event_rejects_unsupported_contract_shape(
    client, valid_payload, field, value, expected_fragment
):
    payload = dict(valid_payload)
    payload[field] = value
    response = client.post("/score-event", headers=auth_header(), json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "EVENT_CONTRACT_INVALID"
    assert any(expected_fragment in item for item in detail["errors"])
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_score_event_rejects_same_team_identity(client, valid_payload):
    payload = dict(valid_payload)
    payload["away_team"] = payload["home_team"]
    response = client.post("/score-event", headers=auth_header(), json=payload)
    assert response.status_code == 422
    assert any(
        "home_team and away_team must differ" in item
        for item in response.json()["detail"]["errors"]
    )


def test_score_event_rejects_started_event(client, valid_payload):
    payload = dict(valid_payload)
    payload["requested_slate_date"] = "2020-01-01"
    payload["event_start_time_utc"] = "2020-01-01T20:00:00Z"
    response = client.post("/score-event", headers=auth_header(), json=payload)
    assert response.status_code == 422
    assert any(
        "event has already started" in item
        for item in response.json()["detail"]["errors"]
    )


def test_score_event_rejects_non_uuid_snapshot(client, valid_payload):
    payload = dict(valid_payload)
    payload["source_snapshot_id"] = "not-a-uuid"
    response = client.post("/score-event", headers=auth_header(), json=payload)
    assert response.status_code == 422
    assert any(
        "source_snapshot_id must be a UUID" in item
        for item in response.json()["detail"]["errors"]
    )


def test_score_event_rejects_unnormalized_market_prior(client, valid_payload):
    payload = dict(valid_payload)
    payload["market_prior"] = dict(valid_payload["market_prior"])
    payload["market_prior"]["home_probability"] = 0.60
    payload["market_prior"]["away_probability"] = 0.45
    response = client.post("/score-event", headers=auth_header(), json=payload)
    assert response.status_code == 422
    assert any(
        "market_prior home+away probabilities must normalize to 1" in item
        for item in response.json()["detail"]["errors"]
    )


def test_score_event_request_has_no_client_probability_override_fields():
    fields = set(api.ScoreEventRequest.model_fields)
    prohibited = {
        "controlling_specialist",
        "model_version",
        "model_artifact_id",
        "simulation_count",
        "raw_home_probability",
        "raw_away_probability",
        "calibrated_home_probability",
        "calibrated_away_probability",
        "probability_publishable",
        "event_decision",
        "can_execute",
    }
    assert fields.isdisjoint(prohibited)


def test_event_schema_is_additive_and_fail_closed():
    schema = (Path(__file__).parent / "event_schema.sql").read_text()
    assert "create table if not exists wow_event_predictions" in schema.lower()
    assert "create table if not exists wow_event_outcomes" in schema.lower()
    assert "references wow_event_predictions(event_prediction_id)" in schema
    assert "direction in ('MORE','LESS')" not in schema
    assert "check (can_execute = false)" in schema
    assert "simulation_count >= 50000" in schema
    assert "abs(raw_home_probability + raw_away_probability - 1.0) <= 0.000001" in schema
    assert "abs(calibrated_home_probability + calibrated_away_probability - 1.0) <= 0.000001" in schema
    assert "alter table wow_event_predictions enable row level security" in schema.lower()
    assert "alter table wow_event_outcomes enable row level security" in schema.lower()


def test_existing_prop_and_settlement_auth_remain_protected(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", VALID_KEY)
    client = TestClient(api.app)
    assert client.post("/score-prop", json={}).status_code == 401
    assert client.post("/settle?prediction_id=x&official_result=y&actual_stat=1&hit=true").status_code == 401
