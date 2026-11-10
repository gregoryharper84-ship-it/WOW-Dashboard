"""Real-Postgres concurrency contract for atomic run-closure row holds (#1895).

Applies the actual pick-request migrations to a localhost Postgres and races
closure against worker finalize on separate connections. Runs only in the
designated wow-engine-verify CI workflow, or locally with
WOW_PICK_RUN_CLOSE_LOCAL_PG=1 and a localhost DATABASE_URL.
"""
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

MIGRATIONS = (
    "20260920_pick_request_durable_run_state.sql",
    "20260921_pick_request_run_control.sql",
    "20261002_pick_request_explicit_readiness_evidence.sql",
    "20261020_pick_request_closure_atomic_hold.sql",
)
STOP = "STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED"
HOLD_CODE = "RUN_CLOSED_BEFORE_TERMINAL"


def _dsn() -> str:
    in_ci = (
        os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("GITHUB_WORKFLOW") == "wow-engine-verify"
        and os.getenv("GITHUB_REPOSITORY") == "gregoryharper84-ship-it/WOW-Dashboard"
    )
    if not in_ci and os.getenv("WOW_PICK_RUN_CLOSE_LOCAL_PG") != "1":
        pytest.skip("Run only in the designated localhost Postgres CI workflow")
    dsn = os.getenv("DATABASE_URL") or "postgresql://wow_agent_ci:wow_agent_ci@localhost:5432/wow_agent_ci"
    assert urlparse(dsn).hostname in {"localhost", "127.0.0.1"}, "Refuse on any nonlocal host"
    return dsn


@pytest.fixture(scope="module")
def dsn():
    psycopg = pytest.importorskip("psycopg")
    value = _dsn()
    with psycopg.connect(value, autocommit=True) as conn:
        for role in ("anon", "authenticated", "service_role"):
            if not conn.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone():
                conn.execute(f"create role {role}")
        # FK target only; prediction rows are outside this contract.
        conn.execute("create table if not exists public.wow_predictions (prediction_id uuid primary key)")
        root = Path(__file__).resolve().parents[1] / "migrations"
        # Base pick-request schema migrations are not re-runnable; bootstrap them
        # once. The migration under test is idempotent and always re-applied.
        base_ready = conn.execute(
            "select to_regclass('public.wow_pick_request_row_states') is not null"
        ).fetchone()[0]
        for name in MIGRATIONS:
            if base_ready and name != MIGRATIONS[-1]:
                continue
            conn.execute((root / name).read_text())
    return value


def _connect(dsn, autocommit=True):
    import psycopg

    return psycopg.connect(dsn, autocommit=autocommit)


def _seed(dsn, keys=("r1", "r2", "r3"), *, reopen=False):
    run_id = f"run-{uuid4()}"
    with _connect(dsn) as conn:
        conn.execute(
            "insert into public.wow_pick_request_runs (run_id, request_id, total_rows) values (%s, %s, %s)",
            (run_id, f"req-{run_id}", len(keys)),
        )
        for key in keys:
            conn.execute(
                """insert into public.wow_pick_request_row_states
                   (run_id, row_key, identity_hash, event_id, event_start_time, sport, player,
                    stat_type, exact_line, direction, source_type, platform)
                   values (%s, %s, %s, 'E1', now() + interval '1 day', 'NFL', 'P', 'PASSING_YARDS',
                           250.5, 'MORE', 'NORMALIZED', 'PRIZEPICKS')""",
                (run_id, key, f"h-{key}"),
            )
        conn.execute(
            """update public.wow_pick_request_runs set closure_code=%s, run_status=%s,
               closed_at=now(), reopen_allowed=%s where run_id=%s""",
            (STOP, STOP, reopen, run_id),
        )
    return run_id


def _close(dsn, run_id, hold=True):
    with _connect(dsn) as conn:
        return conn.execute(
            "select public.wow_v17_close_pick_request_run_rows(%s, %s)", (run_id, hold)
        ).fetchone()[0]


def _worker_terminal_sql():
    # Mirrors the worker's full-row upsert (PostgREST on_conflict=run_id,row_key).
    return """insert into public.wow_pick_request_row_states
              (run_id, row_key, identity_hash, event_id, event_start_time, sport, player, stat_type,
               exact_line, direction, source_type, platform, terminal_status, terminal_code,
               model_evaluated, current_stage, stage_seq)
              values (%s, %s, 'h', 'E1', now(), 'NFL', 'P', 'PASSING_YARDS', 250.5, 'MORE',
                      'NORMALIZED', 'PRIZEPICKS', %s, %s, %s, %s, %s)
              on conflict (run_id, row_key) do update set
                terminal_status=excluded.terminal_status, terminal_code=excluded.terminal_code,
                model_evaluated=excluded.model_evaluated, current_stage=excluded.current_stage,
                stage_seq=excluded.stage_seq"""


def _rows(dsn, run_id):
    with _connect(dsn) as conn:
        return {
            key: (status, code)
            for key, status, code in conn.execute(
                "select row_key, terminal_status, terminal_code from public.wow_pick_request_row_states where run_id=%s",
                (run_id,),
            ).fetchall()
        }


def _counts(dsn, run_id):
    with _connect(dsn) as conn:
        return conn.execute(
            """select total_rows, completed_rows, held_rows, rejected_rows, pending_rows, run_status
               from public.wow_pick_request_runs where run_id=%s""",
            (run_id,),
        ).fetchone()


