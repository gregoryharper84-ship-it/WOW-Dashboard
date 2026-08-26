"""Process-backed lifecycle boundary for canonical WOW Daily runs.

This module intentionally does not score, classify, reconcile, reserve
exposure, or alter any governance decision.  It owns only acknowledgement,
idempotency, whole-run lifetime, and terminal manifest observability.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

can_execute = False
# A full live board previously reconciled 1,929 rows in about 25 minutes.
# Keep a hard bound while leaving enough time for that legitimate workload.
DEFAULT_DEADLINE_SECONDS = 45 * 60

# ---------------------------------------------------------------------------
# Shared asynchronous caller contract (Task #277)
# ---------------------------------------------------------------------------
# A Daily POST acknowledgement with a status in NON_TERMINAL_RUN_STATUSES is
# explicitly not a result: the caller must keep the server-generated run_id
# and poll GET /wow/daily/manifest/{run_id} for THAT SAME run until the
# manifest's run_status reaches one of TERMINAL_RUN_STATUSES.  A zero-row
# manifest whose run_status is non-terminal (progress_stage DISCOVERY /
# SCORING / etc.) is an in-progress run — never an empty-picks result.
TERMINAL_RUN_STATUSES = (
    "COMPLETE",
    "DEGRADED",
    "RECONCILIATION_WARNING",
    "FAILED",
)
NON_TERMINAL_RUN_STATUSES = ("ACCEPTED", "IN_PROGRESS")

# Validated, immutable Daily request scopes.
# FULL_BOARD                — canonical full discovery-to-reconciliation run.
# MONEYLINE_REMAINING_TODAY — narrow OUTRIGHT_WINNER /
#   OUTRIGHT_WIN_PROBABILITY_ONLY research over events still remaining on the
#   requested local date; the broader prop board is never acquired or scored.
SCOPE_FULL_BOARD = "FULL_BOARD"
SCOPE_MONEYLINE_REMAINING_TODAY = "MONEYLINE_REMAINING_TODAY"
DAILY_RUN_SCOPES = (SCOPE_FULL_BOARD, SCOPE_MONEYLINE_REMAINING_TODAY)


def is_terminal_run_status(run_status: object) -> bool:
    """Single shared predicate for the Daily terminal-versus-intermediate contract."""
    return str(run_status or "") in TERMINAL_RUN_STATUSES
REAPER_INTERVAL_SECONDS = 30
RUNNER_HEARTBEAT_SECONDS = 10

_reaper_started = False
_reaper_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _safe_terminalize(**kwargs: Any) -> None:
    try:
        from storage.daily_manifest import terminalize_run
        terminalize_run(**kwargs)
    except Exception:
        logger.exception("daily lifecycle could not terminalize run=%s", kwargs.get("run_id"))


def reap_expired_runs_once() -> int:
    """Close expired headers and stop their detached executor process groups."""
    from storage.daily_manifest import reap_expired_runs
    ensure_manifest_ready()
    reaped = reap_expired_runs(now=_now(), include_executor_records=True)
    if isinstance(reaped, int):  # Compatibility for narrow mocked callers.
        return reaped
    for record in reaped:
        _terminate_executor_process_group(
            run_id=str(record["run_id"]),
            executor_pid=record.get("executor_pid"),
        )
    return len(reaped)


def ensure_manifest_ready() -> None:
    """Create the manifest schema before the HTTP service begins accepting runs."""
    from storage.daily_manifest import ensure_tables
    if not ensure_tables():
        raise RuntimeError("DAILY_MANIFEST_UNAVAILABLE")


def _reaper_loop() -> None:
    while True:
        try:
            reap_expired_runs_once()
        except Exception:
            logger.exception("daily lifecycle reaper failed")
        time.sleep(REAPER_INTERVAL_SECONDS)


def start_manifest_reaper() -> None:
    """Start one restart-recovery reaper per serving process."""
    global _reaper_started
    with _reaper_lock:
        if _reaper_started:
            return
        threading.Thread(
            target=_reaper_loop,
            daemon=True,
            name="wow-daily-manifest-reaper",
        ).start()
        _reaper_started = True


def reset_after_fork() -> None:
    """Recreate reaper state in each Gunicorn worker after --preload forks."""
    global _reaper_started, _reaper_lock
    _reaper_started = False
    _reaper_lock = threading.Lock()


def _is_daily_executor_pid(pid: int) -> bool:
    """Avoid signaling a recycled PID that is not our detached executor."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as proc_cmdline:
            return b"gate_engine.daily_run_executor" in proc_cmdline.read()
    except OSError:
        return False


