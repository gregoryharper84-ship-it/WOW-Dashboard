"""A non-reopenable run closure terminates stranded PENDING rows exactly once.

Production evidence: run 8dc7f247 (request pp-screens-2026-10-15-1544) was
closed STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED with 69 rows left PENDING
at INGESTED and all run counters at 0, so it could never reconcile.
"""
from v17 import pick_request_run_control as subject
from v17 import pick_request_state_runtime as state
from v17.test_pick_request_run_control import _DB, _batch, _row

STOP = "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED"


def _rows(db, request_id):
    return {
        item["row_key"]: item
        for item in db.tables[state.ROW_TABLE]
        if item["run_id"] == state._run_id(request_id)
    }


def _run(db, request_id):
    return subject._load_run_by_request_id(db, request_id)


def _scored(row_key):
    return {
        "row_key": row_key,
        "terminal_status": "REJECTED",
        "code": "NO_LOW_PROBABILITY",
        "model_evaluated": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def test_closure_holds_pending_rows_and_reconciles_run():
    subject.install_semantic_identity_patch()
    db = _DB()
    store = state.PickRequestStateStore(db)
    rows = [_row(index=i, line=200.5 + i) for i in range(1, 4)]
    ctx = store.begin(_batch("close-pending", rows))
    store.record_outcome(ctx, "PP-001::MORE", _scored("PP-001::MORE"))

    closed = subject.close_run(db, "close-pending", subject.ClosePickRunRequest(closure_code=STOP))

    assert closed["rows_held_at_closure"] == 2
    persisted = _rows(db, "close-pending")
    assert persisted["PP-001::MORE"]["terminal_status"] == "REJECTED"
    assert persisted["PP-001::MORE"]["terminal_code"] == "NO_LOW_PROBABILITY"
    for key in ("PP-002::MORE", "PP-003::MORE"):
        record = persisted[key]
        assert record["terminal_status"] == "HELD"
        assert record["terminal_code"] == subject.RUN_CLOSED_ROW_TERMINAL
        assert record["model_evaluated"] is False
        assert record["probability_publishable"] is False
        assert record["rank_eligible"] is False
        assert record["outcome"]["detail"]["run_closure_code"] == STOP
        assert record["can_execute"] is False
    run = _run(db, "close-pending")
    assert run["run_status"] == STOP
    assert run["total_rows"] == 3
    assert run["completed_rows"] + run["held_rows"] + run["rejected_rows"] == run["total_rows"]
    assert run["held_rows"] == 2 and run["rejected_rows"] == 1
    assert run["pending_rows"] == 0
    assert run["can_execute"] is False


def test_reopenable_closure_leaves_pending_rows_untouched():
    subject.install_semantic_identity_patch()
    db = _DB()
    state.PickRequestStateStore(db).begin(_batch("close-reopen", [_row()]))
    closed = subject.close_run(
        db, "close-reopen", subject.ClosePickRunRequest(closure_code=STOP, reopen_allowed=True)
    )
    assert closed["rows_held_at_closure"] == 0
    assert _rows(db, "close-reopen")["PP-001::MORE"]["terminal_status"] == "PENDING"


def test_late_finalize_cannot_terminate_a_closure_held_row_twice():
    subject.install_semantic_identity_patch()
    db = _DB()
    store = state.PickRequestStateStore(db)
    ctx = store.begin(_batch("close-race", [_row()]))
    subject.close_run(db, "close-race", subject.ClosePickRunRequest(closure_code=STOP))

    manifest = store.finalize(ctx, [_scored("PP-001::MORE")])

    record = _rows(db, "close-race")["PP-001::MORE"]
    assert record["terminal_status"] == "HELD"
    assert record["terminal_code"] == subject.RUN_CLOSED_ROW_TERMINAL
    assert manifest["run_status"] == STOP
    assert manifest["held_rows"] == 1 and manifest["pending_rows"] == 0
    assert manifest["can_execute"] is False


def test_repeat_close_is_idempotent():
    subject.install_semantic_identity_patch()
    db = _DB()
    state.PickRequestStateStore(db).begin(_batch("close-twice", [_row()]))
    subject.close_run(db, "close-twice", subject.ClosePickRunRequest(closure_code=STOP))
    again = subject.close_run(db, "close-twice", subject.ClosePickRunRequest(closure_code=STOP))
    assert again["already_closed"] is True
    assert _run(db, "close-twice")["held_rows"] == 1