def test_close_holds_only_pending_rows_and_recounts_from_committed_state(dsn):
    run_id = _seed(dsn)
    with _connect(dsn) as conn:
        conn.execute(_worker_terminal_sql(), (run_id, "r1", "REJECTED", "NO_LOW_PROBABILITY", True, "GOVERNANCE_AUDITED", 5))
    out = _close(dsn, run_id)
    assert out["rows_held_at_closure"] == 2
    assert out["reconciliation_pass"] is True and out["can_execute"] is False
    assert _rows(dsn, run_id) == {
        "r1": ("REJECTED", "NO_LOW_PROBABILITY"),
        "r2": ("HELD", HOLD_CODE),
        "r3": ("HELD", HOLD_CODE),
    }
    assert _counts(dsn, run_id) == (3, 0, 2, 1, 0, STOP)


def test_finalize_committing_first_wins_over_blocked_close(dsn):
    run_id = _seed(dsn, keys=("r1",))
    worker = _connect(dsn, autocommit=False)
    worker.execute(_worker_terminal_sql(), (run_id, "r1", "REJECTED", "NO_LOW_PROBABILITY", True, "GOVERNANCE_AUDITED", 5))
    result = {}
    closer = threading.Thread(target=lambda: result.update(out=_close(dsn, run_id)))
    closer.start()
    time.sleep(0.5)
    assert closer.is_alive(), "close must block on the worker's row lock"
    worker.commit()
    worker.close()
    closer.join(10)
    assert result["out"]["rows_held_at_closure"] == 0
    assert _rows(dsn, run_id) == {"r1": ("REJECTED", "NO_LOW_PROBABILITY")}
    assert _counts(dsn, run_id) == (1, 0, 0, 1, 0, STOP)


def test_close_committing_first_is_not_overwritten_by_late_worker_upsert(dsn):
    run_id = _seed(dsn, keys=("r1",))
    _close(dsn, run_id)
    with _connect(dsn) as conn:
        conn.execute(_worker_terminal_sql(), (run_id, "r1", "REJECTED", "NO_LOW_PROBABILITY", True, "GOVERNANCE_AUDITED", 5))
        conn.execute(_worker_terminal_sql(), (run_id, "r1", "PENDING", None, False, "IDENTITY_VERIFIED", 1))
    assert _rows(dsn, run_id) == {"r1": ("HELD", HOLD_CODE)}
    assert _close(dsn, run_id)["held_rows"] == 1


def test_close_racing_blocked_worker_upsert_keeps_single_terminal(dsn):
    run_id = _seed(dsn, keys=("r1",))
    closer = _connect(dsn, autocommit=False)
    closer.execute("select public.wow_v17_close_pick_request_run_rows(%s, true)", (run_id,))
    done = threading.Event()

    def late_worker():
        with _connect(dsn) as conn:
            conn.execute(_worker_terminal_sql(), (run_id, "r1", "REJECTED", "NO_LOW_PROBABILITY", True, "GOVERNANCE_AUDITED", 5))
        done.set()

    thread = threading.Thread(target=late_worker)
    thread.start()
    time.sleep(0.5)
    assert not done.is_set(), "worker upsert must block on the closure's row lock"
    closer.commit()
    closer.close()
    thread.join(10)
    assert _rows(dsn, run_id) == {"r1": ("HELD", HOLD_CODE)}


def test_duplicate_close_is_idempotent(dsn):
    run_id = _seed(dsn)
    first = _close(dsn, run_id)
    second = _close(dsn, run_id)
    assert first["rows_held_at_closure"] == 3
    assert second["rows_held_at_closure"] == 0
    assert {k: second[k] for k in ("total_rows", "held_rows", "pending_rows")} == {
        "total_rows": 3, "held_rows": 3, "pending_rows": 0,
    }


def test_uncertain_write_rolled_back_then_retry_reconciles(dsn):
    run_id = _seed(dsn, keys=("r1", "r2"))
    lost = _connect(dsn, autocommit=False)
    lost.execute("select public.wow_v17_close_pick_request_run_rows(%s, true)", (run_id,))
    lost.rollback()
    lost.close()
    assert _rows(dsn, run_id) == {"r1": ("PENDING", None), "r2": ("PENDING", None)}
    out = _close(dsn, run_id)
    assert out["rows_held_at_closure"] == 2 and out["reconciliation_pass"] is True


def test_reopenable_and_open_runs_are_not_held(dsn):
    reopen_id = _seed(dsn, keys=("r1",), reopen=True)
    assert _close(dsn, reopen_id)["rows_held_at_closure"] == 0
    assert _rows(dsn, reopen_id) == {"r1": ("PENDING", None)}
    open_id = _seed(dsn, keys=("r1",))
    with _connect(dsn) as conn:
        conn.execute("update public.wow_pick_request_runs set closed_at=null, closure_code=null where run_id=%s", (open_id,))
    assert _close(dsn, open_id)["status"] == "RUN_NOT_CLOSED"
    assert _rows(dsn, open_id) == {"r1": ("PENDING", None)}


def test_client_roles_cannot_execute_closure_rpc(dsn):
    with _connect(dsn) as conn:
        for role in ("anon", "authenticated"):
            allowed = conn.execute(
                "select has_function_privilege(%s, 'public.wow_v17_close_pick_request_run_rows(text, boolean)', 'execute')",
                (role,),
            ).fetchone()[0]
            assert allowed is False
