import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import v17.daily_async_runtime as daily_async_runtime
from v17.daily_async_runtime import (
    AsyncDailySubmitRequest,
    _read,
    _submit,
    install_daily_async_routes,
)


MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "20260921_v17_daily_async_runs.sql"
).read_text()


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.payload = None
        self.run_id = None

    def insert(self, payload):
        self.payload = dict(payload)
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        if field == "run_id":
            self.run_id = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.payload is not None:
            self.rows.append(dict(self.payload))
            return _Result([self.payload])
        return _Result([row for row in self.rows if row.get("run_id") == self.run_id])


class _Db:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "wow_v17_daily_async_runs"
        return _Table(self.rows)


def _auth():
    return None


def test_submit_persists_queued_run_with_execution_disabled():
    db = _Db()
    req = AsyncDailySubmitRequest(
        requested_slate_date="2026-09-21",
        requested_timezone="America/Chicago",
        lanes=["PROPS", "MONEYLINE"],
        max_props=6,
        max_team_events=6,
        response_mode="COMPACT",
    )

    run_id = _submit(db, req)
    assert run_id.startswith("v17-daily-async-")
    assert len(db.rows) == 1
    assert db.rows[0]["run_status"] == "QUEUED"
    assert db.rows[0]["can_execute"] is False
    assert db.rows[0]["request_payload"]["requested_slate_date"] == "2026-09-21"

    row = _read(db, run_id)
    assert row is not None
    assert row["run_id"] == run_id
    assert row["can_execute"] is False


def test_async_routes_mount_without_changing_scoring_authority():
    app = FastAPI()
    db = _Db()
    installed = install_daily_async_routes(
        app,
        auth_callable=_auth,
        db_client_fn=lambda: db,
        market_api=object(),
        event_api=object(),
    )
    assert installed is True
    routes = {getattr(route, "path", None): getattr(route, "operation_id", None) for route in app.routes}
    assert routes["/v17/daily-snapshot-submit"] == "submitWowV17DailySnapshot"
    assert routes["/v17/daily-snapshot-run/{run_id}"] == "getWowV17DailySnapshotRun"


def test_sql_claims_with_skip_locked_and_lease_token():
    normalized = " ".join(MIGRATION.split()).lower()
    assert "for update skip locked" in normalized
    assert "lease_token uuid" in normalized
    assert "gen_random_uuid()" in normalized
    assert "run_status = 'running'" in normalized
    assert "lease_expires_at <= now()" in normalized


def test_sql_terminal_results_are_immutable_and_execution_disabled():
    normalized = " ".join(MIGRATION.split()).lower()
    assert "v17_daily_async_terminal_immutable" in normalized
    assert "old.run_status in ('completed','failed')" in normalized
    assert "raise exception 'v17_daily_async_terminal_immutable'" in normalized
    assert "can_execute boolean not null default false check (can_execute = false)" in normalized


def test_completion_requires_current_lease_and_result_payload():
    normalized = " ".join(MIGRATION.split()).lower()
    assert "and lease_token = p_lease_token" in normalized
    assert "if p_result_payload is null" in normalized
    assert "result_payload = p_result_payload" in normalized
    assert "status',case when changed = 1 then 'completed' else 'stale_lease' end" in normalized


