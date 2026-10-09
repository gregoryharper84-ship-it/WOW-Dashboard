"""Certification-independent telemetry for canonical V17 Action invocations.

This module records only that a governed Action route was called and what HTTP
status it returned. It deliberately does not inspect request bodies or persist
players, lines, prices, probabilities, credentials, or certification state.

Presence of a telemetry row is never evidence that a model is certified,
publishable, rank eligible, or successful. Failed calls are recorded too.
Telemetry failure is isolated from request handling and can never change scoring,
terminal semantics, or ``can_execute=false``.

Receipt persistence is idempotent and recoverable. Each invocation receives a
server-generated UUID before persistence. Atomic, service-role-only database
RPCs serialize the primary receipt and recovery row with the same transaction
advisory lock. The HTTP transport owns its connect/read/write/pool deadlines;
Python never abandons a still-running write thread and starts an overlapping
retry. This keeps telemetry off the Action response critical path while
preventing ambiguous late commits from regressing durable recovery state.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4


# Persistence is always off the scoring response critical path. A finite bound
# prevents degraded PostgREST calls from owning background tasks forever.
_TRANSPORT_CONNECT_TIMEOUT_SECONDS = 2.0
_TRANSPORT_READ_TIMEOUT_SECONDS = 5.0
_TRANSPORT_WRITE_TIMEOUT_SECONDS = 5.0
_TRANSPORT_POOL_TIMEOUT_SECONDS = 2.0
_SHUTDOWN_DRAIN_SECONDS = 12.0
_MAX_IN_FLIGHT_WARNING = 32
_MAX_PERSIST_ATTEMPTS = 3
_RETRY_DELAYS_SECONDS = (0.25, 1.0)


LOGGER = logging.getLogger("wow.v17.action_invocation")
TABLE = "wow_action_invocation_receipts"
RECOVERY_TABLE = "wow_action_invocation_receipt_recovery"
RECORD_RECEIPT_RPC = "wow_record_action_invocation_receipt"
RECORD_RECOVERY_RPC = "wow_record_action_invocation_receipt_recovery"
RECONCILE_RECOVERY_RPC = "wow_reconcile_action_invocation_receipt_recovery"
CAN_EXECUTE = False
_GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

_RUN_PREFIX = "/v17/pick-request-runs/"
_RUN_STATE_ROUTE = "/v17/pick-request-runs/{request_id}"
_RUN_CLOSE_ROUTE = "/v17/pick-request-runs/{request_id}/close"

ROUTE_OPERATION_IDS = {
    "/score-prop": "scoreWowProp",
    "/score-pick-request": "scoreWowPickRequest",
    "/score-team-event-request": "scoreWowTeamEventRequest",
    "/score-team-event": "scoreWowV17TeamEventFromWowHost",
    "/v17/pick-request-runs/resumable": "runWowV17ResumablePickRequest",
}

_EXPLICIT_CALLER_CLASSES = frozenset({
    "GITHUB_ACTIONS",
    "RENDER_INTERNAL",
    "SELF_ACCEPTANCE",
    "OTHER",
})


def _route_metadata(path: str) -> tuple[str, str, str | None] | None:
    """Return normalized route, operation ID, and safe path request ID.

    Run-control state/close routes contain a user-provided request_id in the URL.
    Persist only the canonical route template while exposing that non-secret ID in
    the dedicated request_id column. The resumable POST remains body-opaque by
    design; its request_id is recorded only when the caller supplies the existing
    non-secret request-ID header.
    """
    operation_id = ROUTE_OPERATION_IDS.get(path)
    if operation_id is not None:
        return path, operation_id, None
    if not path.startswith(_RUN_PREFIX):
        return None
    suffix = path[len(_RUN_PREFIX):].strip("/")
    parts = suffix.split("/") if suffix else []
    if len(parts) == 1 and parts[0] and parts[0] != "resumable":
        return _RUN_STATE_ROUTE, "getWowV17PickRequestRunState", parts[0][:256]
    if len(parts) == 2 and parts[0] and parts[1] == "close":
        return _RUN_CLOSE_ROUTE, "closeWowV17PickRequestRun", parts[0][:256]
    return None


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


def _actual_rows_in(request: Any, headers: Any) -> int | None:
    """Read only request-local metadata set by the validated scoring handler."""
    state = getattr(request, "state", None)
    ctx = getattr(state, "wow_interactive_latency_context", None)
    if isinstance(ctx, dict):
        actual = ctx.get("row_count_exact")
        if isinstance(actual, int) and not isinstance(actual, bool) and 0 <= actual <= 10_000:
            return actual
    # Legacy calls without handler annotations may carry this optional count.
    return _rows_in(headers)


def _request_id(headers: Any) -> str | None:
    value = str(
        headers.get("x-wow-request-id") or headers.get("x-request-id") or ""
    ).strip()
    return value[:256] or None


def _configure_transport_timeouts(db: Any) -> Any:
    """Apply deadlines to the synchronous PostgREST HTTP transport.

    ``asyncio.wait_for(asyncio.to_thread(...))`` only stops waiting; it cannot
    stop the in-flight sync request. The Supabase client exposes its httpx
    transport through ``postgrest.session``. Setting the timeout there makes
    the transport terminate before control returns, so the next retry cannot
    overlap the prior write. Lightweight test doubles need no HTTP transport.
    """
    postgrest = getattr(db, "postgrest", None)
    session = getattr(postgrest, "session", None)
    if session is None or not hasattr(session, "timeout"):
        return db
    try:
        import httpx
    except ImportError:  # pragma: no cover - supabase-py depends on httpx
        return db
    session.timeout = httpx.Timeout(
        connect=_TRANSPORT_CONNECT_TIMEOUT_SECONDS,
        read=_TRANSPORT_READ_TIMEOUT_SECONDS,
        write=_TRANSPORT_WRITE_TIMEOUT_SECONDS,
        pool=_TRANSPORT_POOL_TIMEOUT_SECONDS,
    )
    return db


def _execute_rpc(
    db_client_fn: Callable[[], Any],
    name: str,
    params: dict[str, Any],
) -> Any:
    db = _configure_transport_timeouts(db_client_fn())
    return db.rpc(name, params).execute()


def _rpc_status(result: Any) -> str | None:
    """Extract one authoritative durable status from a Supabase RPC result."""
    data = getattr(result, "data", None)
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        return None
    value = str(data.get("status") or "").strip().upper()
    return value or None


def _record_receipt(db_client_fn: Callable[[], Any], receipt: dict[str, Any]) -> None:
    """Atomically insert immutable primary receipt and clear stale recovery."""
    _execute_rpc(db_client_fn, RECORD_RECEIPT_RPC, {"p_receipt": receipt})


def _record_recovery(
    db_client_fn: Callable[[], Any],
    receipt: dict[str, Any],
    *,
    attempt_count: int,
    error_type: str,
    state: str,
) -> str | None:
    result = _execute_rpc(
        db_client_fn,
        RECORD_RECOVERY_RPC,
        {
            "p_receipt": receipt,
            "p_attempt_count": attempt_count,
            "p_error_type": error_type[:128],
            "p_state": state,
        },
    )
    return _rpc_status(result)


async def _transport_bounded_thread_call(
    fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Await the sync call to completion; its HTTP transport owns the timeout."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _reconcile_stale_recovery(db_client_fn: Callable[[], Any]) -> None:
    """Delete bounded stale recovery rows whose immutable primary already won."""
    try:
        await _transport_bounded_thread_call(
            _execute_rpc,
            db_client_fn,
            RECONCILE_RECOVERY_RPC,
            {"p_limit": 100},
        )
    except Exception as exc:  # noqa: BLE001 - telemetry cannot block startup
        LOGGER.warning(
            "WOW_V17_ACTION_INVOCATION_RECOVERY_RECONCILE_FAILED "
            "typed_failure=PERSISTENCE_FAILURE error=%s can_execute=false",
            type(exc).__name__,
        )


async def _persist_with_recovery(
    db_client_fn: Callable[[], Any],
    receipt: dict[str, Any],
) -> None:
    """Persist one receipt with idempotent retry and durable recovery state."""
    last_exc: Exception | None = None
    terminal_recovery_state: str | None = None
    for attempt in range(1, _MAX_PERSIST_ATTEMPTS + 1):
        try:
            await _transport_bounded_thread_call(
                _record_receipt, db_client_fn, receipt
            )
            return
        except Exception as exc:  # noqa: BLE001 - persistence is fail-open
            last_exc = exc
            terminal_attempt = attempt >= _MAX_PERSIST_ATTEMPTS
            requested_state = "DEAD_LETTER" if terminal_attempt else "PENDING"
            durable_state: str | None = None
            try:
                durable_state = await _transport_bounded_thread_call(
                    _record_recovery,
                    db_client_fn,
                    receipt,
                    attempt_count=attempt,
                    error_type=type(exc).__name__,
                    state=requested_state,
                )
            except Exception as queue_exc:  # noqa: BLE001
                LOGGER.warning(
                    "WOW_V17_ACTION_INVOCATION_RECOVERY_QUEUE_FAILED "
                    "route=%s invocation_id=%s attempt=%s requested_state=%s "
                    "durable_state=UNCONFIRMED error=%s "
                    "typed_failure=PERSISTENCE_FAILURE can_execute=false",
                    receipt.get("route"),
                    receipt.get("invocation_id"),
                    attempt,
                    requested_state,
                    type(queue_exc).__name__,
                )

            # The primary receipt is authoritative. A transport timeout can occur
            # after the database commit; the recovery RPC uses the same advisory
            # lock and reports RECONCILED when it observes that canonical row.
            # In that case persistence succeeded durably, even on attempt 3.
            if durable_state == "RECONCILED":
                return

            if terminal_attempt:
                terminal_recovery_state = durable_state
                break
            await asyncio.sleep(_RETRY_DELAYS_SECONDS[attempt - 1])

    recovery_state = terminal_recovery_state or "UNCONFIRMED"
    LOGGER.warning(
        "WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED "
        "route=%s status_code=%s invocation_id=%s attempts=%s error=%s "
        "recovery_state=%s typed_failure=PERSISTENCE_FAILURE "
        "can_execute=false",
        receipt.get("route"),
        receipt.get("http_status"),
        receipt.get("invocation_id"),
        _MAX_PERSIST_ATTEMPTS,
        type(last_exc).__name__ if last_exc is not None else "UNKNOWN",
        recovery_state,
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
                "WOW_V17_ACTION_INVOCATION_TASK_FAILED "
                "typed_failure=PERSISTENCE_FAILURE can_execute=false"
            )

    @app.on_event("startup")
    async def _start_action_invocation_reconciliation() -> None:
        task = asyncio.create_task(_reconcile_stale_recovery(db_client_fn))
        tasks.add(task)
        task.add_done_callback(_task_done)

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
        metadata = _route_metadata(path)
        if metadata is None:
            return await call_next(request)
        normalized_route, operation_id, path_request_id = metadata
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
                "route": normalized_route,
                "action_operation_id": operation_id,
                "http_method": str(getattr(request, "method", "UNKNOWN")).upper(),
                "http_status": status_code,
                "auth_scheme": _auth_scheme(headers.get("authorization")),
                "caller_class": _caller_class(headers, status_code),
                "caller_user_agent": str(headers.get("user-agent") or "")[:256] or None,
                "request_id": _request_id(headers) or path_request_id,
                # Prefer the actual validated handler count over an optional,
                # caller-controlled header. Keep null when neither is known;
                # never infer a batch count from HTTP success.
                "rows_in": _actual_rows_in(request, headers),
                "duration_ms": round(duration_ms, 3),
                "can_execute": False,
            }
            if len(tasks) >= _MAX_IN_FLIGHT_WARNING:
                # Warning only: never drop a successfully completed invocation's
                # proof merely because the telemetry backlog is elevated.
                LOGGER.warning(
                    "WOW_V17_ACTION_INVOCATION_PERSISTENCE_BACKLOG_HIGH "
                    "route=%s status_code=%s in_flight=%s can_execute=false",
                    normalized_route,
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
    "RECORD_RECEIPT_RPC",
    "RECORD_RECOVERY_RPC",
    "RECOVERY_TABLE",
    "RECONCILE_RECOVERY_RPC",
    "ROUTE_OPERATION_IDS",
    "TABLE",
    "install_action_invocation_middleware",
]
