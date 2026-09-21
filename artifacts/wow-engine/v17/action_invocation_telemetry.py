"""Certification-independent telemetry for canonical V17 Action invocations.

This module records only that a governed Action route was called and what HTTP
status it returned. It deliberately does not inspect request bodies or persist
players, lines, prices, probabilities, credentials, or certification state.

Presence of a telemetry row is never evidence that a model is certified,
publishable, rank eligible, or successful. Failed calls are recorded too.
Telemetry failure is isolated from request handling and can never change scoring,
terminal semantics, or ``can_execute=false``.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from time import perf_counter
from typing import Any, Callable
import uuid


# Receipt persistence is deliberately off the response critical path. Each
# attempt is bounded, then retried with short backoff. A receipt that still
# cannot be written is persisted to a service-role-only dead-letter ledger for
# later recovery. None of these telemetry paths can change the Action response.
_PERSIST_TIMEOUT_SECONDS = 3.0
_PERSIST_MAX_ATTEMPTS = 3
_PERSIST_RETRY_DELAYS_SECONDS = (0.05, 0.15)
_DEAD_LETTER_TIMEOUT_SECONDS = 3.0
_SHUTDOWN_DRAIN_SECONDS = 5.25
_MAX_IN_FLIGHT = 32


LOGGER = logging.getLogger("wow.v17.action_invocation")
TABLE = "wow_action_invocation_receipts"
DEAD_LETTER_TABLE = "wow_action_invocation_receipt_dead_letters"
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
    value = str(headers.get("x-wow-request-id") or headers.get("x-request-id") or "").strip()
    return value[:256] or None


def _write_idempotent(table: Any, payload: dict[str, Any], *, conflict_key: str) -> None:
    """Prefer idempotent upsert; retain insert compatibility for unit-test fakes."""
    upsert = getattr(table, "upsert", None)
    if callable(upsert):
        upsert(payload, on_conflict=conflict_key).execute()
        return
    table.insert(payload).execute()


def _insert_receipt(db_client_fn: Callable[[], Any], receipt: dict[str, Any]) -> None:
    _write_idempotent(db_client_fn().table(TABLE), receipt, conflict_key="invocation_id")


def _dead_letter_receipt(
    db_client_fn: Callable[[], Any],
    receipt: dict[str, Any],
    *,
    attempt_count: int,
    last_error: str,
) -> None:
    payload = {
        "invocation_id": receipt["invocation_id"],
        "receipt": receipt,
        "attempt_count": int(attempt_count),
        "last_error": str(last_error)[:256],
        "can_execute": False,
    }
    _write_idempotent(
        db_client_fn().table(DEAD_LETTER_TABLE),
        payload,
        conflict_key="invocation_id",
    )


def install_action_invocation_middleware(app: Any, *, db_client_fn: Callable[[], Any]) -> None:
    """Install one fail-open invocation ledger probe on canonical Action routes."""
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
            LOGGER.exception("WOW_V17_ACTION_INVOCATION_TASK_FAILED can_execute=false")

    @app.on_event("shutdown")
    async def _drain_action_invocation_tasks() -> None:
        pending = tuple(tasks)
        if not pending:
            return
        _, still_pending = await asyncio.wait(pending, timeout=_SHUTDOWN_DRAIN_SECONDS)
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
                # Generate the identity before the first write so a timeout after
                # server commit is safe to retry without duplicating the receipt.
                "invocation_id": str(uuid.uuid4()),
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

            async def _persist_receipt() -> None:
                last_exc: Exception | None = None
                for attempt in range(1, _PERSIST_MAX_ATTEMPTS + 1):
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(_insert_receipt, db_client_fn, receipt),
                            timeout=_PERSIST_TIMEOUT_SECONDS,
                        )
                        if attempt > 1:
                            LOGGER.info(
                                "WOW_V17_ACTION_INVOCATION_PERSISTENCE_RECOVERED route=%s status_code=%s attempt=%s can_execute=false",
                                path, status_code, attempt,
                            )
                        return
                    except Exception as exc:
                        last_exc = exc
                        if attempt < _PERSIST_MAX_ATTEMPTS:
                            LOGGER.warning(
                                "WOW_V17_ACTION_INVOCATION_PERSISTENCE_RETRY route=%s status_code=%s attempt=%s error=%s can_execute=false",
                                path, status_code, attempt, type(exc).__name__,
                            )
                            await asyncio.sleep(_PERSIST_RETRY_DELAYS_SECONDS[attempt - 1])

                error_name = type(last_exc).__name__ if last_exc is not None else "UNKNOWN"
                LOGGER.warning(
                    "WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED route=%s status_code=%s attempts=%s error=%s can_execute=false",
                    path, status_code, _PERSIST_MAX_ATTEMPTS, error_name,
                )
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            _dead_letter_receipt,
                            db_client_fn,
                            receipt,
                            attempt_count=_PERSIST_MAX_ATTEMPTS,
                            last_error=error_name,
                        ),
                        timeout=_DEAD_LETTER_TIMEOUT_SECONDS,
                    )
                    LOGGER.warning(
                        "WOW_V17_ACTION_INVOCATION_DEAD_LETTERED route=%s status_code=%s invocation_id=%s can_execute=false",
                        path, status_code, receipt["invocation_id"],
                    )
                except Exception as dead_exc:
                    LOGGER.error(
                        "WOW_V17_ACTION_INVOCATION_DEAD_LETTER_FAILED route=%s status_code=%s error=%s can_execute=false",
                        path, status_code, type(dead_exc).__name__,
                    )

            if len(tasks) >= _MAX_IN_FLIGHT:
                LOGGER.warning(
                    "WOW_V17_ACTION_INVOCATION_PERSISTENCE_DROPPED route=%s status_code=%s reason=IN_FLIGHT_LIMIT can_execute=false",
                    path, status_code,
                )
            else:
                task = asyncio.create_task(_persist_receipt())
                tasks.add(task)
                task.add_done_callback(_task_done)

    app.state.wow_action_invocation_telemetry_installed = True


__all__ = [
    "CAN_EXECUTE",
    "ROUTE_OPERATION_IDS",
    "TABLE",
    "DEAD_LETTER_TABLE",
    "install_action_invocation_middleware",
]
