"""close_run / finalize route closure through the atomic database RPC (#1895).

Production evidence: run 8dc7f247 (request pp-screens-2026-10-15-1544) was
closed STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED with 69 rows left PENDING
and all run counters at 0. Row locking, the PENDING-only condition and the
closure fence are proven on real Postgres in
test_pick_request_run_close_atomic_db.py; these tests pin the Python contract.
"""
from types import SimpleNamespace

from v17 import pick_request_run_control as subject
from v17 import pick_request_state_runtime as state
from v17.test_pick_request_run_control import _DB, _batch, _row

STOP = "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED"


class _RpcDB(_DB):
    """Fake mirroring the RPC's committed-state contract."""

    def __init__(self, fail_times=0):
        super().__init__()
        self.rpc_calls = []
        self.fail_times = fail_times

    def rpc(self, name, params):
        self.rpc_calls.append((name, dict(params)))
        if self.fail_times:
            self.fail_times -= 1
            raise TimeoutError("uncertain")
        run = next(r for r in self.tables[state.RUN_TABLE] if r["run_id"] == params["p_run_id"])
        rows = [r for r in self.tables.get(state.ROW_TABLE, []) if r["run_id"] == params["p_run_id"]]
        held = 0
        if params["p_hold_pending"] and not run.get("reopen_allowed"):
            for row in rows:
                if row.get("terminal_status") == "PENDING":
                    row.update(terminal_status="HELD", terminal_code=subject.RUN_CLOSED_ROW_TERMINAL,
                               model_evaluated=False, probability_publishable=False, rank_eligible=False)
                    held += 1
        counts = {
            "total_rows": len(rows),
            "completed_rows": sum(r.get("terminal_status") == "COMPLETED" for r in rows),
            "held_rows": sum(r.get("terminal_status") == "HELD" for r in rows),
            "rejected_rows": sum(r.get("terminal_status") == "REJECTED" for r in rows),
            "pending_rows": sum(r.get("terminal_status") == "PENDING" for r in rows),
            "unresolved_rows": 0,
        }
        run.update(counts, run_status=run["closure_code"])
        data = {"status": "RECONCILED", "rows_held_at_closure": held, **counts, "can_execute": False}
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))


def _rows(db, request_id):
    return {r["row_key"]: r for r in db.tables[state.ROW_TABLE] if r["run_id"] == state._run_id(request_id)}


def _scored(row_key):
    return {"row_key": row_key, "terminal_status": "REJECTED", "code": "NO_LOW_PROBABILITY",
            "model_evaluated": True, "probability_publishable": False, "rank_eligible": False,
            "can_execute": False}


def test_close_calls_atomic_rpc_and_reports_committed_counts():
    subject.install_semantic_identity_patch()
    db = _RpcDB()
    store = state.PickRequestStateStore(db)
    ctx = store.begin(_batch("close-pending", [_row(index=i, line=200.5 + i) for i in range(1, 4)]))
    store.record_outcome(ctx, "PP-001::MORE", _scored("PP-001::MORE"))

    closed = subject.close_run(db, "close-pending", subject.ClosePickRunRequest(closure_code=STOP))

    assert db.rpc_calls == [(subject.CLOSE_ROWS_RPC, {"p_run_id": state._run_id("close-pending"), "p_hold_pending": True})]
    assert closed["rows_held_at_closure"] == 2
    assert closed["row_closure_reconciliation"]["status"] == "RECONCILED"
    assert _rows(db, "close-pending")["PP-001::MORE"]["terminal_status"] == "REJECTED"
    run = subject._load_run_by_request_id(db, "close-pending")
    assert (run["held_rows"], run["rejected_rows"], run["pending_rows"]) == (2, 1, 0)
    assert closed["can_execute"] is False


def test_reopenable_close_asks_rpc_not_to_hold():
    subject.install_semantic_identity_patch()
    db = _RpcDB()
    state.PickRequestStateStore(db).begin(_batch("close-reopen", [_row()]))
    closed = subject.close_run(db, "close-reopen", subject.ClosePickRunRequest(closure_code=STOP, reopen_allowed=True))
    assert db.rpc_calls[0][1]["p_hold_pending"] is False
    assert closed["rows_held_at_closure"] == 0
    assert _rows(db, "close-reopen")["PP-001::MORE"]["terminal_status"] == "PENDING"


def test_uncertain_rpc_is_typed_and_repeat_close_retries():
    subject.install_semantic_identity_patch()
    db = _RpcDB(fail_times=1)
    state.PickRequestStateStore(db).begin(_batch("close-retry", [_row()]))
    first = subject.close_run(db, "close-retry", subject.ClosePickRunRequest(closure_code=STOP))
    assert first["row_closure_reconciliation"]["status"] == "UNCERTAIN"
    assert first["row_closure_reconciliation"]["code"] == "RUN_CLOSURE_ROW_RECONCILIATION_UNCERTAIN"
    assert first["rows_held_at_closure"] == 0
    assert _rows(db, "close-retry")["PP-001::MORE"]["terminal_status"] == "PENDING"

    again = subject.close_run(db, "close-retry", subject.ClosePickRunRequest(closure_code=STOP))
    assert again["already_closed"] is True
    assert again["row_closure_reconciliation"]["rows_held_at_closure"] == 1
    assert _rows(db, "close-retry")["PP-001::MORE"]["terminal_status"] == "HELD"


def test_late_finalize_reports_committed_closure_state():
    subject.install_semantic_identity_patch()
    db = _RpcDB()
    store = state.PickRequestStateStore(db)
    ctx = store.begin(_batch("close-race", [_row(), _row(index=2, line=201.5)]))
    subject.close_run(db, "close-race", subject.ClosePickRunRequest(closure_code=STOP))
    db.rpc_calls.clear()

    manifest = store.finalize(ctx, [_scored("PP-001::MORE")])

    assert db.rpc_calls and db.rpc_calls[0][0] == subject.CLOSE_ROWS_RPC
    assert manifest["run_status"] == STOP
    assert manifest["row_closure_reconciliation"]["status"] == "RECONCILED"
    assert manifest["total_rows"] == manifest["completed_rows"] + manifest["held_rows"] + manifest["rejected_rows"]
    assert manifest["pending_rows"] == 0
    assert manifest["can_execute"] is False
