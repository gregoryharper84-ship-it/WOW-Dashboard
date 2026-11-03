"""Actual PostgreSQL lease-race proof for the V17 pick-request reconciler.

This module provisions ONLY the isolated pick-request fixture. It cannot accept
a remote host, arbitrary libpq options, the governed production database or a
service credential. A skipped test is never concurrency evidence.

It proves, on real backends rather than in-process mocks:

* the reconciler's NOWAIT ledger lock makes concurrent maintenance passes and
  live worker writes mutually exclusive instead of interleaved;
* a heartbeat committed while a pass is already running cannot be clobbered by
  that pass (the second stale/status re-check after the row lock is released);
* an unexpired worker lease keeps its run untouched;
* a stopped run cannot be resurrected by a plain UPDATE or by a late
  ``INSERT ... ON CONFLICT DO UPDATE`` from a direct writer;
* repeated concurrent claim/reconcile interleavings never leave a claimed
  RUNNING job on a stopped run and never raise a deadlock.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Barrier
import time

import pytest

DSN = os.environ.get("WOW_PICK_ISOLATED_TEST_DSN")
if not DSN or os.environ.get("WOW_PICK_ISOLATED_TEST_ATTESTATION") != "NONPRODUCTION_EPHEMERAL_ONLY":
    pytest.skip("Isolated pick-request PostgreSQL not configured", allow_module_level=True)

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict

info = conninfo_to_dict(DSN)
if (set(info) - {"host", "port", "dbname", "user", "password"}
        or info.get("host") not in {"127.0.0.1", "localhost", "::1"}
        or info.get("dbname") != "wow_release_fence_test"):
    raise RuntimeError("PICK_REQUEST_ISOLATED_DB_IDENTITY_UNVERIFIED")

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    "20260920_pick_request_durable_run_state.sql",
    "20260921_pick_request_run_control.sql",
    "20260921_pick_request_durable_job_queue.sql",
    "20261103_pick_request_governed_rejection_status.sql",
)
STOPPED = "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED"


def connection(autocommit=True):
    return psycopg.connect(
        DSN, autocommit=autocommit, connect_timeout=10,
        options="-c statement_timeout=20000 -c lock_timeout=5000",
    )


@pytest.fixture(scope="module", autouse=True)
def install_isolated_pick_request_fixture():
    with connection() as db:
        db.execute("""DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
        END $$;""")
        # Minimal stand-in for the FK target of wow_pick_request_row_states.
        # The governed prediction table is out of scope for orchestration proof.
        db.execute("CREATE TABLE IF NOT EXISTS public.wow_predictions (prediction_id uuid PRIMARY KEY)")
        for name in MIGRATIONS:
            db.execute((ROOT / "migrations" / name).read_text())
    yield


@pytest.fixture(autouse=True)
def clean_isolated_rows():
    with connection() as db:
        db.execute("TRUNCATE public.wow_pick_request_runs CASCADE")


def seed_run(run_id="run-lease-1", *, total=2, stale_seconds=7200,
             row_terms=None, job_status="RUNNING", lease_seconds=-3600):
    terms = row_terms if row_terms is not None else [("COMPLETED", True, 4)] * total
    with connection() as db:
        db.execute("""INSERT INTO public.wow_pick_request_runs
            (run_id,request_id,run_status,total_rows,updated_at)
            VALUES (%s,%s,'RUNNING',%s,clock_timestamp() - make_interval(secs => %s))""",
            (run_id, "req-" + run_id, total, stale_seconds))
        for index, (terminal, evaluated, stage) in enumerate(terms):
            db.execute("""INSERT INTO public.wow_pick_request_row_states
                (run_id,row_key,identity_hash,event_id,event_start_time,sport,player,
                 stat_type,exact_line,direction,source_type,terminal_status,
                 model_evaluated,stage_seq)
                VALUES (%s,%s,%s,%s,clock_timestamp() + interval '1 day','NBA','player',
                        'POINTS',1,'MORE','TEST',%s,%s,%s)""",
                (run_id, "row-%d" % index, "h" * 64, "evt-%d" % index,
                 terminal, evaluated, stage))
        if job_status:
            db.execute("""INSERT INTO public.wow_pick_request_jobs
                (run_id,request_id,status,lease_owner,lease_expires_at,updated_at)
                VALUES (%s,%s,%s,%s,clock_timestamp() + make_interval(secs => %s),
                        clock_timestamp() - make_interval(secs => %s))""",
                (run_id, "job-req-" + run_id, job_status,
                 "worker-a" if job_status == "RUNNING" else None,
                 lease_seconds, stale_seconds))


def reconcile(db=None, stale_after=3600):
    sql = "SELECT * FROM public.wow_reconcile_stale_pick_request_runs(%s)"
    if db is not None:
        return db.execute(sql, (stale_after,)).fetchall()
    with connection() as own:
        return own.execute(sql, (stale_after,)).fetchall()


def claim(db, worker="worker-a"):
    return db.execute(
        "SELECT job_id,status,lease_owner FROM public.wow_claim_pick_request_job(%s,1800)",
        (worker,)).fetchall()


def run_state(run_id="run-lease-1"):
    with connection() as db:
        return db.execute("""SELECT run_status,can_execute,completed_rows,held_rows,
                   rejected_rows,pending_rows,unresolved_rows,closure_code,closure_reason
            FROM public.wow_pick_request_runs WHERE run_id=%s""", (run_id,)).fetchone()


def job_state(run_id="run-lease-1"):
    with connection() as db:
        return db.execute("""SELECT status,lease_owner,lease_expires_at
            FROM public.wow_pick_request_jobs WHERE run_id=%s""", (run_id,)).fetchone()


def test_reconciler_finalizes_stale_run_from_durable_row_truth():
    seed_run()
    rows = reconcile()
    assert len(rows) == 1
    assert rows[0][0] == "run-lease-1"
    assert rows[0][2] == "COMPLETE"
    assert rows[0][3:6] == (2, 0, 0)
    status, can_execute, completed = run_state()[0:3]
    assert status == "COMPLETE"
    assert can_execute is False
    assert completed == 2
    job = job_state()
    assert job[0] == "COMPLETED"
    assert job[1] is None and job[2] is None


def test_reconciler_skips_a_run_an_inflight_writer_already_owns():
    seed_run()
    with connection(autocommit=False) as holder:
        holder.execute(
            "SELECT run_id FROM public.wow_pick_request_runs WHERE run_id=%s FOR UPDATE",
            ("run-lease-1",))
        started = time.monotonic()
        # SKIP LOCKED: the pass returns no rows instead of blocking on the row
        # lock (and instead of deadlocking on a conflicting table lock).
        assert reconcile() == []
        assert time.monotonic() - started < 5
        assert run_state()[0] == "RUNNING"
        holder.rollback()
    assert len(reconcile()) == 1
    assert run_state()[0] == "COMPLETE"


def test_two_reconciler_passes_have_one_serial_winner():
    seed_run()
    barrier = Barrier(2)

    def pass_rows():
        with connection() as db:
            barrier.wait(timeout=10)
            return len(reconcile(db))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(f.result(timeout=20) for f in
                         [pool.submit(pass_rows), pool.submit(pass_rows)])
    assert results == [0, 1], results
    assert run_state()[0] == "COMPLETE"


def test_live_worker_lease_is_never_reconciled_away():
    seed_run(job_status="RUNNING", lease_seconds=3600)
    assert reconcile() == []
    assert run_state()[0] == "RUNNING"
    job = job_state()
    assert job[0] == "RUNNING"
    assert job[1] == "worker-a"
    assert job[2] is not None


def test_heartbeat_committed_during_a_pass_blocks_the_stale_stop():
    seed_run()
    with connection(autocommit=False) as heartbeat:
        heartbeat.execute(
            "SELECT run_id FROM public.wow_pick_request_runs WHERE run_id=%s FOR UPDATE",
            ("run-lease-1",))
        with ThreadPoolExecutor(max_workers=1) as pool:
            reconciling = pool.submit(reconcile)
            # Let the pass take its ledger lock, read candidates, then block on
            # the row lock this heartbeat already holds.
            time.sleep(0.75)
            heartbeat.execute("""UPDATE public.wow_pick_request_runs
                SET updated_at=clock_timestamp() WHERE run_id=%s""", ("run-lease-1",))
            heartbeat.commit()
            rows = reconciling.result(timeout=20)
    assert rows == [], rows
    assert run_state()[0] == "RUNNING"


def test_stopped_run_cannot_be_resurrected_by_update_or_late_upsert():
    seed_run(total=2, row_terms=[("COMPLETED", True, 4)])
    rows = reconcile()
    assert len(rows) == 1
    assert rows[0][2] == STOPPED
    assert run_state()[0] == STOPPED
    assert job_state()[0] == "STOPPED"

    with connection() as db:
        with pytest.raises(psycopg.errors.CheckViolation,
                           match="PICK_REQUEST_STOPPED_RUN_IMMUTABLE"):
            db.execute("""UPDATE public.wow_pick_request_runs
                SET run_status='RUNNING', updated_at=now() WHERE run_id=%s""",
                ("run-lease-1",))
        with pytest.raises(psycopg.errors.CheckViolation,
                           match="PICK_REQUEST_STOPPED_RUN_IMMUTABLE"):
            db.execute("""INSERT INTO public.wow_pick_request_runs
                (run_id,request_id,run_status,total_rows)
                VALUES (%s,%s,'RUNNING',2)
                ON CONFLICT (run_id) DO UPDATE SET run_status='RUNNING'""",
                ("run-lease-1", "req-run-lease-1"))
        # Non-status column writes on a stopped run stay allowed (idempotent
        # accounting refresh) while the terminal status itself is frozen.
        db.execute("""UPDATE public.wow_pick_request_runs
            SET pending_rows=0 WHERE run_id=%s""", ("run-lease-1",))
    assert run_state()[0] == STOPPED


def test_claim_and_reconcile_interleavings_never_resurrect_or_deadlock():
    deadlocks, violations = [], []
    for iteration in range(40):
        with connection() as db:
            db.execute("TRUNCATE public.wow_pick_request_runs CASCADE")
        seed_run(total=1, row_terms=[("COMPLETED", True, 4)],
                 job_status="RUNNING", lease_seconds=-60)
        barrier = Barrier(2)

        def claiming():
            with connection() as db:
                barrier.wait(timeout=10)
                try:
                    return ("claim", claim(db))
                except psycopg.Error as error:
                    return ("claim_error", error.sqlstate)

        def reconciling():
            with connection() as db:
                barrier.wait(timeout=10)
                try:
                    return ("reconcile", len(reconcile(db)))
                except psycopg.Error as error:
                    return ("reconcile_error", error.sqlstate)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claiming), pool.submit(reconciling)]
            outcomes = [f.result(timeout=25) for f in futures]

        for kind, value in outcomes:
            if kind.endswith("_error") and value in {"40P01", "55P03"}:
                deadlocks.append((iteration, kind, value))

        status, _can_execute = run_state()[0:2]
        job = job_state()
        claimed = job[0] == "RUNNING" and job[1] == "worker-a"
        if str(status).startswith("STOPPED_") and job[0] == "RUNNING":
            violations.append((iteration, status, job))
        if claimed and str(status).startswith("STOPPED_"):
            violations.append((iteration, status, job))

    assert violations == [], violations[:3]
    assert deadlocks == [], deadlocks[:3]
