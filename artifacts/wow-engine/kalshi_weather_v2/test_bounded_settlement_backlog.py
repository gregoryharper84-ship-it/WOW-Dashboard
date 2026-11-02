"""Contract tests for bounded, idempotent settlement backlog draining.

Settlement is a separate memory workload from capture: it must be hard-capped,
must never re-settle a surviving outcome, and must report what remains.
"""
from __future__ import annotations

from types import SimpleNamespace

import kalshi_weather_v2.empirical_runtime as runtime
import kalshi_weather_v2.shadow_cohort as shadow
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = "2026-11-02T12:00:00Z"
PATH = "/internal/kalshi-weather/v17/settlement-backlog/run"


class _Response:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def order(self, column, desc=False):
        self._rows = sorted(
            self._rows, key=lambda row: str(row.get(column) or ""), reverse=desc
        )
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        return _Response(self._rows[:getattr(self, "_limit", len(self._rows))])


class _Client:
    def __init__(self, tables=None):
        self._tables = tables or {}

    def table(self, name):
        return _Table(self._tables.get(name, []))


def _prediction(
    prediction_id,
    target_time,
    *,
    lane="HOURLY_TEMPERATURE",
    prefix="POINT_IN_TIME:",
    station="KALSHI_WEATHER_INDEX:miami",
):
    return {
        "prediction_id": prediction_id,
        "model_payload": {
            "contract": {
                "lane": lane,
                "observation_window": f"{prefix}{target_time}",
                "settlement_location_code": station,
            }
        },
    }


def _run(monkeypatch, tables, *, max_attempts=10, settle=None):
    monkeypatch.setattr(
        shadow, "ReadOnlyJsonClient", lambda: SimpleNamespace(close=lambda: None)
    )
    calls = []

    def fake_settle(**kwargs):
        calls.append(kwargs)
        if settle is not None:
            settle(kwargs)

    monkeypatch.setattr(shadow, "settle_hourly_prediction", fake_settle)
    result = shadow.run_bounded_settlement_backlog_once(
        client=_Client(tables), now=NOW, max_attempts=max_attempts
    )
    return result, calls


def test_backlog_is_hard_capped_and_reports_remaining(monkeypatch):
    tables = {
        "wow_kalshi_weather_predictions": [
            _prediction("p1", "2026-11-02T08:00:00Z"),
            _prediction("p2", "2026-11-02T09:00:00Z"),
            _prediction("p3", "2026-11-02T10:00:00Z"),
        ]
    }
    result, calls = _run(monkeypatch, tables, max_attempts=2)

    assert result.pending_eligible == 3
    assert result.attempted == 2
    assert result.settled == 2
    assert result.remaining_after_run == 1
    assert result.truncated is True
    assert result.status == shadow.SETTLEMENT_BACKLOG_PARTIAL
    assert len(calls) == 2
    assert result.can_execute is False


def test_backlog_never_resettles_a_surviving_outcome(monkeypatch):
    tables = {
        "wow_kalshi_weather_predictions": [
            _prediction("p1", "2026-11-02T08:00:00Z"),
        ],
        "wow_kalshi_weather_outcomes": [{"prediction_id": "p1", "settled_value": 81.0}],
    }
    result, calls = _run(monkeypatch, tables, max_attempts=5)

    assert result.status == shadow.SETTLEMENT_BACKLOG_CLEARED
    assert result.pending_eligible == 0
    assert result.attempted == 0
    assert calls == []


def test_backlog_ignores_targets_inside_the_settlement_grace_window(monkeypatch):
    tables = {
        "wow_kalshi_weather_predictions": [
            _prediction("future", "2026-11-02T14:00:00Z"),
        ]
    }
    result, calls = _run(monkeypatch, tables, max_attempts=5)

    assert result.status == shadow.SETTLEMENT_BACKLOG_CLEARED
    assert result.pending_eligible == 0
    assert calls == []


def test_backlog_ignores_ineligible_contract_shapes(monkeypatch):
    tables = {
        "wow_kalshi_weather_predictions": [
            _prediction("daily", "2026-11-02T08:00:00Z", lane="DAILY_TEMPERATURE"),
            _prediction("typed", "2026-11-02T08:00:00Z", prefix="DAY:"),
            _prediction(
                "untyped",
                "2026-11-02T08:00:00Z",
                station="KALSHI_WEATHER_INDEX:",
            ),
        ]
    }
    result, calls = _run(monkeypatch, tables, max_attempts=5)

    assert result.status == shadow.SETTLEMENT_BACKLOG_CLEARED
    assert result.pending_eligible == 0
    assert calls == []


def test_backlog_failure_is_reported_not_counted_as_settlement(monkeypatch):
    tables = {
        "wow_kalshi_weather_predictions": [
            _prediction("p1", "2026-11-02T08:00:00Z"),
            _prediction("p2", "2026-11-02T09:00:00Z"),
        ]
    }

    def explode(_kwargs):
        raise TimeoutError("provider timeout")

    result, _calls = _run(monkeypatch, tables, max_attempts=5, settle=explode)

    assert result.status == shadow.SETTLEMENT_BACKLOG_FAILED
    assert result.attempted == 2
    assert result.settled == 0
    assert result.remaining_after_run == 2
    assert len(result.failures) == 2
    assert "TimeoutError" in result.failures[0]


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("WOW_KALSHI_WEATHER_COHORT_KEY", "test-key")
    app = FastAPI()
    runtime.install_empirical_cohort_scheduler(app, db_client_fn=lambda: _Client())
    return TestClient(app)


def test_backlog_endpoint_requires_the_cohort_key(monkeypatch):
    client = _client(monkeypatch)
    response = client.post(PATH, headers={"X-WOW-Weather-Cohort-Key": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == (
        "KALSHI_WEATHER_COHORT_TRIGGER_UNAUTHORIZED"
    )


def test_backlog_endpoint_is_disabled_by_default(monkeypatch):
    client = _client(monkeypatch)
    response = client.post(PATH, headers={"X-WOW-Weather-Cohort-Key": "test-key"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "KALSHI_WEATHER_SETTLEMENT_BACKLOG_DISABLED"
    )


def test_backlog_endpoint_reports_never_executes(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setenv("WOW_KALSHI_WEATHER_SETTLEMENT_BACKLOG_ENABLED", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_SETTLEMENT_BACKLOG_MAX_ATTEMPTS", "999")
    captured = {}

    def fake_backlog(**kwargs):
        captured.update(kwargs)
        return shadow.SettlementBacklogResult(
            status=shadow.SETTLEMENT_BACKLOG_CLEARED,
            pending_eligible=0,
            attempted=0,
            settled=0,
            remaining_after_run=0,
            truncated=False,
            failures=(),
        )

    monkeypatch.setattr(runtime, "run_bounded_settlement_backlog_once", fake_backlog)
    response = client.post(PATH, headers={"X-WOW-Weather-Cohort-Key": "test-key"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == shadow.SETTLEMENT_BACKLOG_CLEARED
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    # An unbounded environment value must still be clamped before the workload runs.
    assert captured["max_attempts"] == 25
