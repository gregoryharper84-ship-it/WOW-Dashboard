"""Process-backed lifecycle boundary for canonical WOW Daily runs.

This module intentionally does not score, classify, reconcile, reserve
exposure, or alter any governance decision.  It owns only acknowledgement,
idempotency, whole-run lifetime, and terminal manifest observability.
"""
from __future__ import annotations

import logging
import multiprocessing
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

can_execute = False
# A full live board previously reconciled 1,929 rows in about 25 minutes.
# Keep a hard bound while leaving enough time for that legitimate workload.
DEFAULT_DEADLINE_SECONDS = 45 * 60
REAPER_INTERVAL_SECONDS = 30

_processes: dict[str, multiprocessing.Process] = {}
_processes_lock = threading.Lock()
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
    """Close deadline-expired headers after a worker or server restart."""
    from storage.daily_manifest import reap_expired_runs
    ensure_manifest_ready()
    return reap_expired_runs(now=_now())


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


def _worker(
    *,
    run_id: str,
    sports: list[str] | None,
    environment: str,
    runtime_provenance: dict | None,
    session_id: str | None,
    deadline_at: str,
) -> None:
    """Run independently from the request process/connection."""
    try:
        from storage.daily_manifest import mark_progress
        mark_progress(
            run_id=run_id,
            stage="DISCOVERY",
            detail="Canonical source union started",
        )
        from gate_engine.daily_orchestrator import run_daily_orchestration
        run_daily_orchestration(
            run_id=run_id,
            sports=sports,
            environment=environment,
            runtime_provenance=runtime_provenance,
            session_id=session_id,
            deadline_at=deadline_at,
            persist=True,
        )
    except Exception as exc:
        logger.exception("canonical daily worker failed run=%s", run_id)
        _safe_terminalize(
            run_id=run_id,
            finished_at=_now(),
            run_status="DEGRADED",
            failure_reason=str(exc),
            failure_module="daily_run_lifecycle.worker",
        )


def _watch_deadline(
    *,
    run_id: str,
    process: multiprocessing.Process,
    deadline_at: str,
) -> None:
    """Hard-stop a hung child, then leave a terminal audit record."""
    try:
        deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        remaining = max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
        process.join(timeout=remaining)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            _safe_terminalize(
                run_id=run_id,
                finished_at=_now(),
                run_status="DEGRADED",
                failure_reason="WHOLE_RUN_DEADLINE_EXCEEDED",
                failure_module="daily_run_lifecycle.deadline_watch",
            )
    finally:
        with _processes_lock:
            _processes.pop(run_id, None)


def start_run(
    *,
    run_id: str | None,
    idempotency_key: str | None,
    sports: list[str] | None,
    environment: str,
    runtime_provenance: dict | None,
    session_id: str | None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Return one immutable run identity and start at most one worker."""
    from storage.daily_manifest import (
        claim_run,
        create_or_get_run,
    )

    if deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be positive")
    if not run_id and not idempotency_key:
        raise ValueError("idempotency_key or run_id is required")

    now = datetime.now(timezone.utc)
    requested_run_id = run_id or str(uuid.uuid4())
    requested_deadline = datetime.fromtimestamp(
        time.time() + deadline_seconds,
        tz=timezone.utc,
    ).isoformat()

    run, created = create_or_get_run(
        run_id=requested_run_id,
        idempotency_key=idempotency_key,
        run_date=now.date().isoformat(),
        started_at=now.isoformat(),
        deadline_at=requested_deadline,
        environment=environment,
        requested_sports=sports,
        session_id=session_id,
        runtime_provenance=runtime_provenance,
    )
    if run is None:
        raise RuntimeError("DAILY_RUN_MANIFEST_UNAVAILABLE")

    canonical_run_id = run["run_id"]
    canonical_deadline = _as_iso(run.get("deadline_at") or requested_deadline)
    claimed = run.get("run_status") == "ACCEPTED" and claim_run(canonical_run_id)
    if claimed:
        process = multiprocessing.Process(
            target=_worker,
            kwargs={
                "run_id": canonical_run_id,
                "sports": sports,
                "environment": environment,
                "runtime_provenance": runtime_provenance,
                "session_id": session_id,
                "deadline_at": canonical_deadline,
            },
            daemon=True,
            name=f"wow-daily-{canonical_run_id[:8]}",
        )
        try:
            process.start()
        except Exception as exc:
            _safe_terminalize(
                run_id=canonical_run_id,
                finished_at=_now(),
                run_status="DEGRADED",
                failure_reason=f"WORKER_START_FAILED:{exc}",
                failure_module="daily_run_lifecycle.start_run",
            )
            raise

        with _processes_lock:
            _processes[canonical_run_id] = process
        threading.Thread(
            target=_watch_deadline,
            kwargs={
                "run_id": canonical_run_id,
                "process": process,
                "deadline_at": canonical_deadline,
            },
            daemon=True,
            name=f"wow-daily-watch-{canonical_run_id[:8]}",
        ).start()

    current = dict(run)
    if claimed:
        current["run_status"] = "IN_PROGRESS"
        current["progress_stage"] = "STARTING"
    return {
        "ok": True,
        "accepted": True,
        "run_id": canonical_run_id,
        "run_status": current.get("run_status", "IN_PROGRESS"),
        "progress_stage": current.get("progress_stage", "STARTING"),
        "deadline_at": _as_iso(current.get("deadline_at") or canonical_deadline),
        "reused": not created,
        "can_execute": False,
    }