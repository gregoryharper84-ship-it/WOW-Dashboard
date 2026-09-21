from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17 import daily_async_runtime as async_daily
from v17.daily_snapshot_runtime import DailySnapshotRequest


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.mode = "select"
        self.payload = None
        self.filters = []
        self.limit_n = None

    def select(self, *_args):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload):
        self.mode = "update"
        self.payload = dict(payload)
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def is_(self, key, value):
        self.filters.append(("is", key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_n = int(value)
        return self

    def _matches(self, row):
        for op, key, value in self.filters:
            if op == "eq" and row.get(key) != value:
                return False
            if op == "is" and row.get(key) is not value:
                return False
        return True

    def execute(self):
        assert self.table == async_daily.TABLE
        rows = self.db.rows
        if self.mode == "insert":
            run_id = self.payload["run_id"]
            if run_id in rows:
                raise RuntimeError("duplicate run")
            for existing in rows.values():
                if (
                    existing.get("idempotency_key") == self.payload.get("idempotency_key")
                    and existing.get("request_hash") == self.payload.get("request_hash")
                ):
                    raise RuntimeError("duplicate idempotency")
            row = dict(self.payload)
            row.setdefault("submission_receipt_id", f"receipt-{len(rows)+1}")
            row.setdefault("lease_owner", None)
            row.setdefault("lease_expires_at", None)
            row.setdefault("execution_run_id", None)
            row.setdefault("result_payload", None)
            row.setdefault("last_error_type", None)
            rows[run_id] = row
            return SimpleNamespace(data=[dict(row)])
        matched = [row for row in rows.values() if self._matches(row)]
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        if self.mode == "select":
            return SimpleNamespace(data=[dict(row) for row in matched])
        if self.mode == "update":
            updated = []
            for row in matched:
                row.update(self.payload)
                updated.append(dict(row))
            return SimpleNamespace(data=updated)
        raise AssertionError(self.mode)


class _DB:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return _Query(self, name)


def _req():
    return DailySnapshotRequest(
        requested_slate_date="2026-09-21",
        requested_timezone="America/Chicago",
        lanes=["MONEYLINE"],
        max_props=0,
        max_team_events=3,
        response_mode="COMPACT",
    )


def test_submission_is_durable_and_idempotent_for_same_request_id():
    db = _DB()
    first, reused_first = async_daily._create_submission(db, req=_req(), request_id="req-123")
    second, reused_second = async_daily._create_submission(db, req=_req(), request_id="req-123")

    assert reused_first is False
    assert reused_second is True
    assert first["run_id"] == second["run_id"]
    assert first["submission_receipt_id"] == second["submission_receipt_id"]
    assert first["status"] == "QUEUED"
    assert first["can_execute"] is False
    assert len(db.rows) == 1


def test_claim_is_single_owner_until_lease_expires():
    db = _DB()
    row, _ = async_daily._create_submission(db, req=_req(), request_id="req-claim")

    first = async_daily._claim_run(db, row["run_id"])
    second = async_daily._claim_run(db, row["run_id"])

    assert first is not None
    claimed, owner = first
    assert claimed["status"] == "RUNNING"
    assert owner
    assert claimed["attempt_count"] == 1
    assert second is None
    assert db.rows[row["run_id"]]["can_execute"] is False


def test_background_completion_preserves_governed_result_and_maps_detail_refs(monkeypatch):
    db = _DB()
    row, _ = async_daily._create_submission(db, req=_req(), request_id="req-complete")

    monkeypatch.setattr(
        async_daily,
        "run_daily_snapshot",
        lambda *_args, **_kwargs: {
            "run_id": "v17-daily-inner-1",
            "run_status": "COMPLETED",
            "rows": [
                {
                    "lane": "MONEYLINE",
                    "detail_ref": {"run_id": "v17-daily-inner-1", "row_index": 0},
                }
            ],
            "reconciliation": {"balanced": True},
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "blockers": [],
            "can_execute": False,
        },
    )

    status = async_daily._execute_submission(
        run_id=row["run_id"],
        db_client_fn=lambda: db,
        market_api=object(),
        event_api=object(),
    )

    stored = db.rows[row["run_id"]]
    result = stored["result_payload"]
    assert status == "COMPLETED"
    assert stored["status"] == "COMPLETED"
    assert stored["execution_run_id"] == "v17-daily-inner-1"
    assert result["run_id"] == row["run_id"]
    assert result["execution_run_id"] == "v17-daily-inner-1"
    assert result["rows"][0]["detail_ref"]["run_id"] == row["run_id"]
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["can_execute"] is False


def test_background_runtime_failure_retries_then_fails_without_model_unavailable(monkeypatch):
    db = _DB()
    row, _ = async_daily._create_submission(db, req=_req(), request_id="req-fail")

    def fail(*_args, **_kwargs):
        raise TimeoutError("simulated background transport failure")

    monkeypatch.setattr(async_daily, "run_daily_snapshot", fail)

    statuses = [
        async_daily._execute_submission(
            run_id=row["run_id"],
            db_client_fn=lambda: db,
            market_api=object(),
            event_api=object(),
        )
        for _ in range(async_daily.MAX_ATTEMPTS)
    ]

    assert statuses == ["RETRY_PENDING", "RETRY_PENDING", "FAILED"]
    stored = db.rows[row["run_id"]]
    assert stored["status"] == "FAILED"
    assert stored["attempt_count"] == async_daily.MAX_ATTEMPTS
    assert stored["last_error_type"] == "TimeoutError"
    assert stored["result_payload"]["failure_class"] == "DAILY_BACKGROUND_COMPLETION_FAILED"
    assert "MODEL_UNAVAILABLE" not in repr(stored["result_payload"])
    assert stored["can_execute"] is False


def test_public_submission_receipt_is_nonterminal_and_pollable():
    db = _DB()
    row, _ = async_daily._create_submission(db, req=_req(), request_id="req-receipt")
    receipt = async_daily._submission_receipt(row, reused=False)

    assert receipt["run_id"] == row["run_id"]
    assert receipt["accepted"] is True
    assert receipt["terminal"] is False
    assert receipt["poll_operation_id"] == "readWowV17DailySnapshotRowDetail"
    assert row["run_id"] in receipt["poll_url"]
    assert receipt["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert receipt["can_execute"] is False
