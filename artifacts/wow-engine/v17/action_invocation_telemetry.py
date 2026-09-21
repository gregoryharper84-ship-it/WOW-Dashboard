"""Certification-independent telemetry for canonical V17 Action invocations.

This module records only that a governed Action route was called and what HTTP
status it returned. It deliberately does not inspect request bodies or persist
players, lines, prices, probabilities, credentials, or certification state.

Presence of a telemetry row is never evidence that a model is certified,
publishable, rank eligible, or successful. Failed calls are recorded too.
Telemetry failure is isolated from request handling and can never change scoring,
terminal semantics, or ``can_execute=false``.

Receipt persistence is idempotent and recoverable. Each invocation receives a
server-generated UUID before persistence. If the primary receipt write fails or
its completion is ambiguous, the same safe receipt is queued in the server-only
Supabase recovery table, retried with the same UUID, and promoted to
``DEAD_LETTER`` only after bounded retry exhaustion. This keeps telemetry off the
Action response critical path while preventing a transient insert timeout from
silently changing a successfully completed invocation into "no proof exists".
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import json
import logging
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4


# Persistence is always off the scoring response critical path. A finite bound
# prevents degraded PostgREST calls from owning background tasks forever.
_PERSIST_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_DRAIN_SECONDS = 12.0
_MAX_IN_FLIGHT_WARNING = 32
_MAX_PERSIST_ATTEMPTS = 3
_RETRY_DELAYS_SECONDS = (0.25, 1.0)


LOGGER = logging.getLogger("wow.v17.action_invocation")
TABLE = "wow_action_invocation_receipts"
RECOVERY_TABLE = "wow_action_invocation_receipt_recovery"
CAN_EXECUTE = False
_GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

ROUTE_OPERATION_IDS = {
    "/score-prop": "scoreWowProp",
    "/score-pick-request": "scoreWowPickRequest",
    "/score-team-event-request": "scoreWowTeamEventRequest",
    "/score-team-event": "scoreWowV17TeamEventFromWowHost",
}

_EXPLICIT_CALLER_CLASSES = frozenset({
    "GITHUB_ACTIONS",
    "RENDER_INTERNAL",
    "SELF_ACCEPTANCE",
    "OTHER",
})


def _auth_scheme(value: str | None) -> str:
    token = str(value or "").strip().split(" ", 1)[0].upper()
    if token in {"BEARER", "BASIC"}:
        return token
    return "NONE" if not token else "OTHER"


def _jwt_issuer(authorization: str | None) -> str | None:
    """Read only JWT issuer for telemetry classification; never authorize here."""
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        return None
    token = value.split(" ", 1)[1].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_segment = parts[1]
        payload_segment += "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment.encode("ascii")))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    issuer = str(payload.get("iss") or "").strip()
    return issuer or None


def _caller_class(headers: Any, status_code: int) -> str:
    """Classify caller for observability only; never use this for authorization."""
    authorization = headers.get("authorization")
    auth_scheme = _auth_scheme(authorization)
    if status_code == 401:
        return "UNKNOWN"
    if _jwt_issuer(authorization) == _GITHUB_OIDC_ISSUER:
        return "GITHUB_ACTIONS"
    user_agent = str(headers.get("user-agent") or "")
    lowered = user_agent.casefold()
    if auth_scheme == "BEARER" and ("openai" in lowered or "chatgpt" in lowered):
        return "CHATGPT_ACTION"
    explicit = str(headers.get("x-wow-caller-class") or "").strip().upper()
    if explicit in _EXPLICIT_CALLER_CLASSES:
        return explicit
    if "github" in lowered and "action" in lowered:
        return "GITHUB_ACTIONS"
    if auth_scheme == "BEARER":
        return "ACTION_API_KEY"
    return "UNKNOWN"


def _rows_in(headers: Any) -> int | None:
    raw = str(headers.get("x-wow-rows-in") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _request_id(headers: Any) -> str | None:
    value = str(
        headers.get("x-wow-request-id") or headers.get("x-request-id") or ""
    ).strip()
    return value[:256] or None


def _upsert_receipt(db_client_fn: Callable[[], Any], receipt: dict[str, Any]) -> None:
    """Idempotently persist one invocation using its server-generated UUID."""
    table = db_client_fn().table(TABLE)
    upsert = getattr(table, "upsert", None)
    if callable(upsert):
        upsert(receipt, on_conflict="invocation_id").execute()
        return
    # Compatibility seam for simple test doubles. Production Supabase clients
    # expose upsert; the explicit UUID still makes duplicate completion visible.
    table.insert(receipt).execute()


def _upsert_recovery(
    db_client_fn: Callable[[], Any],
    receipt: dict[str, Any],
    *,
    attempt_count: int,
    error_type: str,
    state: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "invocation_id": receipt["invocation_id"],
        "updated_at": now,
        "attempt_count": attempt_count,
        "state": state,
        "last_error_type": error_type[:128],
        "receipt": receipt,
        "can_execute": False,
    }
    table = db_client_fn().table(RECOVERY_TABLE)
    upsert = getattr(table, "upsert", None)
    if callable(upsert):
        upsert(payload, on_conflict="invocation_id").execute()
        return
    table.insert(payload).execute()


def _delete_recovery(db_client_fn: Callable[[], Any], invocation_id: str) -> None:
    table = db_client_fn().table(RECOVERY_TABLE)
    delete = getattr(table, "delete", None)
    if not callable(delete):
        return
    delete().eq("invocation_id", invocation_id).execute()


async def _bounded_thread_call(fn: Callable[..., None], *args: Any, **kwargs: Any) -> None:
    await asyncio.wait_for(
        asyncio.to_thread(fn, *args, **kwargs),
        timeout=_PERSIST_TIMEOUT_SECONDS,
    )


async def _persist_with_recovery(
    db_client_fn: Callable[[], Any],
    receipt: dict[str, Any],
) -> None:
    """Persist one receipt with idempotent retry and durable recovery state."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_PERSIST_ATTEMPTS + 1):
        try:
            await _bounded_thread_call(_upsert_receipt, db_client_fn, receipt)
            if attempt > 1:
                try:
                    await _bounded_thread_call(
                        _delete_recovery,
                        db_client_fn,
                        str(receipt["invocation_id"]),
                    )
                except Exception as cleanup_exc:  # queue row is safe if stale
                    LOGGER.warning(
                        "WOW_V17_ACTION_INVOCATION_RECOVERY_CLEANUP_FAILED "
                        "route=%s invocation_id=%s error=%s can_execute=false",
                        receipt.get("route"),
                        receipt.get("invocation_id"),
                        type(cleanup_exc).__name__,
                    )
            return
        except Exception as exc:  # noqa: BLE001 - persistence is fail-open
            last_exc = exc
            terminal_attempt = attempt >= _MAX_PERSIST_ATTEMPTS
            state = "DEAD_LETTER" if terminal_attempt else "PENDING"
            try:
                await _bounded_thread_call(
                    _upsert_recovery,
                    db_client_fn,
                    receipt,
                    attempt_count=attempt,
                    error_type=type(exc).__name__,
                    state=state,
                )
            except Exception as queue_exc:  # noqa: BLE001
                LOGGER.warning(
                    "WOW_V17_ACTION_INVOCATION_RECOVERY_QUEUE_FAILED "
                    "route=%s invocation_id=%s attempt=%s state=%s error=%s "
                    "can_execute=false",
                    receipt.get("route"),
                    receipt.get("invocation_id"),
                    attempt,
                    state,
                    type(queue_exc).__name__,
                )
            if not terminal_attempt:
                await asyncio.sleep(_RETRY_DELAYS_SECONDS[attempt - 1])

    LOGGER.warning(
        "WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED "
        "route=%s status_code=%s invocation_id=%s attempts=%s error=%s "
        "recovery_state=DEAD_LETTER can_execute=false",
        receipt.get("route"),
        receipt.get("http_status"),
        receipt.get("invocation_id"),
        _MAX_PERSIST_ATTEMPTS,
        type(last_exc).__name__ if last_exc is not None else "UNKNOWN",
    )


