from pathlib import Path

from fastapi import FastAPI

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
