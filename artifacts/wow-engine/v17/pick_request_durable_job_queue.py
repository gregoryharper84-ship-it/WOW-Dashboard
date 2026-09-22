"""Durable DB-leased worker for V17 large-board prop scoring.

The Action request only freezes/enqueues the manifest and returns quickly. A
background worker then invokes the existing canonical /score-pick-request
endpoint one bounded batch at a time. Job state lives in Postgres, so client
disconnects and process restarts do not erase work. Atomic SKIP LOCKED leases
prevent two workers from owning the same job concurrently.

This module is orchestration only. It never creates sporting probability,
changes model/calibration logic, or enables wager execution.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from fastapi import HTTPException

import pick_request_runtime_core as pick_runtime
from v17 import pick_request_run_control as control
from v17 import pick_request_state_runtime as state
from v17.pick_request_run_control_hardening import (
    install_pick_request_run_control_hardening,
)

CAN_EXECUTE = False
JOB_TABLE = "wow_pick_request_jobs"
WORKER_VERSION = "V17_PICK_REQUEST_DURABLE_JOB_V1"
MAX_CONSECUTIVE_FAILURES = 5
LEASE_SECONDS = 1800
POLL_SECONDS = 2.0
_STATE_KEY = "wow_pick_request_durable_job_queue_installed"

_ORIGINAL_RUN_RESUMABLE = control.run_resumable
_ORIGINAL_READ_RUN_STATE = control.read_run_state
_ORIGINAL_CLOSE_RUN = control.close_run
_PATCHED = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_for_request(db: Any, request_id: str) -> dict[str, Any] | None:
    result = db.table(JOB_TABLE).select("*").eq("request_id", request_id).limit(1).execute()
    rows = list(getattr(result, "data", None) or [])
    return dict(rows[0]) if rows else None


def _public_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    keys = (
        "job_id",
        "request_id",
        "status",
        "response_mode",
        "batch_size",
        "lease_expires_at",
        "next_attempt_at",
        "consecutive_failures",
        "total_batches_completed",
        "total_rows_attempted",
        "receipt_recovered_count",
        "last_error",
        "created_at",
        "updated_at",
        "can_execute",
    )
    return {key: job.get(key) for key in keys}


def _upsert_job(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    result = db.table(JOB_TABLE).upsert(payload, on_conflict="request_id").execute()
    rows = list(getattr(result, "data", None) or [])
    if rows:
        return dict(rows[0])
    current = _job_for_request(db, str(payload["request_id"]))
    if current is None:
        raise RuntimeError("PICK_REQUEST_JOB_PERSISTENCE_FAILED")
    return current


def _update_job(db: Any, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    body["updated_at"] = _now()
    body["can_execute"] = False
    result = db.table(JOB_TABLE).update(body).eq("job_id", job_id).execute()
    rows = list(getattr(result, "data", None) or [])
    if rows:
        return dict(rows[0])
    return body


def _pending_count(db: Any, request_id: str) -> int:
    return len(control._pending_rows(db, request_id))


def enqueue_resumable(
    db: Any,
    request: control.ResumablePickRunRequest,
    *,
    score_fn: Callable[..., dict[str, Any]],
    model_identity: str | None,
) -> dict[str, Any]:
    """Freeze/enqueue and return without waiting for model scoring."""
    del score_fn  # worker owns scoring after enqueue
    control._seed_manifest(db, request)
    view = _ORIGINAL_READ_RUN_STATE(db, request.request_id, offset=0, limit=1)
    run = view["run"]
    if control._is_closed(run):
        raise control._closed_error(run, request.request_id)

    existing = _job_for_request(db, request.request_id)
    pending = _pending_count(db, request.request_id)
    if pending == 0:
        if existing:
            existing = _update_job(
                db,
                str(existing["job_id"]),
                {"status": "COMPLETED", "lease_owner": None, "lease_expires_at": None, "next_attempt_at": None},
            )
        return {
            "control_version": control.CONTROL_VERSION,
            "worker_version": WORKER_VERSION,
            "request_id": request.request_id,
            "run_status": run.get("run_status"),
            "rows_manifested": view["rows_total"],
            "pending_rows": 0,
            "job": _public_job(existing),
            "next_action": "RUN_TERMINAL",
            "can_execute": False,
        }

    if existing and str(existing.get("status")) == "RUNNING":
        return {
            "control_version": control.CONTROL_VERSION,
            "worker_version": WORKER_VERSION,
            "request_id": request.request_id,
            "run_status": run.get("run_status"),
            "rows_manifested": view["rows_total"],
            "pending_rows": pending,
            "job": _public_job(existing),
            "next_action": "POLL_RUN_STATE",
            "can_execute": False,
        }

    payload = dict(existing or {})
    payload.update(
        {
            "run_id": str(run["run_id"]),
            "request_id": request.request_id,
            "status": "QUEUED",
            "response_mode": request.response_mode,
            "batch_size": min(int(request.batch_size), 50),
            "model_identity": model_identity,
            "lease_owner": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "consecutive_failures": 0,
            "last_error": None,
            "can_execute": False,
            "updated_at": _now(),
        }
    )
    job = _upsert_job(db, payload)
    return {
        "control_version": control.CONTROL_VERSION,
        "worker_version": WORKER_VERSION,
        "request_id": request.request_id,
        "run_status": run.get("run_status"),
        "rows_manifested": view["rows_total"],
        "pending_rows": pending,
        "job": _public_job(job),
        "next_action": "POLL_RUN_STATE",
        "can_execute": False,
    }


def read_run_state_with_job(
    db: Any,
    request_id: str,
    *,
    offset: int = 0,
    limit: int = 100,
    include_outcomes: bool = False,
) -> dict[str, Any]:
    out = _ORIGINAL_READ_RUN_STATE(
        db,
        request_id,
        offset=offset,
        limit=limit,
        include_outcomes=include_outcomes,
    )
    out["job"] = _public_job(_job_for_request(db, request_id))
    out["can_execute"] = False
    return out


def close_run_with_job(
    db: Any,
    request_id: str,
    request: control.ClosePickRunRequest,
) -> dict[str, Any]:
    out = _ORIGINAL_CLOSE_RUN(db, request_id, request)
    job = _job_for_request(db, request_id)
    if job:
        _update_job(
            db,
            str(job["job_id"]),
            {
                "status": "STOPPED",
                "lease_owner": None,
                "lease_expires_at": None,
                "next_attempt_at": None,
                "last_error": {"code": request.closure_code, "reason": request.closure_reason},
            },
        )
    out["can_execute"] = False
    return out


def _claim_job(db: Any, worker_id: str) -> dict[str, Any] | None:
    result = db.rpc(
        "wow_claim_pick_request_job",
        {"p_worker_id": worker_id, "p_lease_seconds": LEASE_SECONDS},
    ).execute()
    rows = list(getattr(result, "data", None) or [])
    return dict(rows[0]) if rows else None


def _terminal_stop(
    db: Any,
    job: dict[str, Any],
    *,
    code: str,
    detail: dict[str, Any] | None = None,
) -> None:
    request_id = str(job["request_id"])
    reason = dict(detail or {})
    reason["code"] = code
    try:
        _ORIGINAL_CLOSE_RUN(
            db,
            request_id,
            control.ClosePickRunRequest(
                closure_code="STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED",
                closure_reason=code,
                closure_metadata={"worker_version": WORKER_VERSION, "typed_stop": reason},
            ),
        )
    finally:
        _update_job(
            db,
            str(job["job_id"]),
            {
                "status": "STOPPED",
                "lease_owner": None,
                "lease_expires_at": None,
                "next_attempt_at": None,
                "last_error": reason,
            },
        )


def _retry_or_stop(db: Any, job: dict[str, Any], error: dict[str, Any]) -> None:
    failures = int(job.get("consecutive_failures") or 0) + 1
    if failures >= MAX_CONSECUTIVE_FAILURES:
        _terminal_stop(db, job, code="RUN_WORKER_RETRY_BUDGET_EXHAUSTED", detail=error)
        return
    delay = (15, 30, 60, 120)[min(failures - 1, 3)]
    _update_job(
        db,
        str(job["job_id"]),
        {
            "status": "RETRY_WAIT",
            "lease_owner": None,
            "lease_expires_at": None,
            "next_attempt_at": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
            "consecutive_failures": failures,
            "last_error": error,
        },
    )


def _process_claimed_job(
    db: Any,
    job: dict[str, Any],
    *,
    score_fn: Callable[..., dict[str, Any]],
) -> None:
    request_id = str(job["request_id"])
    run = control._load_run_by_request_id(db, request_id)
    if control._is_closed(run):
        _update_job(
            db,
            str(job["job_id"]),
            {"status": "STOPPED", "lease_owner": None, "lease_expires_at": None, "next_attempt_at": None},
        )
        return

    pending = control._pending_rows(db, request_id)
    if not pending:
        _update_job(
            db,
            str(job["job_id"]),
            {"status": "COMPLETED", "lease_owner": None, "lease_expires_at": None, "next_attempt_at": None, "consecutive_failures": 0, "last_error": None},
        )
        return

    batch_size = max(1, min(int(job.get("batch_size") or 10), 50))
    selected = pending[:batch_size]
    retryable, recovered, blocker = control._receipt_preflight(db, request_id, selected)
    if blocker:
        _terminal_stop(db, job, code=str(blocker.get("code") or "RUN_RECEIPT_PREFLIGHT_BLOCKED"), detail=blocker)
        return

    if not retryable:
        still_pending = control._pending_rows(db, request_id)
        status = "QUEUED" if still_pending else "COMPLETED"
        _update_job(
            db,
            str(job["job_id"]),
            {
                "status": status,
                "lease_owner": None,
                "lease_expires_at": None,
                "next_attempt_at": None,
                "consecutive_failures": 0,
                "receipt_recovered_count": int(job.get("receipt_recovered_count") or 0) + recovered,
                "last_error": None,
            },
        )
        return

    rows: list[pick_runtime.PickRequestRow] = []
    for record in retryable:
        payload = record.get("request_payload")
        if not isinstance(payload, dict):
            _terminal_stop(
                db,
                job,
                code="RUN_ROW_REQUEST_PAYLOAD_MISSING",
                detail={"row_key": record.get("row_key")},
            )
            return
        rows.append(pick_runtime.PickRequestRow.model_validate(payload))

    try:
        result = score_fn(
            pick_runtime.PickRequestBatch(
                request_id=request_id,
                response_mode=str(job.get("response_mode") or "COMPACT"),
                rows=rows,
            ),
            job.get("model_identity"),
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"detail": str(exc.detail)}
        code = str(detail.get("code") or "RUN_SERVER_SCORER_HTTP_FAILURE")
        if exc.status_code in {409, 422} or code in {
            "RUN_ROW_IDENTITY_CONFLICT",
            "RUN_ROW_KEY_DUPLICATE",
            "RUN_CLOSED_REOPEN_REQUIRED",
        }:
            _terminal_stop(db, job, code=code, detail={**detail, "http_status": exc.status_code})
        else:
            _retry_or_stop(db, job, {"code": code, "http_status": exc.status_code, **detail})
        return
    except Exception as exc:
        _retry_or_stop(
            db,
            job,
            {"code": "RUN_SERVER_SCORER_EXCEPTION", "error_type": type(exc).__name__},
        )
        return

    if result.get("reconciliation_pass") is not True:
        _terminal_stop(
            db,
            job,
            code="RUN_BATCH_RECONCILIATION_FAILED",
            detail={"run_controller_status": result.get("run_controller_status")},
        )
        return

    still_pending = control._pending_rows(db, request_id)
    _update_job(
        db,
        str(job["job_id"]),
        {
            "status": "QUEUED" if still_pending else "COMPLETED",
            "lease_owner": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "consecutive_failures": 0,
            "total_batches_completed": int(job.get("total_batches_completed") or 0) + 1,
            "total_rows_attempted": int(job.get("total_rows_attempted") or 0) + len(rows),
            "receipt_recovered_count": int(job.get("receipt_recovered_count") or 0) + recovered,
            "last_error": None,
        },
    )


async def _worker_loop(
    *,
    db_client_fn: Callable[[], Any],
    score_fn: Callable[..., dict[str, Any]],
    worker_id: str,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            db = await asyncio.to_thread(db_client_fn)
            job = await asyncio.to_thread(_claim_job, db, worker_id)
            if job is None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
                continue
            await asyncio.to_thread(_process_claimed_job, db, job, score_fn=score_fn)
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass


def install_durable_pick_job_queue(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> None:
    global _PATCHED
    install_pick_request_run_control_hardening()
    if not _PATCHED:
        control.run_resumable = enqueue_resumable
        control.read_run_state = read_run_state_with_job
        control.close_run = close_run_with_job
        _PATCHED = True

    scheduled_key = f"{_STATE_KEY}_scheduled"
    if getattr(app.state, scheduled_key, False):
        return

    @app.on_event("startup")
    async def _start_worker() -> None:
        score_route = next(
            (
                route
                for route in app.router.routes
                if getattr(route, "path", None) == "/score-pick-request"
                and "POST" in (getattr(route, "methods", set()) or set())
            ),
            None,
        )
        if score_route is None or not callable(getattr(score_route, "endpoint", None)):
            return
        worker_id = f"{os.getenv('RENDER_INSTANCE_ID') or 'local'}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            _worker_loop(
                db_client_fn=db_client_fn,
                score_fn=score_route.endpoint,
                worker_id=worker_id,
                stop_event=stop_event,
            )
        )
        setattr(app.state, f"{_STATE_KEY}_stop", stop_event)
        setattr(app.state, f"{_STATE_KEY}_task", task)

    @app.on_event("shutdown")
    async def _stop_worker() -> None:
        stop_event = getattr(app.state, f"{_STATE_KEY}_stop", None)
        task = getattr(app.state, f"{_STATE_KEY}_task", None)
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    setattr(app.state, scheduled_key, True)


__all__ = [
    "CAN_EXECUTE",
    "JOB_TABLE",
    "WORKER_VERSION",
    "enqueue_resumable",
    "install_durable_pick_job_queue",
    "read_run_state_with_job",
]
