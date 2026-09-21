from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pick_request_runtime_core import PickRequestBatch, PickRequestRow
import v17.pick_request_state_runtime as subject


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.mode = "select"
        self.payload = None
        self.filters = {}

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def upsert(self, payload, **_kwargs):
        self.mode = "upsert"
        self.payload = payload
        return self

    def execute(self):
        if self.db.fail_table == self.table and self.mode == "upsert":
            raise TimeoutError("forced persistence outage")
        rows = self.db.tables.setdefault(self.table, [])
        if self.mode == "select":
            data = [
                dict(row)
                for row in rows
                if all(row.get(key) == value for key, value in self.filters.items())
            ]
            return SimpleNamespace(data=data)
        payloads = self.payload if isinstance(self.payload, list) else [self.payload]
        for payload in payloads:
            item = dict(payload)
            if self.table == subject.RUN_TABLE:
                key_fields = ("run_id",)
            elif self.table == subject.ROW_TABLE:
                key_fields = ("run_id", "row_key")
            else:
                key_fields = ("transition_id",)
            match = next(
                (
                    row
                    for row in rows
                    if all(row.get(field) == item.get(field) for field in key_fields)
                ),
                None,
            )
            if match is None:
                rows.append(item)
            else:
                match.update(item)
        return SimpleNamespace(data=[dict(item) for item in payloads])


class _DB:
    def __init__(self):
        self.tables = {}
        self.fail_table = None

    def table(self, name):
        return _Query(self, name)


def _row(index, *, row_key=None, line=None):
    return PickRequestRow(
        row_key=row_key or f"board-row-{index}",
        event_id=f"NFL:20260921:{index}",
        event_start_time="2026-09-22T00:00:00+00:00",
        sport="NFL",
        player=f"Player {index}",
        stat_type="RECEIVING_YARDS",
        line=float(line if line is not None else 20 + index),
        direction="MORE",
        source_type="PASTED_BOARD",
        platform="PRIZEPICKS",
    )


def _batch(request_id, rows):
    return PickRequestBatch(request_id=request_id, response_mode="FULL", rows=rows)


def _held(row_key, code="MODEL_UNAVAILABLE"):
    return {
        "row_key": row_key,
        "terminal_status": "HELD",
        "code": code,
        "model_evaluated": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def _completed(row_key, prediction_id):
    return {
        "row_key": row_key,
        "terminal_status": "COMPLETED",
        "code": "FULL_MODEL",
        "model_evaluated": True,
        "rank_eligible": True,
        "probability_publishable": True,
        "result": {
            "prediction": {
                "prediction_id": prediction_id,
                "calibrated_probability": 0.66,
                "calibrated_probability_lower_bound": 0.59,
            }
        },
        "can_execute": False,
    }


def test_board_manifest_accumulates_all_54_rows_across_small_action_chunks():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    request_id = "board-54-stable-request"
    all_rows = [_row(i) for i in range(54)]

    last_manifest = None
    for start in range(0, 54, 4):
        chunk = all_rows[start : start + 4]
        ctx = store.begin(_batch(request_id, chunk))
        last_manifest = store.finalize(
            ctx,
            [_held(row.row_key, "MODEL_INPUTS_INSUFFICIENT") for row in chunk],
        )

    assert last_manifest is not None
    assert last_manifest["total_rows"] == 54
    assert last_manifest["held_rows"] == 54
    assert last_manifest["pending_rows"] == 0
    assert last_manifest["unresolved_rows"] == 0
    assert last_manifest["can_execute"] is False
    assert len(db.tables[subject.ROW_TABLE]) == 54


def test_completed_prediction_receipt_is_resumable_without_new_model_work():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    batch = _batch("resume-board", [_row(1)])
    first = store.begin(batch)
    outcome = _completed("board-row-1", "pred-1")
    store.record_outcome(first, "board-row-1", outcome)
    store.finalize(first, [outcome])

    second = store.begin(batch)
    cached = second.reusable_outcome("board-row-1")

    assert cached is not None
    assert cached["resumed_from_durable_receipt"] is True
    assert cached["result"]["prediction"]["prediction_id"] == "pred-1"
    assert second.rows["board-row-1"]["retry_count"] == 1
    assert second.rows["board-row-1"]["stage_seq"] >= subject.STAGE_SEQ["RECEIPT_PERSISTED"]
    assert second.rows["board-row-1"]["can_execute"] is False


def test_same_request_and_row_key_cannot_mutate_exact_identity():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    store.begin(_batch("identity-lock", [_row(3, line=23.5)]))

    with pytest.raises(HTTPException) as excinfo:
        store.begin(_batch("identity-lock", [_row(3, line=24.5)]))

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "RUN_ROW_IDENTITY_CONFLICT"
    assert excinfo.value.detail["can_execute"] is False


def test_model_computed_without_prediction_receipt_stays_research_only_and_nonpublishable():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    ctx = store.begin(_batch("research-only", [_row(5)]))
    outcome = {
        "row_key": "board-row-5",
        "terminal_status": "COMPLETED",
        "code": "RESEARCH_MODEL_OUTPUT",
        "model_evaluated": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "result": {
            "research_model_output": {
                "calibrated_probability": 0.61,
                "calibrated_probability_lower_bound": 0.53,
            }
        },
        "can_execute": False,
    }

    store.record_outcome(ctx, "board-row-5", outcome)
    record = ctx.rows["board-row-5"]

    assert record["current_stage"] == "MODEL_COMPUTED"
    assert record["durable_status"] == "MODEL_COMPUTED_RESEARCH_ONLY"
    assert record["probability_publishable"] is False
    assert record["rank_eligible"] is False
    assert ctx.reusable_outcome("board-row-5") is None


def test_state_ledger_failure_is_typed_receipt_service_failure_before_acknowledgement():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    ctx = store.begin(_batch("receipt-outage", [_row(7)]))
    db.fail_table = subject.ROW_TABLE
    outcome = _completed("board-row-7", "pred-7")

    with pytest.raises(subject.PickRequestStatePersistenceError) as excinfo:
        store.record_outcome(ctx, "board-row-7", outcome)

    http_error = subject._persistence_error(excinfo.value, request_id=ctx.request_id)
    assert http_error.status_code == 503
    assert http_error.detail["code"] == "RECEIPT_SERVICE_UNREACHABLE"
    assert http_error.detail["probability_publishable"] is False
    assert http_error.detail["rank_eligible"] is False
    assert http_error.detail["can_execute"] is False


def test_distinct_failure_domain_is_stored_without_renaming_terminal_code():
    db = _DB()
    store = subject.PickRequestStateStore(db)
    ctx = store.begin(_batch("failure-domain", [_row(9)]))
    outcome = _held("board-row-9", "MODEL_SCORER_FAILED")

    store.record_outcome(ctx, "board-row-9", outcome)

    record = ctx.rows["board-row-9"]
    assert record["terminal_code"] == "MODEL_SCORER_FAILED"
    assert record["failure_domain"] == "MODEL_SERVICE_UNREACHABLE"
    assert record["can_execute"] is False