def install_action_invocation_middleware(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> None:
    """Install one fail-open, recoverable invocation-ledger probe."""
    if getattr(app.state, "wow_action_invocation_telemetry_installed", False):
        return

    tasks: set[asyncio.Task[None]] = set()
    app.state.wow_action_invocation_tasks = tasks

    def _task_done(done: asyncio.Task[None]) -> None:
        tasks.discard(done)
        if done.cancelled():
            return
        try:
            done.exception()
        except Exception:
            LOGGER.exception(
                "WOW_V17_ACTION_INVOCATION_TASK_FAILED can_execute=false"
            )

    @app.on_event("shutdown")
    async def _drain_action_invocation_tasks() -> None:
        pending = tuple(tasks)
        if not pending:
            return
        _, still_pending = await asyncio.wait(
            pending,
            timeout=_SHUTDOWN_DRAIN_SECONDS,
        )
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)

    @app.middleware("http")
    async def _action_invocation_probe(request: Any, call_next: Any):
        path = str(getattr(request.url, "path", ""))
        operation_id = ROUTE_OPERATION_IDS.get(path)
        if operation_id is None:
            return await call_next(request)
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            duration_ms = (perf_counter() - started) * 1000.0
            headers = getattr(request, "headers", {})
            receipt = {
                "invocation_id": str(uuid4()),
                "route": path,
                "action_operation_id": operation_id,
                "http_method": str(getattr(request, "method", "UNKNOWN")).upper(),
                "http_status": status_code,
                "auth_scheme": _auth_scheme(headers.get("authorization")),
                "caller_class": _caller_class(headers, status_code),
                "caller_user_agent": str(headers.get("user-agent") or "")[:256] or None,
                "request_id": _request_id(headers),
                "rows_in": _rows_in(headers),
                "duration_ms": round(duration_ms, 3),
                "can_execute": False,
            }
            if len(tasks) >= _MAX_IN_FLIGHT_WARNING:
                # Warning only: never drop a successfully completed invocation's
                # proof merely because the telemetry backlog is elevated.
                LOGGER.warning(
                    "WOW_V17_ACTION_INVOCATION_PERSISTENCE_BACKLOG_HIGH "
                    "route=%s status_code=%s in_flight=%s can_execute=false",
                    path,
                    status_code,
                    len(tasks),
                )
            task = asyncio.create_task(
                _persist_with_recovery(db_client_fn, receipt)
            )
            tasks.add(task)
            task.add_done_callback(_task_done)

    app.state.wow_action_invocation_telemetry_installed = True


__all__ = [
    "CAN_EXECUTE",
    "RECOVERY_TABLE",
    "ROUTE_OPERATION_IDS",
    "TABLE",
    "install_action_invocation_middleware",
]