def test_worker_staggers_first_database_claim_after_startup(monkeypatch):
    app = FastAPI()
    app.state.wow_v17_daily_async_wake = asyncio.Event()
    sleeps = []

    async def stop_on_initial_delay(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    def db_must_not_be_touched():
        raise AssertionError("daily async database claim occurred before startup delay")

    monkeypatch.setenv("WOW_V17_DAILY_ASYNC_INITIAL_DELAY_SECONDS", "10")
    monkeypatch.setattr(daily_async_runtime.asyncio, "sleep", stop_on_initial_delay)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await daily_async_runtime._worker_loop(
                app,
                db_client_fn=db_must_not_be_touched,
                market_api=object(),
                event_api=object(),
            )

    asyncio.run(exercise())
    assert sleeps == [10.0]


def test_idle_worker_uses_slow_poll_and_bounded_db_failure_backoff():
    assert daily_async_runtime.DEFAULT_IDLE_POLL_SECONDS == 30
    assert daily_async_runtime.DEFAULT_DB_FAILURE_BACKOFF_MAX_SECONDS == 120
    assert daily_async_runtime._idle_wait_seconds(30, 0, 120) == 30.0
    assert daily_async_runtime._idle_wait_seconds(30, 1, 120) == 60.0
    assert daily_async_runtime._idle_wait_seconds(30, 2, 120) == 120.0
    assert daily_async_runtime._idle_wait_seconds(30, 5, 120) == 120.0


class _PendingProbe:
    def __init__(self, *, rows):
        self.rows = rows
        self.predicate = None
        self.limit_value = None

    def select(self, columns):
        assert columns == "run_id"
        return self

    def or_(self, predicate):
        self.predicate = predicate
        return self

    def limit(self, amount):
        self.limit_value = amount
        return self

    def execute(self):
        return _Result(self.rows)


class _PendingDb:
    def __init__(self, probe):
        self.probe = probe

    def table(self, name):
        assert name == daily_async_runtime.TABLE
        return self.probe


@pytest.mark.parametrize("rows,expected", [([], False), ([{"run_id": "queued"}], True)])
def test_read_only_queue_preflight_preserves_expired_lease_claimability(rows, expected):
    probe = _PendingProbe(rows=rows)
    assert daily_async_runtime._has_claimable_work(_PendingDb(probe)) is expected
    assert probe.limit_value == 1
    assert "run_status.eq.QUEUED" in probe.predicate
    assert "and(run_status.eq.RUNNING,lease_expires_at.lte." in probe.predicate


def test_async_submission_signals_worker_wake_after_durable_insert():
    app = FastAPI()
    db = _Db()
    daily_async_runtime.install_daily_async_routes(
        app,
        auth_callable=_auth,
        db_client_fn=lambda: db,
        market_api=object(),
        event_api=object(),
    )
    wake = app.state.wow_v17_daily_async_wake
    assert wake.is_set() is False
    client = TestClient(app)
    response = client.post(
        "/v17/daily-snapshot-submit",
        json={
            "requested_slate_date": "2026-10-09",
            "requested_timezone": "America/Chicago",
            "lanes": ["PROPS", "MONEYLINE"],
        },
    )
    assert response.status_code == 202
    assert response.json()["run_status"] == "QUEUED"
    assert response.json()["can_execute"] is False
    assert wake.is_set() is True
    assert db.rows[0]["run_status"] == "QUEUED"


def test_no_queue_never_attempts_heavy_memory_admission(monkeypatch):
    app = FastAPI()
    app.state.wow_v17_daily_async_wake = asyncio.Event()
    monkeypatch.setenv("WOW_V17_DAILY_ASYNC_INITIAL_DELAY_SECONDS", "0")
    monkeypatch.setattr(daily_async_runtime, "_has_claimable_from_factory", lambda _fn: False)
    attempts = []

    def unexpected_admission(_operation):
        attempts.append("heavy_admission")
        raise AssertionError("No work; memory guard must not be invoked")

    monkeypatch.setattr(
        daily_async_runtime.memory_admission, "try_acquire_heavy_job", unexpected_admission
    )
    waits = []

    async def stop_idle_wait(_wake, seconds):
        waits.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(daily_async_runtime, "_wait_for_worker_wake", stop_idle_wait)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await daily_async_runtime._worker_loop(
                app,
                db_client_fn=lambda: _PendingDb(_PendingProbe(rows=[])),
                market_api=object(),
                event_api=object(),
            )

    asyncio.run(exercise())
    assert attempts == []
    assert waits == [30.0]


def test_queue_probe_failure_is_nonterminal_and_worker_retries_without_memory_admission(monkeypatch):
    app = FastAPI()
    app.state.wow_v17_daily_async_wake = asyncio.Event()
    monkeypatch.setenv("WOW_V17_DAILY_ASYNC_INITIAL_DELAY_SECONDS", "0")

    def broken_probe(_fn):
        raise OSError("database unavailable")

    monkeypatch.setattr(daily_async_runtime, "_has_claimable_from_factory", broken_probe)
    monkeypatch.setattr(
        daily_async_runtime.memory_admission,
        "try_acquire_heavy_job",
        lambda _operation: (_ for _ in ()).throw(AssertionError("No attempt on DB failure")),
    )
    waits = []

    async def stop_on_probe_wait(_wake, seconds):
        waits.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(daily_async_runtime, "_wait_for_worker_wake", stop_on_probe_wait)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await daily_async_runtime._worker_loop(
                app,
                db_client_fn=lambda: None,
                market_api=object(),
                event_api=object(),
            )

    asyncio.run(exercise())
    assert waits == [60.0]  # first failure uses exponential bounded backoff
