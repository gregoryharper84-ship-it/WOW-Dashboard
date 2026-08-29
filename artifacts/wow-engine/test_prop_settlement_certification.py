from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api_prod_market_acceptance as api


PREDICTION_ID = "11111111-1111-4111-8111-111111111111"


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class FakeClient:
    def __init__(self, prediction, outcomes=None):
        self.prediction = prediction
        self.outcomes = outcomes or []

    def table(self, name):
        if name == "wow_predictions":
            return FakeQuery([self.prediction] if self.prediction is not None else [])
        if name == "wow_outcomes":
            return FakeQuery(self.outcomes)
        raise AssertionError(f"unexpected table: {name}")


def _prediction(direction="MORE", line=4.0):
    return {
        "prediction_id": PREDICTION_ID,
        "event_id": "WOW:SETTLEMENT:FIXTURE",
        "event_start_time": "2026-08-30T00:00:00+00:00",
        "sport": "MLB",
        "stat_type": "STRIKEOUTS",
        "line": line,
        "direction": direction,
        "source_snapshot_id": "22222222-2222-4222-8222-222222222222",
    }


def test_derive_more_less_and_push_math():
    assert api._derive_prop_settlement("MORE", 4.0, 5.0)["hit"] is True
    assert api._derive_prop_settlement("MORE", 4.0, 3.0)["hit"] is False
    assert api._derive_prop_settlement("LESS", 4.0, 3.0)["hit"] is True
    assert api._derive_prop_settlement("LESS", 4.0, 5.0)["hit"] is False

    push_more = api._derive_prop_settlement("MORE", 4.0, 4.0)
    push_less = api._derive_prop_settlement("LESS", 4.0, 4.0)
    assert push_more["push"] is True and push_more["hit"] is None
    assert push_less["push"] is True and push_less["hit"] is None


def test_invalid_direction_fails_closed():
    with pytest.raises(Exception) as exc:
        api._derive_prop_settlement("OVER", 4.0, 5.0)
    assert getattr(exc.value, "status_code", None) == 409


def test_settle_http_derives_more_hit_and_never_accepts_caller_hit(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "settlement-test-key")
    monkeypatch.setattr(api.market_api.prod, "get_client", lambda: FakeClient(_prediction("MORE", 4.0)))

    writes = []

    def fake_record_outcome(prediction_id, **fields):
        writes.append((prediction_id, fields))
        return {"outcome_id": "33333333-3333-4333-8333-333333333333", "prediction_id": prediction_id, **fields}

    monkeypatch.setattr(api, "record_outcome", fake_record_outcome)
    client = TestClient(api.app)
    response = client.post(
        "/settle",
        params={
            "prediction_id": PREDICTION_ID,
            "official_result": "OFFICIAL_BOX_SCORE",
            "actual_stat": 5,
            "hit": "false",
        },
        headers={"Authorization": "Bearer settlement-test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["settlement_math"] == "PROVEN_BACKEND_DERIVED"
    assert body["direction"] == "MORE"
    assert body["line"] == 4.0
    assert body["actual_stat"] == 5.0
    assert body["hit"] is True
    assert body["push"] is False
    assert body["can_execute"] is False
    assert len(writes) == 1
    assert writes[0][1]["hit"] is True
    assert writes[0][1]["push"] is False


def test_settle_http_derives_less_hit(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "settlement-test-key")
    monkeypatch.setattr(api.market_api.prod, "get_client", lambda: FakeClient(_prediction("LESS", 4.0)))
    monkeypatch.setattr(
        api,
        "record_outcome",
        lambda prediction_id, **fields: {"prediction_id": prediction_id, **fields},
    )

    response = TestClient(api.app).post(
        "/settle",
        params={
            "prediction_id": PREDICTION_ID,
            "official_result": "OFFICIAL_BOX_SCORE",
            "actual_stat": 3,
        },
        headers={"Authorization": "Bearer settlement-test-key"},
    )
    assert response.status_code == 200
    assert response.json()["hit"] is True
    assert response.json()["push"] is False


def test_settle_http_persists_push_as_non_hit(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "settlement-test-key")
    monkeypatch.setattr(api.market_api.prod, "get_client", lambda: FakeClient(_prediction("MORE", 4.0)))
    captured = {}

    def fake_record_outcome(prediction_id, **fields):
        captured.update(fields)
        return {"prediction_id": prediction_id, **fields}

    monkeypatch.setattr(api, "record_outcome", fake_record_outcome)
    response = TestClient(api.app).post(
        "/settle",
        params={
            "prediction_id": PREDICTION_ID,
            "official_result": "OFFICIAL_BOX_SCORE",
            "actual_stat": 4,
        },
        headers={"Authorization": "Bearer settlement-test-key"},
    )
    assert response.status_code == 200
    assert response.json()["push"] is True
    assert response.json()["hit"] is None
    assert captured["push"] is True
    assert captured["hit"] is None


def test_settle_missing_prediction_and_duplicate_fail_closed(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "settlement-test-key")
    client = TestClient(api.app)

    monkeypatch.setattr(api.market_api.prod, "get_client", lambda: FakeClient(None))
    missing = client.post(
        "/settle",
        params={
            "prediction_id": PREDICTION_ID,
            "official_result": "OFFICIAL_BOX_SCORE",
            "actual_stat": 4,
        },
        headers={"Authorization": "Bearer settlement-test-key"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PROP_PREDICTION_NOT_FOUND"

    monkeypatch.setattr(
        api.market_api.prod,
        "get_client",
        lambda: FakeClient(_prediction(), outcomes=[{"outcome_id": "already", "prediction_id": PREDICTION_ID}]),
    )
    duplicate = client.post(
        "/settle",
        params={
            "prediction_id": PREDICTION_ID,
            "official_result": "OFFICIAL_BOX_SCORE",
            "actual_stat": 4,
        },
        headers={"Authorization": "Bearer settlement-test-key"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "PROP_PREDICTION_ALREADY_SETTLED"
