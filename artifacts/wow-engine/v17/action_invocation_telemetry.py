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


LOGGER = logging.getLogger("wow.v17.action_invocation")
TABLE = "wow_action_invocation_receipts"
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

    # A 401 means authentication did not succeed, so transport hints cannot be
    # elevated into an attributable caller class.
    if status_code == 401:
        return "UNKNOWN"

    # GitHub OIDC is independently authenticated by the existing API auth layer.
    # We only inspect its issuer claim here to distinguish CI/synthetic traffic;
    # no token contents are persisted and this result grants no authority.
    if _jwt_issuer(authorization) == _GITHUB_OIDC_ISSUER:
        return "GITHUB_ACTIONS"

    user_agent = str(headers.get("user-agent") or "")
    lowered = user_agent.casefold()

    # The live GPT host is not allowed to self-assert CHATGPT_ACTION through a
    # custom header. Upgrade to CHATGPT_ACTION only when the authenticated
    # transport also presents an OpenAI/ChatGPT user-agent identity.
    if auth_scheme == "BEARER" and ("openai" in lowered or "chatgpt" in lowered):
        return "CHATGPT_ACTION"

    explicit = str(headers.get("x-wow-caller-class") or "").strip().upper()
    if explicit in _EXPLICIT_CALLER_CLASSES:
        return explicit

    if "github" in lowered and "action" in lowered:
        return "GITHUB_ACTIONS"

    # A successful/validated bearer request without stronger attributable
    # transport evidence is only ACTION_API_KEY. Never infer CHATGPT_ACTION from
    # bearer authentication alone.
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
        headers.get("x-wow-request-id")
        or headers.get("x-request-id")
        or ""
    ).strip()
    return value[:256] or None


def _insert_receipt(db_client_fn: Callable[[], Any], receipt: dict[str, Any]) -> None:
    db_client_fn().table(TABLE).insert(receipt).execute()


def install_action_invocation_middleware(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> None:
    """Install one fail-open invocation ledger probe on canonical Action routes."""
    if getattr(app.state, "wow_action_invocation_telemetry_installed", False):
        return

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
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(_insert_receipt, db_client_fn, receipt),
                    timeout=1.0,
                )
            except Exception as exc:
                LOGGER.warning(
                    "WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED route=%s status_code=%s "
                    "error=%s can_execute=false",
                    path,
                    status_code,
                    type(exc).__name__,
                )

    app.state.wow_action_invocation_telemetry_installed = True


__all__ = [
    "CAN_EXECUTE",
    "ROUTE_OPERATION_IDS",
    "TABLE",
    "install_action_invocation_middleware",
]
