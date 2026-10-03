from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI

import v17.nfl_pickem_async_runtime as pickem_async_runtime
import v17.nfl_pickem_runtime as pickem_runtime
from v17.nfl_pickem_async_runtime import (
    AsyncNFLPickemSubmitRequest,
    _read,
    _run_existing_pickem,
    _submit,
    install_nfl_pickem_async_routes,
)


MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "20260930_v17_nfl_pickem_async_runs.sql"
).read_text()


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.payload = None
        self.filters: dict[str, object] = {}

    def insert(self, payload):
        self.payload = dict(payload)
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.payload is not None:
            self.rows.append(dict(self.payload))
            return _Result([self.payload])
        return _Result(
            [
                row
                for row in self.rows
                if all(row.get(field) == value for field, value in self.filters.items())
            ]
        )


class _Db:
    def __init__(self):
        self.rows: list[dict] = []

    def table(self, name):
        assert name == "wow_v17_nfl_pickem_async_runs"
        return _Table(self.rows)


def _request(*, idempotency_key: str | None = None, expected_game_count: int = 16):
    return AsyncNFLPickemSubmitRequest(
        requested_slate_dates=["2026-10-01", "2026-10-04", "2026-10-05"],
        requested_timezone="America/Chicago",
        expected_game_count=expected_game_count,
        strategy_mode="MAX_EXPECTED_CORRECT",
        idempotency_key=idempotency_key,
    )


def _auth():
    return None


def test_submit_persists_fast_queued_run_without_invoking_scorer():
    db = _Db()
    run_id = _submit(db, _request())
    assert run_id.startswith("v17-nfl-pickem-async-")
    assert len(db.rows) == 1
    assert db.rows[0]["run_status"] == "QUEUED"
    assert db.rows[0]["request_payload"]["expected_game_count"] == 16
    assert db.rows[0]["request_payload"]["strategy_mode"] == "MAX_EXPECTED_CORRECT"
    assert db.rows[0]["can_execute"] is False

    row = _read(db, run_id)
    assert row is not None
    assert row["run_id"] == run_id
    assert row["can_execute"] is False


def test_same_idempotency_key_returns_same_run_and_does_not_duplicate_work():
    db = _Db()
    req = _request(idempotency_key="week4-2026-card")
    first = _submit(db, req)
    second = _submit(db, req)
    assert second == first
    assert len(db.rows) == 1


def test_idempotency_key_cannot_be_reused_for_different_request():
    db = _Db()
    _submit(db, _request(idempotency_key="week4-2026-card"))
    with pytest.raises(ValueError, match="IDEMPOTENCY_KEY_REUSED"):
        _submit(
            db,
            _request(idempotency_key="week4-2026-card", expected_game_count=15),
        )


def test_worker_delegates_to_existing_governed_pickem_runtime(monkeypatch):
    captured = {}

    def fake_run(req, *, db_client_fn, event_api):
        captured["request"] = req
        captured["db"] = db_client_fn()
        captured["event_api"] = event_api
        return {
            "run_id": "inner-run",
            "status": "PICKEM_BOARD_READY",
            "submission_ready": True,
            "can_execute": False,
        }

    monkeypatch.setattr(pickem_runtime, "run_nfl_pickem_board", fake_run)
    db = object()
    event_api = object()
    result = _run_existing_pickem(
        {
            "requested_slate_dates": ["2026-10-01", "2026-10-04", "2026-10-05"],
            "requested_timezone": "America/Chicago",
            "expected_game_count": 16,
            "strategy_mode": "MAX_EXPECTED_CORRECT",
        },
        db_client_fn=lambda: db,
        event_api=event_api,
    )
    assert result["status"] == "PICKEM_BOARD_READY"
    assert result["can_execute"] is False
    assert captured["request"].expected_game_count == 16
    assert captured["db"] is db
    assert captured["event_api"] is event_api


def test_async_routes_mount_with_stable_operation_ids():
    app = FastAPI()
    db = _Db()
    installed = install_nfl_pickem_async_routes(
        app,
        auth_callable=_auth,
        db_client_fn=lambda: db,
        event_api=object(),
    )
    assert installed is True
    routes = {
        getattr(route, "path", None): getattr(route, "operation_id", None)
        for route in app.routes
    }
    assert routes["/v17/nfl-pickem-submit"] == "submitWowV17NFLPickemBoard"
    assert routes["/v17/nfl-pickem-run/{run_id}"] == "getWowV17NFLPickemRun"


def test_sql_claims_one_run_with_skip_locked_and_restart_lease():
    normalized = " ".join(MIGRATION.split()).lower()
    assert "for update skip locked" in normalized
    assert "lease_token uuid" in normalized
    assert "gen_random_uuid()" in normalized
    assert "run_status = 'running'" in normalized
    assert "lease_expires_at <= now()" in normalized
    assert "greatest(300" in normalized


def test_sql_terminal_results_are_immutable_and_execution_disabled():
    normalized = " ".join(MIGRATION.split()).lower()
    assert "v17_nfl_pickem_async_terminal_immutable" in normalized
    assert "old.run_status in ('completed','failed')" in normalized
    assert "raise exception 'v17_nfl_pickem_async_terminal_immutable'" in normalized
    assert "can_execute boolean not null default false check (can_execute = false)" in normalized


def test_completion_requires_current_lease_and_result_payload():
    normalized = " ".join(MIGRATION.split()).lower()
    assert "and lease_token = p_lease_token" in normalized
    assert "if p_result_payload is null" in normalized
    assert "result_payload = p_result_payload" in normalized
    assert "status',case when changed = 1 then 'completed' else 'stale_lease' end" in normalized


def test_worker_staggers_first_database_claim_after_startup(monkeypatch):
    app = FastAPI()
    app.state.wow_v17_nfl_pickem_async_wake = asyncio.Event()
    sleeps = []

    async def stop_on_initial_delay(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    def db_must_not_be_touched():
        raise AssertionError("NFL Pick'em database claim occurred before startup delay")

    monkeypatch.setenv("WOW_V17_NFL_PICKEM_ASYNC_INITIAL_DELAY_SECONDS", "20")
    monkeypatch.setattr(pickem_async_runtime.asyncio, "sleep", stop_on_initial_delay)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await pickem_async_runtime._worker_loop(
                app,
                db_client_fn=db_must_not_be_touched,
                event_api=object(),
            )

    asyncio.run(exercise())
    assert sleeps == [20.0]
