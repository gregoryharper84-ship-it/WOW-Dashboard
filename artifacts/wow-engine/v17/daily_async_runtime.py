"""Durable asynchronous V17 Daily submission and polling runtime.

The synchronous Daily scorer remains the single scoring implementation. This
module only changes transport/orchestration: submission persists a small run
request and returns immediately; a background worker claims it with a database
lease, executes the existing governed Daily runner, and stores the terminal result.
A process restart can reclaim an expired lease. No probability, terminal, or
execution authority is added here; can_execute remains false.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


LOGGER = logging.getLogger("wow.v17.daily_async")
TABLE = "wow_v17_daily_async_runs"
_WORKER_STATE_KEY = "wow_v17_daily_async_worker_installed"
_ROUTE_STATE_KEY = "wow_v17_daily_async_routes_installed"
CAN_EXECUTE = False


class AsyncDailySubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_slate_date: str
    requested_timezone: str = Field(min_length=1, max_length=64)
    lanes: list[Literal["PROPS", "MONEYLINE"]] = Field(default_factory=lambda: ["PROPS", "MONEYLINE"])
    max_props: int = Field(default=6, ge=0, le=12)
    max_team_events: int = Field(default=6, ge=0, le=12)
    response_mode: Literal["COMPACT", "FULL"] = "COMPACT"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _submit(db: Any, req: AsyncDailySubmitRequest) -> str:
    payload = req.model_dump(mode="json", exclude={"idempotency_key"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    idempotency_key = req.idempotency_key

    if idempotency_key:
        existing = (
            db.table(TABLE)
            .select("run_id,request_hash")
            .eq("idempotency_key", idempotency_key)
            .limit(1)
            .execute().data
            or []
        )
        if existing:
            row = existing[0]
            if not isinstance(row, dict) or row.get("request_hash") != request_hash:
                raise ValueError("DAILY_ASYNC_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST")
            return str(row["run_id"])

    run_id = f"v17-daily-async-{uuid4()}"
    db.table(TABLE).insert({
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "request_payload": payload,
        "run_status": "QUEUED",
        "can_execute": False,
    }).execute()
    return run_id


def _read(db: Any, run_id: str) -> dict[str, Any] | None:
    rows = (
        db.table(TABLE)
        .select(
            "run_id,run_status,submitted_at,started_at,completed_at,attempt_count,"
            "result_run_id,result_payload,last_error_code,can_execute"
        )
        .eq("run_id", run_id)
        .limit(1)
        .execute().data
        or []
    )
    return dict(rows[0]) if rows and isinstance(rows[0], dict) else None


def _claim(db: Any, lease_seconds: int) -> dict[str, Any] | None:
    result = db.rpc(
        "wow_claim_v17_daily_async_run",
        {"p_lease_seconds": lease_seconds},
    ).execute().data
    if not isinstance(result, dict) or result.get("status") != "CLAIMED":
        return None
    return dict(result)


def _complete(db: Any, *, run_id: str, lease_token: str, result: dict[str, Any]) -> dict[str, Any]:
    receipt = db.rpc(
        "wow_complete_v17_daily_async_run",
        {
            "p_run_id": run_id,
            "p_lease_token": lease_token,
            "p_result_run_id": str(result.get("run_id") or ""),
            "p_result_payload": result,
        },
    ).execute().data
    return dict(receipt) if isinstance(receipt, dict) else {"status": "INVALID_COMPLETION_RECEIPT"}


def _fail(db: Any, *, run_id: str, lease_token: str, error_code: str, max_attempts: int) -> dict[str, Any]:
    receipt = db.rpc(
        "wow_fail_v17_daily_async_run",
        {
            "p_run_id": run_id,
            "p_lease_token": lease_token,
            "p_error_code": error_code,
            "p_max_attempts": max_attempts,
        },
    ).execute().data
    return dict(receipt) if isinstance(receipt, dict) else {"status": "INVALID_FAILURE_RECEIPT"}


def _claim_from_factory(db_client_fn: Any, lease_seconds: int) -> dict[str, Any] | None:
    return _claim(db_client_fn(), lease_seconds)


def _complete_from_factory(
    db_client_fn: Any,
    *,
    run_id: str,
    lease_token: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return _complete(db_client_fn(), run_id=run_id, lease_token=lease_token, result=result)


def _fail_from_factory(
    db_client_fn: Any,
    *,
    run_id: str,
    lease_token: str,
    error_code: str,
    max_attempts: int,
) -> dict[str, Any]:
    return _fail(
        db_client_fn(),
        run_id=run_id,
        lease_token=lease_token,
        error_code=error_code,
        max_attempts=max_attempts,
    )


def _run_existing_daily(
    request_payload: dict[str, Any],
    *,
    db_client_fn: Any,
    market_api: Any,
    event_api: Any,
) -> dict[str, Any]:
    # Imported only when a worker actually claims a row. This avoids circular
    # import side effects while v17 package composition is still mounting routes.
    from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot

    request = DailySnapshotRequest(**request_payload)
    return run_daily_snapshot(
        request,
        db=db_client_fn(),
        market_api=market_api,
        event_api=event_api,
    )


async def _worker_loop(
    app: FastAPI,
    *,
    db_client_fn: Any,
    market_api: Any,
    event_api: Any,
) -> None:
    poll_seconds = _int_env("WOW_V17_DAILY_ASYNC_POLL_SECONDS", 2, minimum=1, maximum=30)
    lease_seconds = _int_env("WOW_V17_DAILY_ASYNC_LEASE_SECONDS", 900, minimum=60, maximum=3600)
    max_attempts = _int_env("WOW_V17_DAILY_ASYNC_MAX_ATTEMPTS", 3, minimum=1, maximum=10)
    wake: asyncio.Event = app.state.wow_v17_daily_async_wake

    while True:
        claim: dict[str, Any] | None = None
        try:
            claim = await asyncio.to_thread(_claim_from_factory, db_client_fn, lease_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning(
                "WOW_V17_DAILY_ASYNC_CLAIM_FAILED error=%s can_execute=false",
                type(exc).__name__,
            )

        if claim is None:
            try:
                await asyncio.wait_for(wake.wait(), timeout=float(poll_seconds))
                wake.clear()
            except TimeoutError:
                pass
            continue

        run_id = str(claim.get("run_id") or "")
        lease_token = str(claim.get("lease_token") or "")
        request_payload = claim.get("request_payload")
        if not run_id or not lease_token or not isinstance(request_payload, dict):
            LOGGER.error(
                "WOW_V17_DAILY_ASYNC_CLAIM_INVALID run_id=%s can_execute=false",
                run_id or "MISSING",
            )
            continue

        started = datetime.now(timezone.utc)
        LOGGER.warning(
            "WOW_V17_DAILY_ASYNC_RUN_STARTED run_id=%s attempt=%s can_execute=false",
            run_id,
            claim.get("attempt_count"),
        )
        try:
            result = await asyncio.to_thread(
                _run_existing_daily,
                dict(request_payload),
                db_client_fn=db_client_fn,
                market_api=market_api,
                event_api=event_api,
            )
            completion = await asyncio.to_thread(
                _complete_from_factory,
                db_client_fn,
                run_id=run_id,
                lease_token=lease_token,
                result=result,
            )
            LOGGER.warning(
                "WOW_V17_DAILY_ASYNC_RUN_FINISHED run_id=%s status=%s result_run_id=%s elapsed_seconds=%.3f can_execute=false",
                run_id,
                completion.get("status"),
                result.get("run_id"),
                (datetime.now(timezone.utc) - started).total_seconds(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_code = f"ASYNC_DAILY_WORKER_EXCEPTION:{type(exc).__name__}"
            try:
                failure = await asyncio.to_thread(
                    _fail_from_factory,
                    db_client_fn,
                    run_id=run_id,
                    lease_token=lease_token,
                    error_code=error_code,
                    max_attempts=max_attempts,
                )
                failure_status = failure.get("status")
            except Exception as persist_exc:
                failure_status = f"FAILURE_RECEIPT_ERROR:{type(persist_exc).__name__}"
            LOGGER.exception(
                "WOW_V17_DAILY_ASYNC_RUN_FAILED run_id=%s error=%s disposition=%s can_execute=false",
                run_id,
                type(exc).__name__,
                failure_status,
            )


def install_daily_async_routes(
    app: FastAPI,
    *,
    auth_callable: Any,
    db_client_fn: Any,
    market_api: Any,
    event_api: Any,
) -> bool:
    """Mount durable submit/status routes and one recoverable worker loop."""
    if getattr(app.state, _ROUTE_STATE_KEY, False):
        return True

    auth = Depends(auth_callable) if callable(auth_callable) else auth_callable

    @app.post(
        "/v17/daily-snapshot-submit",
        dependencies=[auth],
        operation_id="submitWowV17DailySnapshot",
        status_code=202,
    )
    def submit_daily_snapshot(req: AsyncDailySubmitRequest):
        try:
            run_id = _submit(db_client_fn(), req)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "DAILY_ASYNC_SUBMISSION_PERSISTENCE_FAILED",
                    "error_type": type(exc).__name__,
                    "can_execute": False,
                },
            ) from exc
        wake = getattr(app.state, "wow_v17_daily_async_wake", None)
        if isinstance(wake, asyncio.Event):
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(wake.set)
            except RuntimeError:
                pass
        return {
            "run_id": run_id,
            "run_status": "QUEUED",
            "terminal": False,
            "poll_path": f"/v17/daily-snapshot-run/{run_id}",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "can_execute": False,
        }

    @app.get(
        "/v17/daily-snapshot-run/{run_id}",
        dependencies=[auth],
        operation_id="getWowV17DailySnapshotRun",
    )
    def get_daily_snapshot_run(run_id: str):
        try:
            row = _read(db_client_fn(), run_id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "DAILY_ASYNC_STATUS_UNAVAILABLE",
                    "error_type": type(exc).__name__,
                    "can_execute": False,
                },
            ) from exc
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "DAILY_ASYNC_RUN_NOT_FOUND", "run_id": run_id, "can_execute": False},
            )
        return {
            "run_id": row["run_id"],
            "run_status": row["run_status"],
            "terminal": row["run_status"] in {"COMPLETED", "FAILED"},
            "submitted_at": row.get("submitted_at"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "attempt_count": row.get("attempt_count"),
            "result_run_id": row.get("result_run_id"),
            "result": row.get("result_payload"),
            "last_error_code": row.get("last_error_code"),
            "can_execute": False,
        }

    app.state.wow_v17_daily_async_wake = asyncio.Event()

    if not getattr(app.state, _WORKER_STATE_KEY, False):
        @app.on_event("startup")
        async def start_daily_async_worker() -> None:
            task = asyncio.create_task(
                _worker_loop(
                    app,
                    db_client_fn=db_client_fn,
                    market_api=market_api,
                    event_api=event_api,
                )
            )
            tasks = getattr(app.state, "wow_v17_daily_async_tasks", None)
            if tasks is None:
                tasks = set()
                app.state.wow_v17_daily_async_tasks = tasks
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        @app.on_event("shutdown")
        async def stop_daily_async_worker() -> None:
            tasks = tuple(getattr(app.state, "wow_v17_daily_async_tasks", set()) or ())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        app.state.wow_v17_daily_async_worker_installed = True

    app.state.wow_v17_daily_async_routes_installed = True
    return True


__all__ = [
    "AsyncDailySubmitRequest",
    "CAN_EXECUTE",
    "TABLE",
    "install_daily_async_routes",
]
