from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pick_request_runtime_core import PickRequestBatch, PickRequestRow
from v17 import pick_request_state_runtime as state
import v17.pick_request_run_control as subject


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.mode = "select"
        self.payload = None
        self.filters = {}
        self.limit_n = None

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_n = int(value)
        return self

    def upsert(self, payload, **_kwargs):
        self.mode = "upsert"
        self.payload = payload
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.mode == "select":
            data = [dict(row) for row in rows if all(row.get(k) == v for k, v in self.filters.items())]
            if self.limit_n is not None:
                data = data[: self.limit_n]
            return SimpleNamespace(data=data)
        payloads = self.payload if isinstance(self.payload, list) else [self.payload]
        for raw in payloads:
            item = dict(raw)
            if self.table == state.RUN_TABLE:
                key_fields = ("run_id",)
            elif self.table == state.ROW_TABLE:
                key_fields = ("run_id", "row_key")
            else:
                key_fields = ("transition_id",)
            match = next((row for row in rows if all(row.get(f) == item.get(f) for f in key_fields)), None)
            if match is None:
                rows.append(item)
            else:
                match.update(item)
        return SimpleNamespace(data=[dict(item) for item in payloads])


class _DB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _Query(self, name)


def _row(index=1, *, row_key=None, event_start="2026-09-22T00:15:00+00:00", line=238.5, direction="MORE"):
    return PickRequestRow(
        row_key=row_key or f"PP-{index:03d}::{direction}",
        event_id="401872947",
        event_start_time=event_start,
        sport="NFL",
        player="Matthew Stafford" if index != 15 else "Jaxson Dart",
        stat_type="PASS_YARDS",
        line=line,
        direction=direction,
        source_type="NORMALIZED",
        platform="PrizePicks",
    )


def _batch(request_id, rows):
    return PickRequestBatch(request_id=request_id, response_mode="COMPACT", rows=rows)


def test_semantic_timestamp_retry_migrates_legacy_hash_and_does_not_conflict():
    subject.install_semantic_identity_patch()
    db = _DB()
    store = state.PickRequestStateStore(db)
    first = store.begin(_batch("run-1", [_row()]))
    assert first.rows["PP-001::MORE"]["retry_count"] == 0
    second = store.begin(_batch("run-1", [_row(event_start="2026-09-22T00:15:00Z")]))
    record = second.rows["PP-001::MORE"]
    assert record["retry_count"] == 1
    assert record["identity_version"] == subject.IDENTITY_VERSION
    assert record["event_start_time"] == "2026-09-22T00:15:00+00:00"
    assert record["stat_type"] == "PASSING_YARDS"
    assert record["platform"] == "PRIZEPICKS"
    assert record["request_payload"]["event_start_time"] == "2026-09-22T00:15:00+00:00"


def test_semantic_identity_still_rejects_real_line_mutation():
    subject.install_semantic_identity_patch()
    db = _DB()
    store = state.PickRequestStateStore(db)
    store.begin(_batch("run-2", [_row(line=238.5)]))
    with pytest.raises(HTTPException) as excinfo:
        store.begin(_batch("run-2", [_row(line=239.5)]))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "RUN_ROW_IDENTITY_CONFLICT"
    assert "exact_line" in excinfo.value.detail["conflicting_fields"]
    assert excinfo.value.detail["can_execute"] is False


def test_close_is_durable_and_blocks_silent_resume():
    subject.install_semantic_identity_patch()
    db = _DB()
    store = state.PickRequestStateStore(db)
    store.begin(_batch("run-close", [_row()]))
    closed = subject.close_run(
        db,
        "run-close",
        subject.ClosePickRunRequest(
            closure_code="STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED",
            closure_reason="transport ambiguity",
            closure_metadata={"source_candidates": 479, "terminally_accounted": 20},
        ),
    )
    assert closed["run_status"] == "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED"
    with pytest.raises(HTTPException) as excinfo:
        store.begin(_batch("run-close", [_row()]))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "RUN_CLOSED_REOPEN_REQUIRED"
    state_view = subject.read_run_state(db, "run-close")
    assert state_view["resumable"] is False
    assert state_view["run"]["closure_metadata"]["source_candidates"] == 479


def test_finalize_cannot_reopen_a_closed_run():
    subject.install_semantic_identity_patch()
    db = _DB()
    store = state.PickRequestStateStore(db)
    ctx = store.begin(_batch("run-race", [_row()]))
    subject.close_run(db, "run-race", subject.ClosePickRunRequest(closure_code="STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED"))
    outcome = {
        "row_key": "PP-001::MORE",
        "terminal_status": "REJECTED",
        "code": "NO_LOW_PROBABILITY",
        "model_evaluated": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    manifest = store.finalize(ctx, [outcome])
    assert manifest["run_status"] == "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED"
    run = subject._load_run_by_request_id(db, "run-race")
    assert run["run_status"] == "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED"


def test_resumable_runner_freezes_full_manifest_then_batches_without_chat_loop():
    subject.install_semantic_identity_patch()
    db = _DB()
    rows = [_row(index=i + 1, row_key=f"row-{i + 1}", line=200.5 + i) for i in range(5)]
    calls = []

    def score_fn(batch, _model_identity):
        calls.append([row.row_key for row in batch.rows])
        run_id = state._run_id(batch.request_id)
        table = db.tables[state.ROW_TABLE]
        for request_row in batch.rows:
            record = next(row for row in table if row["run_id"] == run_id and row["row_key"] == request_row.row_key)
            record.update({
                "terminal_status": "REJECTED",
                "terminal_code": "NO_LOW_PROBABILITY",
                "durable_status": "REJECTED:NO_LOW_PROBABILITY",
                "outcome": {"row_key": request_row.row_key, "terminal_status": "REJECTED", "code": "NO_LOW_PROBABILITY"},
                "can_execute": False,
            })
        return {"run_controller_status": "BLOCKED", "reconciliation_pass": True, "can_execute": False}

    result = subject.run_resumable(
        db,
        subject.ResumablePickRunRequest(request_id="board-5", rows=rows, batch_size=2, max_batches=3),
        score_fn=score_fn,
        model_identity=None,
    )
    assert calls == [["row-1", "row-2"], ["row-3", "row-4"], ["row-5"]]
    assert result["rows_manifested"] == 5
    assert result["rows_attempted_this_call"] == 5
    assert result["batches_completed_this_call"] == 3
    assert result["pending_rows"] == 0
    assert result["next_action"] == "RUN_TERMINAL"
    assert result["can_execute"] is False
    assert all(row.get("request_payload") for row in db.tables[state.ROW_TABLE])


def test_resumable_runner_can_resume_from_request_id_only():
    subject.install_semantic_identity_patch()
    db = _DB()
    row = _row(row_key="row-only")
    subject._seed_manifest(db, subject.ResumablePickRunRequest(request_id="resume-only", rows=[row], max_batches=1))
    calls = []

    def score_fn(batch, _model_identity):
        calls.append(batch.rows[0].row_key)
        record = db.tables[state.ROW_TABLE][0]
        record["terminal_status"] = "REJECTED"
        record["terminal_code"] = "NO_LOW_PROBABILITY"
        return {"run_controller_status": "BLOCKED", "reconciliation_pass": True, "can_execute": False}

    result = subject.run_resumable(
        db,
        subject.ResumablePickRunRequest(request_id="resume-only", rows=[], batch_size=1, max_batches=1),
        score_fn=score_fn,
        model_identity=None,
    )
    assert calls == ["row-only"]
    assert result["pending_rows"] == 0
    assert result["can_execute"] is False
