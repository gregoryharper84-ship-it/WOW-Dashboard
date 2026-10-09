"""Rundown daily budget: atomic reservation, fail-closed durable state, soft ceiling.

The fake DB below mirrors the SQL contract in
``migrations/20261009_v17_rundown_daily_budget.sql`` (one conditional update
under a row lock). The SQL itself was verified against Postgres 16 with
concurrent sessions; see the PR #1568 evidence comment.
"""
import http.client
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from v17 import market_evidence_sources as sources
from v17 import rundown_datapoint_budget as budget
from v17 import rundown_market_ingestor as ingestor
from v17 import rundown_provider_health as health

DAY1 = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)
DAY2 = datetime(2026, 10, 10, 0, 5, tzinfo=timezone.utc)


class FakeBudgetDb:
    """Shared store with the same atomic semantics as the SQL functions."""

    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()
        self.fail_reserve = False
        self.fail_settle = False
        self.malformed = False
        self.rpc_calls = []

    def _row(self, day):
        return self.rows.setdefault(day, {k: 0 for k in budget._empty_totals()})

    def reserve(self, p):
        if self.fail_reserve:
            raise RuntimeError("db down")
        if self.malformed:
            return {"allowed": "yes"}
        with self.lock:
            row = self._row(p["p_day"])
            ok = (p["p_call_limit"] is None or row["calls_reserved"] < p["p_call_limit"]) and (
                p["p_datapoint_limit"] is None or row["datapoints"] < p["p_datapoint_limit"]
            )
            if ok:
                row["calls_reserved"] += 1
                row["calls_in_flight"] += 1
            return {"allowed": ok, **row}

    def settle(self, p):
        if self.fail_settle:
            raise RuntimeError("db down")
        with self.lock:
            row = self.rows[p["p_day"]]
            row["calls_in_flight"] = max(0, row["calls_in_flight"] - 1)
            row["calls_settled"] += 1
            row["calls_unmetered"] += 1 if p["p_datapoints"] is None else 0
            row["calls_failed"] += 1 if p["p_failed"] else 0
            row["datapoints"] += p["p_datapoints"] or 0
            return dict(row)

    def client(self):
        db = self

        class _Rpc:
            def __init__(self, name, params):
                self.name, self.params = name, params

            def execute(self):
                db.rpc_calls.append(self.name)
                fn = db.reserve if self.name == budget.RESERVE_RPC else db.settle
                return SimpleNamespace(data=fn(self.params))

        class _Table:
            def __init__(self):
                self.day = None

            def select(self, *_a):
                return self

            def eq(self, _f, value):
                self.day = value
                return self

            def limit(self, *_a):
                return self

            def execute(self):
                row = db.rows.get(self.day)
                return SimpleNamespace(data=[dict(row)] if row else [])

        return SimpleNamespace(rpc=lambda n, p: _Rpc(n, p), table=lambda _n: _Table())


def _admit(now=DAY1, datapoints=None, failed=False):
    res = budget.reserve(now)
    if res.allowed:
        res.datapoints, res.failed = datapoints, failed
        res.settle()
    return res


def test_default_limits_and_off(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", raising=False)
    monkeypatch.delenv("WOW_RUNDOWN_DAILY_CALL_BUDGET", raising=False)
    assert budget.datapoint_limit() == 6000 and budget.call_limit() == 300
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "off")
    assert budget.datapoint_limit() is None


def test_local_scope_is_reported_and_soft_ceiling_resets_next_day(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "100")
    assert budget.scope() == budget.SCOPE_LOCAL
    assert _admit(datapoints=60).allowed
    assert _admit(datapoints=45).allowed  # soft ceiling: admitted while below
    denied = budget.reserve(DAY1)
    assert (denied.allowed, denied.code) == (False, "PAID_PROVIDER_BUDGET_EXHAUSTED")
    assert denied.audit()["datapoint_limit_kind"] == "SOFT_ADMISSION_CEILING"
    assert denied.audit()["scope"] == "PROCESS_LOCAL"
    assert budget.reserve(DAY2).allowed


