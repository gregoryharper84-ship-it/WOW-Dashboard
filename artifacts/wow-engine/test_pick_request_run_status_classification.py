"""Board processing completion is separate from selection approval."""
import pytest
from v17.pick_request_run_status import governed_run_status

@pytest.mark.parametrize('total,completed,held,rejected,pending,unresolved,expected', [
    (4,0,0,4,0,0,'COMPLETE'),
    (4,2,0,2,0,0,'COMPLETE'),
    (1,1,0,0,0,0,'COMPLETE'),
    (1,0,1,0,0,0,'BLOCKED'),
    (2,1,1,0,0,0,'DEGRADED'),
    (2,0,1,1,0,0,'BLOCKED'),
    (2,0,0,1,1,1,'RUNNING'),
    (1,0,0,1,0,1,'BLOCKED'),
    (2,1,0,0,0,0,'BLOCKED'),
    (1,1,0,1,0,0,'BLOCKED'),
    (0,0,0,0,0,0,'BLOCKED'),
])
def test_governed_board_classification(total,completed,held,rejected,pending,unresolved,expected):
    assert governed_run_status(total=total,completed=completed,held=held,rejected=rejected,
                               pending=pending,unresolved=unresolved) == expected

def test_reconciler_stopped_run_trigger_guards_upsert_and_plain_update():
    """A late direct finalize must not reopen a STOPPED_ run after reconciliation.

    This is a source-contract test, not a PostgreSQL execution test.
    """
    from pathlib import Path
    sql = (
        Path(__file__).resolve().parent / "migrations"
        / "20261103_pick_request_governed_rejection_status.sql"
    ).read_text(encoding="utf-8").lower()
    assert "create or replace function public.wow_reject_stopped_pick_request_run_reopen()" in sql
    assert "left(coalesce(old.run_status, ''), 8) = 'stopped_'" in sql
    assert "new.run_status is distinct from old.run_status" in sql
    assert "pick_request_stopped_run_immutable" in sql
    assert "before update on public.wow_pick_request_runs" in sql
    assert "for each row" in sql
    assert "execute function public.wow_reject_stopped_pick_request_run_reopen()" in sql
    # The reconciler and the worker claim serialize on the parent run row.
    # A whole-table NOWAIT lock cannot do that: it deadlocks with any in-flight
    # run writer that already holds a row lock (executed proof:
    # tests/test_v17_pick_request_lease_race_postgres.py). Row-level locking
    # with SKIP LOCKED is the protocol; it must never regress to a table lock.
    assert "for update of r skip locked" in sql
    assert "for share of r skip locked" in sql
    assert "lock table public.wow_pick_request" not in sql
    assert "j.lease_expires_at > v_now" in sql
    assert "r.run_status = 'running'" in sql
    assert "r.updated_at < v_cutoff" in sql
