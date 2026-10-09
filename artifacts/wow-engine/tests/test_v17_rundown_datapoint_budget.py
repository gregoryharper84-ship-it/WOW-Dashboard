import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from v17 import market_evidence_sources as sources
from v17 import rundown_datapoint_budget as budget
from v17 import rundown_market_ingestor as ingestor

DAY1 = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)
DAY2 = datetime(2026, 10, 10, 0, 5, tzinfo=timezone.utc)


class _Table:
    def __init__(self, store, name):
        self.store, self.name, self.key = store, name, None

    def select(self, *_a):
        return self

    def eq(self, _field, value):
        self.key = value
        return self

    def limit(self, *_a):
        return self

    def upsert(self, row, **_k):
        self.store[row["feed_key"]] = row
        self.key = None
        return self

    def execute(self):
        if self.key is None:
            return SimpleNamespace(data=[])
        row = self.store.get(self.key)
        return SimpleNamespace(data=[row] if row else [])


class _Client:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Table(self.store, name)


def test_default_limits_and_off(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", raising=False)
    monkeypatch.delenv("WOW_RUNDOWN_DAILY_CALL_BUDGET", raising=False)
    assert budget.datapoint_limit() == 6000 and budget.call_limit() == 300
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "off")
    assert budget.datapoint_limit() is None


def test_datapoint_limit_blocks_and_resets_next_utc_day(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "100")
    assert budget.check(DAY1) == (True, None)
    budget.record(60, DAY1)
    assert budget.check(DAY1) == (True, None)
    budget.record(45, DAY1)
    assert budget.check(DAY1) == (False, "PAID_PROVIDER_BUDGET_EXHAUSTED")
    assert budget.check(DAY2) == (True, None)


def test_call_cap_bounds_unmetered_responses(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_CALL_BUDGET", "2")
    budget.record(None, DAY1)
    budget.record(None, DAY1)
    assert budget.status(DAY1)["datapoints"] == 0
    assert budget.check(DAY1)[0] is False


def test_durable_total_is_shared_across_workers_and_restarts(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "100")
    store = {}
    budget.register_client(lambda: _Client(store))
    budget.record(70, DAY1)
    row = store[budget.feed_key("2026-10-09")]
    assert row["metadata"]["datapoints"] == 70 and row["can_execute"] is False

    budget._reset_for_tests()  # simulate a restart / second worker
    budget.register_client(lambda: _Client(store))
    budget.record(40, DAY1)
    assert store[budget.feed_key("2026-10-09")]["metadata"]["datapoints"] == 110
    assert budget.check(DAY1)[0] is False


def test_bookkeeping_db_failure_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("db down")

    budget.register_client(broken)
    assert budget.record(5, DAY1)["datapoints"] == 5
    assert budget.check(DAY1) == (True, None)


class _Resp:
    def __init__(self, payload, headers):
        self.payload, self.headers, self.status, self.code = payload, headers, 200, 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


@pytest.fixture
def _rundown_key(monkeypatch):
    monkeypatch.setenv("THERUNDOWN_API_KEY", "test-only")
    monkeypatch.setattr(sources, "ENABLED", True, raising=False)


def test_shared_fetch_records_header_datapoints_and_blocks_without_network(monkeypatch, _rundown_key):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "50")
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return _Resp({"sports": []}, {"X-Datapoints": "60"})

    first = sources.fetch("RUNDOWN", "sports", opener=opener)
    assert first.ok and budget.status()["datapoints"] == 60
    second = sources.fetch("RUNDOWN", "sports", opener=opener)
    assert second.ok is False and second.code == "PAID_PROVIDER_BUDGET_EXHAUSTED"
    assert len(calls) == 1  # no network call once exhausted


def test_shared_fetch_counts_http_errors_as_calls(monkeypatch, _rundown_key):
    def opener(request, timeout=None):
        raise HTTPError(request.full_url, 503, "down", {}, None)

    result = sources.fetch("RUNDOWN", "sports", opener=opener)
    assert result.ok is False and result.code == "RUNDOWN_HTTP_503"
    assert budget.status()["calls"] == 1


def test_ingestor_transport_is_metered_and_fails_closed(monkeypatch, _rundown_key):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "10")
    calls = []

    def opener(request, timeout=None):
        calls.append(1)
        return _Resp({"events": []}, {"X-Datapoints": "12", "X-Data-Delay-Seconds": "60"})

    assert ingestor._request_json("/api/v2/sports/3/events/2026-10-09", opener=opener).ok
    blocked = ingestor._request_json("/api/v2/sports/3/events/2026-10-09", opener=opener)
    assert blocked.ok is False and blocked.code == "PAID_PROVIDER_BUDGET_EXHAUSTED"
    assert calls == [1]