def _terminate_executor_process_group(
    *,
    run_id: str,
    executor_pid: object,
) -> None:
    """Send TERM then bounded KILL to a reaped executor's isolated session."""
    if not isinstance(executor_pid, int) or executor_pid <= 0:
        logger.warning("reaped daily run has no executor pid run=%s", run_id)
        return
    if not _is_daily_executor_pid(executor_pid):
        logger.info(
            "daily executor already exited or pid no longer matches run=%s pid=%s",
            run_id,
            executor_pid,
        )
        return
    try:
        os.killpg(executor_pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        logger.exception(
            "daily executor termination failed run=%s pid=%s",
            run_id,
            executor_pid,
        )
        return

    def _force_kill_if_still_running() -> None:
        time.sleep(5)
        if not _is_daily_executor_pid(executor_pid):
            return
        try:
            os.killpg(executor_pid, signal.SIGKILL)
            logger.warning(
                "daily executor force-killed after reaping run=%s pid=%s",
                run_id,
                executor_pid,
            )
        except ProcessLookupError:
            return
        except OSError:
            logger.exception(
                "daily executor force-kill failed run=%s pid=%s",
                run_id,
                executor_pid,
            )

    threading.Thread(
        target=_force_kill_if_still_running,
        daemon=True,
        name=f"wow-daily-kill-{run_id[:8]}",
    ).start()


def _reap_executor_child(
    process: subprocess.Popen,
    *,
    run_id: str,
    execution_owner: str,
) -> None:
    """Reap a detached child and close an unexpected pre-reporting failure."""
    try:
        exit_code = process.wait()
        logger.info(
            "daily executor child reaped run=%s exit_code=%s",
            run_id,
            exit_code,
        )
        if exit_code != 0:
            _safe_terminalize(
                run_id=run_id,
                finished_at=_now(),
                run_status="DEGRADED",
                failure_reason=f"RUNNER_UNEXPECTED_EXIT_{exit_code}",
                failure_module="daily_run_lifecycle._reap_executor_child",
                execution_owner=execution_owner,
            )
    except Exception:
        logger.exception("daily executor child reap failed run=%s", run_id)


def _launch_executor(*, run_id: str, execution_owner: str) -> subprocess.Popen:
    """
    Launch a fresh interpreter in its own session.

    The executor has no inherited Gunicorn locks, request context, or worker
    stdio pipes. A serving-worker restart can therefore not strand HTTP reads;
    if the executor itself is interrupted, its lease is closed by the reaper.
    """
    project_root = Path(__file__).resolve().parents[1]
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gate_engine.daily_run_executor",
            "--run-id",
            run_id,
            "--execution-owner",
            execution_owner,
        ],
        cwd=str(project_root),
        close_fds=True,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def start_run(
    *,
    run_id: str | None,
    idempotency_key: str | None,
    sports: list[str] | None,
    environment: str,
    runtime_provenance: dict | None,
    session_id: str | None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    intended_date: str | None = None,
    run_timezone: str = "UTC",
    scope: str = SCOPE_FULL_BOARD,
) -> dict[str, Any]:
    """Return one immutable run identity and start at most one worker.

    ``scope`` is part of the immutable request identity: it is validated,
    fingerprinted, and persisted at acknowledgement time.  Retrying the same
    idempotency key with a different scope raises
    ``IDEMPOTENCY_KEY_SCOPE_MISMATCH`` instead of mutating the stored run.
    """
    from storage.daily_manifest import (
        claim_run,
        create_or_get_run,
        register_executor,
    )

    if deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be positive")
    if not run_id and not idempotency_key:
        raise ValueError("idempotency_key or run_id is required")

    if scope not in DAILY_RUN_SCOPES:
        raise ValueError("INVALID_DAILY_SCOPE")

    now = datetime.now(timezone.utc)
    try:
        canonical_run_date = (
            date.fromisoformat(intended_date)
            if intended_date is not None
            else now.date()
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_INTENDED_DATE") from exc
    request_identity = {
        "date": canonical_run_date.isoformat(),
        "timezone": run_timezone,
        "scope": scope,
    }
    # The request-time instant is captured once at acknowledgement and stored
    # with the run; scoped executors read only this persisted value.
    scope_requested_at = now.isoformat()
    request_fingerprint = hashlib.sha256(
        json.dumps(request_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    requested_run_id = run_id or str(uuid.uuid4())
    requested_deadline = datetime.fromtimestamp(
        time.time() + deadline_seconds,
        tz=timezone.utc,
    ).isoformat()

    run, created = create_or_get_run(
        run_id=requested_run_id,
        idempotency_key=idempotency_key,
        run_date=canonical_run_date.isoformat(),
        started_at=now.isoformat(),
        deadline_at=requested_deadline,
        environment=environment,
        requested_sports=sports,
        session_id=session_id,
        runtime_provenance=runtime_provenance,
        run_timezone=run_timezone,
        request_fingerprint=request_fingerprint,
        request_scope=scope,
        scope_requested_at=scope_requested_at,
    )
    if run is None:
        raise RuntimeError("DAILY_RUN_MANIFEST_UNAVAILABLE")

    canonical_run_id = run["run_id"]
    canonical_deadline = _as_iso(run.get("deadline_at") or requested_deadline)
    execution_owner = str(uuid.uuid4())
    claimed = (
        run.get("run_status") == "ACCEPTED"
        and claim_run(canonical_run_id, execution_owner)
    )
    if claimed:
        try:
            executor_process = _launch_executor(
                run_id=canonical_run_id,
                execution_owner=execution_owner,
            )
            threading.Thread(
                target=_reap_executor_child,
                args=(executor_process,),
                kwargs={
                    "run_id": canonical_run_id,
                    "execution_owner": execution_owner,
                },
                daemon=True,
                name=f"wow-daily-child-reap-{canonical_run_id[:8]}",
            ).start()
            if not register_executor(
                run_id=canonical_run_id,
                execution_owner=execution_owner,
                executor_pid=executor_process.pid,
            ):
                _terminate_executor_process_group(
                    run_id=canonical_run_id,
                    executor_pid=executor_process.pid,
                )
                _safe_terminalize(
                    run_id=canonical_run_id,
                    finished_at=_now(),
                    run_status="DEGRADED",
                    failure_reason="RUNNER_PID_REGISTRATION_FAILED",
                    failure_module="daily_run_lifecycle.start_run",
                    execution_owner=execution_owner,
                )
                raise RuntimeError("DAILY_RUNNER_PID_REGISTRATION_FAILED")
        except Exception as exc:
            _safe_terminalize(
                run_id=canonical_run_id,
                finished_at=_now(),
                run_status="DEGRADED",
                failure_reason=f"RUNNER_LAUNCH_FAILED:{type(exc).__name__}",
                failure_module="daily_run_lifecycle.start_run",
            )
            raise

    current = dict(run)
    if claimed:
        current["run_status"] = "IN_PROGRESS"
        current["progress_stage"] = "STARTING"
    return {
        "ok": True,
        "accepted": True,
        "run_id": canonical_run_id,
        "run_date": canonical_run_date.isoformat(),
        "timezone": run_timezone,
        "run_status": current.get("run_status", "IN_PROGRESS"),
        "progress_stage": current.get("progress_stage", "STARTING"),
        "progress_detail": current.get("progress_detail"),
        "total_discovered": current.get("total_discovered"),
        # Deprecated response-only aliases.  Keep these derived from the
        # canonical fields above; callers cannot provide or mutate them.
        "latest_detail": current.get("progress_detail"),
        "rows_committed": current.get("total_discovered"),
        "deadline_at": _as_iso(current.get("deadline_at") or canonical_deadline),
        "reused": not created,
        "scope": str(current.get("request_scope") or scope),
        "terminal": is_terminal_run_status(current.get("run_status")),
        "can_execute": False,
    }