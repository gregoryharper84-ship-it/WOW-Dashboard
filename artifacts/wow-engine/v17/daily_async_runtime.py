"""Durable asynchronous completion wrapper for the public V17 Daily Action.

The canonical Daily scorer remains ``run_daily_snapshot`` and therefore keeps
all existing specialist ownership, calibration, row reconciliation, immutable
row-detail persistence and V17_TERMINAL_REDUCER semantics. This module changes
only transport/orchestration:

    submit -> durable receipt -> return -> background score -> poll result

Accepted work is persisted before the HTTP response. Background ownership uses a
bounded lease stored in Supabase, so a process restart leaves recoverable work
rather than an incomplete Action connection. The current Render web process can
execute the work immediately; a periodic sweeper reclaims queued/retryable or
expired-running submissions after a crash.

No probability is created here and ``can_execute`` is always false.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any, Optional
from uuid import uuid4

from fastapi import Header, HTTPException, Query

from v17.daily_response_contract import read_row_detail_page
from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot


LOGGER = logging.getLogger("wow.v17.daily_async")
TABLE = "wow_v17_daily_async_runs"
CAN_EXECUTE = False
TERMINAL_STATUSES = frozenset({"COMPLETED", "COMPLETED_WITH_BLOCKERS", "FAILED"})
RECOVERABLE_STATUSES = frozenset({"QUEUED", "RETRY_PENDING", "RUNNING"})
MAX_ATTEMPTS = 3
LEASE_SECONDS = 600
SWEEP_SECONDS = 20
_STATE_KEY = "wow_v17_daily_async_installed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _request_payload(req: DailySnapshotRequest) -> dict[str, Any]:
    return req.model_dump(mode="json")


def _request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _automatic_idempotency_key(request_hash: str, now: datetime | None = None) -> str:
    # Action retries caused by an ambiguous/disconnected submit normally occur
    # immediately. A one-minute bucket collapses those retries without turning a
    # later intentional refresh of the same slate into the same run forever.
    current = (now or _now()).astimezone(timezone.utc)
    bucket = current.strftime("%Y%m%dT%H%M")
    return f"AUTO:{request_hash}:{bucket}"


def _get_run(db: Any, run_id: str) -> dict[str, Any] | None:
    result = db.table(TABLE).select("*").eq("run_id", run_id).limit(1).execute()
    rows = result.data or []
    return dict(rows[0]) if rows else None


def _find_idempotent(db: Any, *, idempotency_key: str, request_hash: str) -> dict[str, Any] | None:
    result = (
        db.table(TABLE)
        .select("*")
        .eq("idempotency_key", idempotency_key)
        .eq("request_hash", request_hash)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return dict(rows[0]) if rows else None


def _create_submission(
    db: Any,
    *,
    req: DailySnapshotRequest,
    request_id: str | None,
) -> tuple[dict[str, Any], bool]:
    payload = _request_payload(req)
    request_hash = _request_hash(payload)
    idempotency_key = str(request_id or "").strip()[:256] or _automatic_idempotency_key(request_hash)
    existing = _find_idempotent(db, idempotency_key=idempotency_key, request_hash=request_hash)
    if existing is not None:
        return existing, True

    run_id = f"v17-daily-async-{uuid4()}"
    record = {
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "request_payload": payload,
        "status": "QUEUED",
        "attempt_count": 0,
        "can_execute": False,
    }
    try:
        result = db.table(TABLE).insert(record).execute()
        rows = result.data or []
        if not rows:
            raise RuntimeError("DAILY_ASYNC_SUBMISSION_INSERT_EMPTY")
        return dict(rows[0]), False
    except Exception:
        # Losing a concurrent unique-key race is safe: return the winner's
        # durable receipt rather than manufacturing a second run.
        existing = _find_idempotent(db, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing, True
        raise


def _claim_run(db: Any, run_id: str) -> tuple[dict[str, Any], str] | None:
    row = _get_run(db, run_id)
    if row is None or str(row.get("status")) in TERMINAL_STATUSES:
        return None

    status = str(row.get("status") or "")
    lease = _parse_time(row.get("lease_expires_at"))
    now = _now()
    if status == "RUNNING" and lease is not None and lease > now:
        return None
    if status not in RECOVERABLE_STATUSES:
        return None

    owner = str(uuid4())
    fields = {
        "status": "RUNNING",
        "attempt_count": int(row.get("attempt_count") or 0) + 1,
        "lease_owner": owner,
        "lease_expires_at": _iso(now + timedelta(seconds=LEASE_SECONDS)),
        "updated_at": _iso(now),
        "can_execute": False,
    }
    query = db.table(TABLE).update(fields).eq("run_id", run_id).eq("status", status)
    old_lease = row.get("lease_expires_at")
    if status == "RUNNING":
        query = query.eq("lease_expires_at", old_lease) if old_lease else query.is_("lease_expires_at", None)
    result = query.execute()
    rows = result.data or []
    if not rows:
        return None
    return dict(rows[0]), owner


def _public_result(result: dict[str, Any], *, submission_run_id: str) -> tuple[dict[str, Any], str | None]:
    payload = deepcopy(result)
    execution_run_id = str(payload.get("run_id") or "") or None
    payload["run_id"] = submission_run_id
    payload["execution_run_id"] = execution_run_id
    payload["async_submission"] = True
    payload["can_execute"] = False

    rows = payload.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            detail_ref = row.get("detail_ref")
            if isinstance(detail_ref, dict):
                detail_ref["run_id"] = submission_run_id

    retrieval = payload.get("row_detail_retrieval")
    if isinstance(retrieval, dict):
        retrieval["submission_run_id"] = submission_run_id
        retrieval["can_execute"] = False
    return payload, execution_run_id


def _terminal_status(result: dict[str, Any]) -> str:
    run_status = str(result.get("run_status") or "").upper()
    if run_status.startswith("RUN_INVALID") or run_status == "FAILED":
        return "FAILED"
    blockers = result.get("blockers")
    if "BLOCKER" in run_status or (isinstance(blockers, list) and bool(blockers)):
        return "COMPLETED_WITH_BLOCKERS"
    return "COMPLETED"


def _finish_run(
    db: Any,
    *,
    run_id: str,
    owner: str,
    status: str,
    result_payload: dict[str, Any] | None,
    execution_run_id: str | None,
    error_type: str | None = None,
) -> None:
    now = _iso(_now())
    fields = {
        "status": status,
        "result_payload": result_payload,
        "execution_run_id": execution_run_id,
        "last_error_type": error_type,
        "lease_owner": None,
        "lease_expires_at": None,
        "updated_at": now,
        "completed_at": now if status in TERMINAL_STATUSES else None,
        "can_execute": False,
    }
    (
        db.table(TABLE)
        .update(fields)
        .eq("run_id", run_id)
        .eq("status", "RUNNING")
        .eq("lease_owner", owner)
        .execute()
    )


def _execute_submission(
    *,
    run_id: str,
    db_client_fn: Any,
    market_api: Any,
    event_api: Any,
) -> str:
    db = db_client_fn()
    claimed = _claim_run(db, run_id)
    if claimed is None:
        current = _get_run(db, run_id) or {}
        return str(current.get("status") or "NOT_CLAIMED")
    row, owner = claimed

    try:
        req = DailySnapshotRequest.model_validate(row.get("request_payload") or {})
        result = run_daily_snapshot(req, db=db, market_api=market_api, event_api=event_api)
        public, execution_run_id = _public_result(result, submission_run_id=run_id)
        status = _terminal_status(public)
        _finish_run(
            db,
            run_id=run_id,
            owner=owner,
            status=status,
            result_payload=public,
            execution_run_id=execution_run_id,
        )
        return status
    except Exception as exc:  # noqa: BLE001 - orchestration failure stays typed
        attempts = int(row.get("attempt_count") or 1)
        terminal = attempts >= MAX_ATTEMPTS
        status = "FAILED" if terminal else "RETRY_PENDING"
        error_payload = {
            "run_id": run_id,
            "run_status": status,
            "failure_class": "DAILY_BACKGROUND_COMPLETION_FAILED",
            "error_type": type(exc).__name__,
            "probability_publishable": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }
        _finish_run(
            db,
            run_id=run_id,
            owner=owner,
            status=status,
            result_payload=error_payload if terminal else None,
            execution_run_id=None,
            error_type=type(exc).__name__,
        )
        LOGGER.exception(
            "WOW_V17_DAILY_ASYNC_FAILED run_id=%s attempt=%s status=%s can_execute=false",
            run_id,
            attempts,
            status,
        )
        return status


def _recoverable_runs(db: Any, *, limit: int = 20) -> list[str]:
    # Keep the query deliberately broad and filter lease freshness in-process so
    # it remains compatible with Supabase/PostgREST test doubles.
    result = db.table(TABLE).select("run_id,status,lease_expires_at").order("created_at").limit(limit).execute()
    now = _now()
    out: list[str] = []
    for row in result.data or []:
        status = str(row.get("status") or "")
        if status in {"QUEUED", "RETRY_PENDING"}:
            out.append(str(row["run_id"]))
        elif status == "RUNNING":
            lease = _parse_time(row.get("lease_expires_at"))
            if lease is None or lease <= now:
                out.append(str(row["run_id"]))
    return out


def _submission_receipt(row: dict[str, Any], *, reused: bool) -> dict[str, Any]:
    run_id = str(row.get("run_id") or "")
    status = str(row.get("status") or "QUEUED")
    if status in TERMINAL_STATUSES and isinstance(row.get("result_payload"), dict):
        return {
            **dict(row["result_payload"]),
            "submission_receipt_id": row.get("submission_receipt_id"),
            "async_submission": True,
            "reused": reused,
            "can_execute": False,
        }
    return {
        "run_id": run_id,
        "submission_receipt_id": row.get("submission_receipt_id"),
        "status": status,
        "terminal": False,
        "accepted": True,
        "reused": reused,
        "poll_operation_id": "readWowV17DailySnapshotRowDetail",
        "poll_url": f"/v17/daily-snapshot-run/{run_id}/rows?offset=0&limit=5",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def install_async_daily_snapshot_routes(
    app: Any,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
    event_api: Any,
) -> None:
    """Replace only the public Daily Action transport with durable async intake."""
    if getattr(app.state, _STATE_KEY, False):
        return

    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None)
        not in {"/v17/daily-snapshot-run", "/v17/daily-snapshot-run/{run_id}/rows", "/v17/daily-snapshot-run/{run_id}"}
    ]

    tasks: set[asyncio.Task[Any]] = set()
    app.state.wow_v17_daily_async_tasks = tasks
    semaphore = asyncio.Semaphore(1)

    async def _run_one(run_id: str) -> None:
        async with semaphore:
            status = await asyncio.to_thread(
                _execute_submission,
                run_id=run_id,
                db_client_fn=db_client_fn,
                market_api=market_api,
                event_api=event_api,
            )
        if status == "RETRY_PENDING":
            await asyncio.sleep(1.0)
            _schedule(run_id)

    def _done(task: asyncio.Task[Any]) -> None:
        tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            LOGGER.exception("WOW_V17_DAILY_ASYNC_TASK_FAILED can_execute=false")

    def _schedule(run_id: str) -> None:
        task = asyncio.create_task(_run_one(run_id))
        tasks.add(task)
        task.add_done_callback(_done)

    @app.post(
        "/v17/daily-snapshot-run",
        dependencies=[auth_dependency],
        operation_id="runWowV17DailySnapshot",
    )
    async def submit_daily_snapshot(
        req: DailySnapshotRequest,
        x_wow_request_id: Optional[str] = Header(default=None, alias="X-WOW-Request-ID"),
    ):
        try:
            row, reused = await asyncio.to_thread(
                _create_submission,
                db_client_fn(),
                req=req,
                request_id=x_wow_request_id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "DAILY_ASYNC_SUBMISSION_PERSISTENCE_FAILED",
                    "error_type": type(exc).__name__,
                    "probability_publishable": False,
                    "can_execute": False,
                },
            ) from exc
        if str(row.get("status") or "") not in TERMINAL_STATUSES:
            _schedule(str(row["run_id"]))
        return _submission_receipt(row, reused=reused)

    @app.get(
        "/v17/daily-snapshot-run/{run_id}",
        dependencies=[auth_dependency],
        operation_id="getWowV17DailySnapshotRun",
    )
    async def get_daily_snapshot_run(run_id: str):
        row = await asyncio.to_thread(_get_run, db_client_fn(), run_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "DAILY_ASYNC_RUN_NOT_FOUND", "can_execute": False})
        if str(row.get("status") or "") in TERMINAL_STATUSES and isinstance(row.get("result_payload"), dict):
            return {**dict(row["result_payload"]), "submission_receipt_id": row.get("submission_receipt_id"), "terminal": True, "can_execute": False}
        return _submission_receipt(row, reused=True)

    @app.get(
        "/v17/daily-snapshot-run/{run_id}/rows",
        dependencies=[auth_dependency],
        operation_id="readWowV17DailySnapshotRowDetail",
    )
    async def read_daily_snapshot_rows(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=5, ge=1, le=25),
    ):
        db = db_client_fn()
        row = await asyncio.to_thread(_get_run, db, run_id)
        if row is None:
            # Backward compatibility for completed synchronous Daily IDs created
            # before async transport activation.
            return await asyncio.to_thread(read_row_detail_page, db, run_id=run_id, offset=offset, limit=limit)
        status = str(row.get("status") or "")
        if status not in TERMINAL_STATUSES:
            return {**_submission_receipt(row, reused=True), "rows": [], "offset": offset, "limit": limit}
        execution_run_id = str(row.get("execution_run_id") or "")
        if not execution_run_id:
            return {
                "run_id": run_id,
                "status": status,
                "terminal": True,
                "rows": [],
                "blockers": ["DAILY_EXECUTION_RUN_ID_UNAVAILABLE"],
                "can_execute": False,
            }
        page = await asyncio.to_thread(
            read_row_detail_page,
            db,
            run_id=execution_run_id,
            offset=offset,
            limit=limit,
        )
        return {**page, "run_id": run_id, "execution_run_id": execution_run_id, "terminal": True, "status": status, "can_execute": False}

    async def _sweeper() -> None:
        while True:
            try:
                run_ids = await asyncio.to_thread(_recoverable_runs, db_client_fn())
                for run_id in run_ids:
                    _schedule(run_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("WOW_V17_DAILY_ASYNC_SWEEP_FAILED can_execute=false")
            await asyncio.sleep(SWEEP_SECONDS)

    app.state.wow_v17_daily_async_sweeper = asyncio.create_task(_sweeper())
    app.state.wow_v17_daily_async_schedule = _schedule
    setattr(app.state, _STATE_KEY, True)


def schedule_async_daily_snapshot_install(
    app: Any,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
    event_api: Any,
) -> None:
    """Install after module import finishes so the sync Daily route already exists."""
    if getattr(app.state, "wow_v17_daily_async_install_scheduled", False):
        return

    @app.on_event("startup")
    async def _install_daily_async_transport() -> None:
        install_async_daily_snapshot_routes(
            app,
            auth_dependency=auth_dependency,
            db_client_fn=db_client_fn,
            market_api=market_api,
            event_api=event_api,
        )

    @app.on_event("shutdown")
    async def _shutdown_daily_async_transport() -> None:
        sweeper = getattr(app.state, "wow_v17_daily_async_sweeper", None)
        if sweeper is not None:
            sweeper.cancel()
            await asyncio.gather(sweeper, return_exceptions=True)
        tasks = tuple(getattr(app.state, "wow_v17_daily_async_tasks", set()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    app.state.wow_v17_daily_async_install_scheduled = True


__all__ = [
    "CAN_EXECUTE",
    "TABLE",
    "install_async_daily_snapshot_routes",
    "schedule_async_daily_snapshot_install",
]
