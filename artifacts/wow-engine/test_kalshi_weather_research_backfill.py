"""Offline acceptance for bounded Miami settlement recovery and HTTP authority."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api_kalshi_weather_v2 as api
from kalshi_weather_v2.research_backfill import BackfillDataError, reconcile_miami_settlements
from kalshi_weather_v2.runtime import KalshiWeatherRuntimeError


class Query:
    def __init__(self, rows, *, broken=False, filters=None):
        self.rows = rows
        self.broken = broken
        self.filters = filters or []
    def select(self, *_args):
        return self
    def order(self, *_args, **_kwargs):
        return self
    def limit(self, *_args):
        return self
    def eq(self, key, value):
        self.filters.append((key, value))
        return self
    def execute(self):
        if self.broken:
            raise RuntimeError("SENSITIVE_INTERNAL_DB_ERROR")
        rows = [x for x in self.rows if all(x.get(k) == v for k, v in self.filters)]
        return SimpleNamespace(data=rows)


class Client:
    def __init__(self, predictions, *, broken=False):
        self.predictions = predictions
        self.outcomes = []
        self.broken = broken
    def table(self, name):
        return Query(self.predictions if name.endswith("predictions") else self.outcomes, broken=self.broken)


TARGET = "2026-10-30T18:00:00Z"
CLOCK = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)


def prediction(pid, *, decision="2026-10-30T16:00:00Z", city="MIAMI", lane="HOURLY_TEMPERATURE"):
    return {
        "prediction_id": pid, "rule_snapshot_id": "rule-x", "decision_time": decision,
        "model_payload": {"contract": {
            "lane": lane,
            "settlement_location_code": "KALSHI_WEATHER_INDEX:" + city,
            "observation_window": "POINT_IN_TIME:" + TARGET,
        }},
    }


def test_recovery_retries_do_not_duplicate_outcomes_and_no_probability():
    db = Client([prediction("p1"), prediction("p2"), prediction("p3"), prediction("p4")])
    def settle(*, client, prediction_id, index_city, settled_at):
        assert index_city == "miami"
        assert settled_at.endswith("Z")
        client.outcomes.append({"prediction_id": prediction_id})
        return {"status": "SETTLED", "outcome_id": "outcome-" + prediction_id, "target_time_utc": TARGET}

    first = reconcile_miami_settlements(client=db, limit=2, now=CLOCK, settle=settle)
    second = reconcile_miami_settlements(client=db, limit=2, now=CLOCK, settle=settle)
    third = reconcile_miami_settlements(client=db, limit=2, now=CLOCK, settle=settle)
    assert (first["new_outcomes"], second["new_outcomes"], third["new_outcomes"]) == (2, 2, 0)
    assert len(db.outcomes) == 4
    assert first["model_p_yes"] is None and first["can_execute"] is False
    assert all(x["status_gate"] == "NO_PLAY_DATA_INSUFFICIENT" for x in (first, second, third))


def test_rejects_hindsight_city_mismatch_and_bad_dates():
    db = Client([
        prediction("future-leak", decision="2026-10-31T19:00:00Z"),
        prediction("wrong-station", city="CHICAGO"),
        prediction("wrong-lane", lane="DAILY_HIGH_TEMPERATURE"),
        {**prediction("bad-window"), "model_payload": {"contract": {
            "lane": "HOURLY_TEMPERATURE", "settlement_location_code": "KALSHI_WEATHER_INDEX:MIAMI",
            "observation_window": "POINT_IN_TIME:bad"
        }}},
    ])
    result = reconcile_miami_settlements(client=db, now=CLOCK)
    assert result["attempted"] == 0 and result["new_outcomes"] == 0
    assert result["rejected_bad_provenance"] == 2


def test_unavailable_index_is_gap_not_guess():
    db = Client([prediction("p1")])
    def unavailable(**_kwargs):
        raise KalshiWeatherRuntimeError("SETTLEMENT_POINT_UNAVAILABLE", ("NO_DATA",))
    result = reconcile_miami_settlements(client=db, now=CLOCK, settle=unavailable)
    assert result["unavailable_official_points"][0]["reason"] == "SETTLEMENT_POINT_UNAVAILABLE"
    assert result["new_outcomes"] == 0 and result["status_gate"] == "NO_PLAY_DATA_INSUFFICIENT"


def test_failed_ledger_read_fails_closed():
    with pytest.raises(BackfillDataError, match="PREDICTION_LEDGER_UNAVAILABLE"):
        reconcile_miami_settlements(client=Client([prediction("p")], broken=True), now=CLOCK)


def test_endpoint_requires_distinct_worker_key(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY", "worker-secret")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY", "launch-secret")
    monkeypatch.setattr(api, "get_client", lambda: Client([prediction("p")]))
    monkeypatch.setattr(api, "reconcile_miami_settlements", lambda **_kw: {
        "status": "NO_NEW_CANONICAL_SETTLEMENT", "probability_publishable": False, "can_execute": False,
    })
    client = TestClient(api.app)
    endpoint = "/internal/research/miami-settlement-reconcile"
    assert client.post(endpoint).status_code == 401
    assert client.post(endpoint, json={"scope": "miami"}, headers={"Authorization": "Bearer launch-secret"}).status_code == 401
    ok = client.post(endpoint, json={"scope": "miami"}, headers={"Authorization": "Bearer worker-secret"})
    assert ok.status_code == 200 and ok.json()["can_execute"] is False
    assert client.post("/research/backfill", json={"scope": "miami"}, headers={"Authorization": "Bearer worker-secret"}).status_code == 401


def test_launch_requires_credentials_and_explicit_trigger_environment(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY", "launch-secret")
    monkeypatch.delenv("WOW_KALSHI_WEATHER_TRIGGER_SECRET_KEY", raising=False)
    client = TestClient(api.app)
    endpoint = "/research/backfill"
    assert client.post(endpoint, json={"scope": "miami"}).status_code == 401
    assert client.post(endpoint, json={"scope": "chicago"},
        headers={"Authorization": "Bearer launch-secret"}).status_code == 422
    denied = client.post(endpoint, json={"scope": "miami"},
        headers={"Authorization": "Bearer launch-secret"})
    assert denied.status_code == 503
    assert "TRIGGER" in str(denied.json())


def test_launch_returns_only_trigger_receipt(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY", "launch-secret")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_TRIGGER_SECRET_KEY", "prod-trigger-secret")
    class Response:
        status_code = 200
        def json(self): return {"id": "run_0123456789abcdef"}
    def fake_post(url, **kwargs):
        assert url == "https://api.trigger.dev/api/v1/tasks/weather-research-backfill/trigger"
        assert kwargs["headers"]["Authorization"] == "Bearer prod-trigger-secret"
        assert kwargs["json"]["payload"] == {"scope": "miami"}
        return Response()
    monkeypatch.setattr(api.httpx, "post", fake_post)
    result = TestClient(api.app).post("/research/backfill", json={"scope": "miami"},
        headers={"Authorization": "Bearer launch-secret"})
    assert result.status_code == 202
    assert result.json()["run_id"] == "run_0123456789abcdef"
    assert result.json()["can_execute"] is False


def test_rotating_progress_does_not_starve_later_good_outcomes():
    from datetime import timedelta
    db = Client([prediction("missing-a"), prediction("missing-b"),
                 prediction("missing-c"), prediction("available")])
    settled_ids = []
    def settle(*, client, prediction_id, index_city, settled_at):
        if prediction_id.startswith("missing"):
            raise KalshiWeatherRuntimeError("SETTLEMENT_POINT_UNAVAILABLE", ("NO_DATA",))
        client.outcomes.append({"prediction_id": prediction_id})
        settled_ids.append(prediction_id)
        return {"status": "SETTLED", "outcome_id": "outcome-available", "target_time_utc": TARGET}

    # Each five-minute window uses a different deterministic start offset.
    for i in range(4):
        reconcile_miami_settlements(client=db, limit=3, now=CLOCK + timedelta(minutes=5*i), settle=settle)
    assert settled_ids == ["available"]



def test_periodic_thirty_minute_launches_do_not_alias_a_three_of_nine_slice():
    from datetime import timedelta
    db = Client([prediction(f"missing-{i}") for i in range(8)] + [prediction("zz-available")])
    receipts = []
    offsets = []
    def settle(*, client, prediction_id, index_city, settled_at):
        if prediction_id != "zz-available":
            raise KalshiWeatherRuntimeError("SETTLEMENT_POINT_UNAVAILABLE", ("NO_DATA",))
        client.outcomes.append({"prediction_id": prediction_id})
        receipts.append(prediction_id)
        return {"status": "SETTLED", "outcome_id": "outcome-available",
                "target_time_utc": TARGET}

    for i in range(12):
        result = reconcile_miami_settlements(
            client=db, limit=3, now=CLOCK + timedelta(minutes=30*i), settle=settle,
        )
        offsets.append(result["rotation_offset"])
        assert result["attempted"] <= 3
        assert result["can_execute"] is False
        assert result["probability_publishable"] is False
    assert len(set(offsets)) > 1
    assert receipts == ["zz-available"]


def test_receipt_cannot_claim_another_settlement_hour():
    db = Client([prediction("p1")])
    def bad_settle(**_kwargs):
        return {"status": "SETTLED", "outcome_id": "out-1", "target_time_utc": "2026-10-30T19:00:00Z"}
    with pytest.raises(BackfillDataError, match="SETTLEMENT_RECEIPT_INVALID"):
        reconcile_miami_settlements(client=db, now=CLOCK, settle=bad_settle)