def test_call_cap_is_hard_and_counts_unsettled_reservations(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_CALL_BUDGET", "2")
    first = budget.reserve(DAY1)  # in flight, never settled (simulated crash)
    assert first.allowed
    assert _admit().allowed
    assert budget.reserve(DAY1).allowed is False
    st = budget.status(DAY1)
    assert st["calls_reserved"] == 2 and st["calls_in_flight"] == 1 and st["calls_unmetered"] == 1


def test_durable_total_is_shared_across_workers_and_restarts(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "100")
    db = FakeBudgetDb()
    budget.register_client(db.client)
    assert budget.scope() == budget.SCOPE_DURABLE
    assert _admit(datapoints=70).allowed
    budget._reset_for_tests()  # restart / second worker: no local state survives
    budget.register_client(db.client)
    assert _admit(datapoints=40).allowed
    denied = budget.reserve(DAY1)
    assert (denied.allowed, denied.code, denied.scope) == (False, budget.BLOCK_CODE, "DURABLE")
    assert db.rows["2026-10-09"]["datapoints"] == 110
    assert budget.status(DAY1)["datapoints"] == 110


def test_two_worker_race_never_exceeds_call_cap(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_CALL_BUDGET", "25")
    db = FakeBudgetDb()
    budget.register_client(db.client)
    admitted = []
    gate = threading.Barrier(8)

    def worker():
        gate.wait()
        for _ in range(10):
            res = budget.reserve(DAY1)
            if res.allowed:
                admitted.append(1)
                res.settle()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(admitted) == 25
    assert db.rows["2026-10-09"]["calls_reserved"] == 25
    assert db.rows["2026-10-09"]["calls_in_flight"] == 0


def test_db_read_failure_fails_closed_and_never_resets(monkeypatch):
    db = FakeBudgetDb()
    budget.register_client(db.client)
    assert _admit(datapoints=5).allowed
    db.fail_reserve = True
    res = budget.reserve(DAY1)
    assert (res.allowed, res.code) == (False, "PAID_PROVIDER_BUDGET_STATE_UNAVAILABLE")
    db.fail_reserve = False
    assert budget.reserve(DAY1).totals["datapoints"] == 5  # ledger intact


def test_malformed_rpc_response_fails_closed():
    db = FakeBudgetDb()
    db.malformed = True
    budget.register_client(db.client)
    assert budget.reserve(DAY1).code == budget.STATE_UNAVAILABLE_CODE


def test_settle_write_failure_blocks_until_recovered_then_flushes():
    db = FakeBudgetDb()
    budget.register_client(db.client)
    res = budget.reserve(DAY1)
    db.fail_settle = True
    res.datapoints = 30
    res.settle()  # cannot be written: kept pending, never dropped
    blocked = budget.reserve(DAY1)
    assert (blocked.allowed, blocked.code) == (False, budget.STATE_UNAVAILABLE_CODE)
    assert db.rows["2026-10-09"]["calls_reserved"] == 1  # no reservation taken while blocked
    db.fail_settle = False
    assert budget.reserve(DAY1).allowed
    assert db.rows["2026-10-09"]["datapoints"] == 30


def test_settle_is_idempotent():
    db = FakeBudgetDb()
    budget.register_client(db.client)
    res = budget.reserve(DAY1)
    res.settle()
    res.settle()
    assert db.rpc_calls.count(budget.SETTLE_RPC) == 1


@pytest.mark.parametrize(
    "headers,expected",
    [({"X-Datapoints": "12"}, 12), ({}, None), ({"X-Datapoints": "abc"}, None),
     ({"X-Datapoints": "-4"}, None), (None, None)],
)
def test_header_parsing_missing_or_invalid_is_unmetered(headers, expected):
    assert budget.header_datapoints(headers) == expected


def test_observe_never_erases_a_known_count():
    res = budget.reserve(DAY1)
    res.observe({"X-Datapoints": "9"})
    res.observe(None, failed=True)
    assert (res.datapoints, res.failed) == (9, True)


class _Resp:
    def __init__(self, payload, headers, read_error=None):
        self.payload, self.headers, self.status, self.code = payload, headers, 200, 200
        self.read_error = read_error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        if self.read_error:
            raise self.read_error
        return json.dumps(self.payload).encode()


@pytest.fixture
def _rundown_key(monkeypatch):
    monkeypatch.setenv("THERUNDOWN_API_KEY", "test-only")
    monkeypatch.setattr(sources, "ENABLED", True, raising=False)


def test_shared_fetch_blocks_without_network_once_exhausted(monkeypatch, _rundown_key):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "50")
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return _Resp({"sports": []}, {"X-Datapoints": "60"})

    assert sources.fetch("RUNDOWN", "sports", opener=opener).ok
    second = sources.fetch("RUNDOWN", "sports", opener=opener)
    assert second.ok is False and second.code == "PAID_PROVIDER_BUDGET_EXHAUSTED"
    assert second.request_audit["budget"]["paid_provider_network_attempted"] is False
    assert len(calls) == 1


def test_shared_fetch_fails_closed_when_durable_state_unavailable(monkeypatch, _rundown_key):
    db = FakeBudgetDb()
    db.fail_reserve = True
    budget.register_client(db.client)
    calls = []
    result = sources.fetch("RUNDOWN", "sports", opener=lambda r, timeout=None: calls.append(1))
    assert result.code == "PAID_PROVIDER_BUDGET_STATE_UNAVAILABLE" and calls == []


@pytest.mark.parametrize(
    "error",
    [HTTPError("u", 503, "down", {"X-Datapoints": "3"}, None), URLError("dns"), TimeoutError()],
)
def test_partial_provider_failures_settle_exactly_once(_rundown_key, error):
    def opener(request, timeout=None):
        raise error

    assert sources.fetch("RUNDOWN", "sports", opener=opener).ok is False
    st = budget.status()
    assert st["calls_reserved"] == 1 and st["calls_settled"] == 1 and st["calls_in_flight"] == 0
    assert st["calls_failed"] == 1


def test_unexpected_read_error_still_releases_in_flight(_rundown_key):
    def opener(request, timeout=None):
        return _Resp({}, {"X-Datapoints": "7"}, read_error=http.client.IncompleteRead(b""))

    with pytest.raises(http.client.IncompleteRead):
        sources.fetch("RUNDOWN", "sports", opener=opener)
    st = budget.status()
    assert st["calls_in_flight"] == 0 and st["datapoints"] == 7


def test_auth_failover_reserves_each_network_attempt(monkeypatch, _rundown_key):
    monkeypatch.setattr(sources, "_api_keys", lambda provider: ["k1", "k2"])
    attempts = []

    def opener(request, timeout=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 401, "auth", {}, None)
        return _Resp({"sports": []}, {"X-Datapoints": "2"})

    assert sources.fetch("RUNDOWN", "sports", opener=opener).ok
    st = budget.status()
    assert len(attempts) == 2 and st["calls_reserved"] == 2 and st["datapoints"] == 2


def test_ingestor_transport_is_metered_and_fails_closed(monkeypatch, _rundown_key):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", "10")
    calls = []

    def opener(request, timeout=None):
        calls.append(1)
        return _Resp({"events": []}, {"X-Datapoints": "12", "X-Data-Delay-Seconds": "60"})

    assert ingestor._request_json("/api/v2/sports/3/events/2026-10-09", opener=opener).ok
    blocked = ingestor._request_json("/api/v2/sports/3/events/2026-10-09", opener=opener)
    assert blocked.ok is False and blocked.code == "PAID_PROVIDER_BUDGET_EXHAUSTED"
    assert calls == [1] and budget.status()["calls_in_flight"] == 0


def test_ingestor_network_error_settles(_rundown_key):
    def opener(request, timeout=None):
        raise URLError("reset")

    assert ingestor._request_json("/api/v2/sports", opener=opener).ok is False
    st = budget.status()
    assert st["calls_settled"] == 1 and st["calls_in_flight"] == 0 and st["calls_unmetered"] == 1


def test_provider_health_probe_uses_same_ledger(monkeypatch, _rundown_key):
    monkeypatch.setenv("WOW_RUNDOWN_DAILY_CALL_BUDGET", "1")
    provider = sources.PROVIDERS["RUNDOWN"]
    calls = []

    def opener(request, timeout=None):
        calls.append(1)
        return _Resp({"sports": []}, {"X-Datapoints": "1"})

    first, _ = health._request_json(provider, "sports", opener=opener)
    second, _ = health._request_json(provider, "sports", opener=opener)
    assert first["market_acquisition_status"] == "PASS"
    assert second["blocker"] == "PAID_PROVIDER_BUDGET_EXHAUSTED" and calls == [1]
