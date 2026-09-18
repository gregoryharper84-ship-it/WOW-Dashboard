"""Certification-independent invocation telemetry for canonical WOW Actions.

WOW-RUNTIME-ACTION-CANARY-NEVER-VERIFIED-011, repair P0-A.

WHY THIS EXISTS
``wow_prop_action_canary_receipts`` was being read as invocation telemetry. It
is not. That table is correctly gated behind an independently reviewed
certification release (``REVIEWED_CERTIFICATION_RELEASES``), which is
intentionally empty in production, so ``capture_action_canary_receipts``
returns ``NO_REVIEWED_CERTIFICATION_RELEASES`` before persisting anything. It
stays empty no matter how many Action calls succeed. Reading that emptiness as
"the Action was never invoked" produced a false P0 root cause.

This module answers the separate, non-governance question: did something call a
canonical Action route, from which caller class, and what did it get back?

DELIBERATE PROPERTIES
* It records FAILURES as readily as successes. A 401/404/422/409/500 is exactly
  the signal that was missing -- a request rejected by schema validation never
  reaches the endpoint at all, so route-level wrappers cannot see it. That is
  why this is middleware.
* It never depends on certification, publication eligibility, model success, or
  reconciliation. A row here grants nothing.
* It records the request envelope only. No credential, no bearer token, no API
  key, no request body, no player, line, market price or probability.
* It never alters sporting probability, terminal semantics, or execution
  authority, and never fails a request: a telemetry fault is swallowed into a
  log line, never raised into the governed API.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Optional

CAN_EXECUTE = False

LOGGER = logging.getLogger("wow.v17.action_invocation")
LEDGER_TABLE = "wow_action_invocation_receipts"
ENABLE_ENV = "WOW_ACTION_INVOCATION_LEDGER_ENABLED"
_INSTALLED_ATTR = "wow_action_invocation_telemetry_installed"
_MAX_USER_AGENT = 120

# Exactly the canonical Action surface. Kept in sync with the route check
# constraint in migrations/20260918_action_invocation_receipts.sql.
OPERATION_BY_ROUTE: dict[str, str] = {
    "/score-pick-request": "scoreWowPickRequest",
    "/score-team-event-request": "scoreWowTeamEventRequest",
    "/score-team-event": "scoreWowTeamEvent",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_auth_scheme(authorization: Optional[str]) -> str:
    """Classify the auth SCHEME only. The credential is never read or stored."""
    token = str(authorization or "").strip()
    if not token:
        return "NONE"
    scheme = token.split(" ", 1)[0].lower()
    if scheme == "bearer":
        return "BEARER"
    if scheme == "basic":
        return "BASIC"
    return "OTHER"


def classify_caller(user_agent: Optional[str]) -> str:
    """Classify the calling host.

    This is the field that separates a live Custom GPT invocation from CI or
    self-acceptance traffic -- the distinction the canary table could never
    express, and the one P0-B needs in order to be provable.
    """
    agent = str(user_agent or "").strip().lower()
    if not agent:
        return "UNKNOWN"
    if "chatgpt" in agent or "openai" in agent:
        return "CHATGPT_ACTION"
    if "github" in agent or "actions/" in agent:
        return "GITHUB_ACTIONS"
    if "wow-self-acceptance" in agent:
        return "SELF_ACCEPTANCE"
    if "render" in agent or "go-http-client" in agent:
        return "RENDER_INTERNAL"
    return "OTHER"


def _truncate(value: Optional[str], limit: int = _MAX_USER_AGENT) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def build_receipt(
    *,
    route: str,
    method: str,
    status_code: int,
    authorization: Optional[str],
    user_agent: Optional[str],
    request_id: Optional[str],
    rows_in: Optional[int],
    duration_ms: Optional[float],
) -> dict[str, Any]:
    """Build one invocation receipt. Pure; no I/O, no secrets."""
    return {
        "occurred_at": _utcnow(),
        "route": route,
        "action_operation_id": OPERATION_BY_ROUTE.get(route, "UNKNOWN"),
        "http_method": str(method or "UNKNOWN").upper(),
        "http_status": int(status_code),
        "auth_scheme": classify_auth_scheme(authorization),
        "caller_class": classify_caller(user_agent),
        "caller_user_agent": _truncate(user_agent),
        "request_id": _truncate(request_id, 128),
        "rows_in": int(rows_in) if isinstance(rows_in, int) and rows_in >= 0 else None,
        "duration_ms": round(float(duration_ms), 3) if duration_ms is not None else None,
        "can_execute": False,
    }


def ledger_enabled() -> bool:
    return os.getenv(ENABLE_ENV, "1") == "1"


def persist_receipt(db: Any, receipt: dict[str, Any]) -> bool:
    """Best-effort durable write. A failure is logged, never raised."""
    if db is None:
        return False
    try:
        db.table(LEDGER_TABLE).insert(receipt).execute()
        return True
    except Exception as exc:
        LOGGER.warning(
            "WOW_V17_ACTION_INVOCATION_PERSIST_FAILED route=%s error_type=%s can_execute=false",
            receipt.get("route"),
            type(exc).__name__,
        )
        return False


def record_invocation(receipt: dict[str, Any], *, db_client_fn: Optional[Callable[[], Any]]) -> bool:
    """Log the invocation always; persist it when a ledger client is available.

    The structured log line is emitted unconditionally so the signal exists in
    Render logs even before the ledger migration is applied, and even if the
    database is unreachable.
    """
    LOGGER.warning(
        "WOW_V17_ACTION_INVOCATION route=%s operation_id=%s method=%s status_code=%s "
        "caller_class=%s auth_scheme=%s request_id=%s rows_in=%s duration_ms=%s "
        "probability_publishable=false can_execute=false",
        receipt.get("route"),
        receipt.get("action_operation_id"),
        receipt.get("http_method"),
        receipt.get("http_status"),
        receipt.get("caller_class"),
        receipt.get("auth_scheme"),
        receipt.get("request_id"),
        receipt.get("rows_in"),
        receipt.get("duration_ms"),
    )
    if not ledger_enabled() or db_client_fn is None:
        return False
    try:
        db = db_client_fn()
    except Exception as exc:
        LOGGER.warning(
            "WOW_V17_ACTION_INVOCATION_CLIENT_UNAVAILABLE error_type=%s can_execute=false",
            type(exc).__name__,
        )
        return False
    return persist_receipt(db, receipt)


def install_action_invocation_telemetry(
    app: Any,
    *,
    db_client_fn: Optional[Callable[[], Any]] = None,
) -> bool:
    """Install one idempotent invocation probe over the canonical Action surface."""
    if getattr(app.state, _INSTALLED_ATTR, False):
        return True

    @app.middleware("http")
    async def _action_invocation_probe(request: Any, call_next: Any):
        path = str(getattr(request.url, "path", ""))
        if path not in OPERATION_BY_ROUTE:
            return await call_next(request)

        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            # Telemetry must never convert a served response into an error, and
            # must never mask an in-flight exception from the governed API.
            try:
                headers = getattr(request, "headers", {}) or {}
                rows_in = getattr(getattr(request, "state", None), "wow_action_rows_in", None)
                receipt = build_receipt(
                    route=path,
                    method=str(getattr(request, "method", "UNKNOWN")),
                    status_code=status_code,
                    authorization=headers.get("authorization"),
                    user_agent=headers.get("user-agent"),
                    request_id=headers.get("x-request-id") or headers.get("x-wow-request-id"),
                    rows_in=rows_in,
                    duration_ms=(perf_counter() - started) * 1000.0,
                )
                record_invocation(receipt, db_client_fn=db_client_fn)
            except Exception as exc:
                LOGGER.warning(
                    "WOW_V17_ACTION_INVOCATION_PROBE_FAILED route=%s error_type=%s can_execute=false",
                    path,
                    type(exc).__name__,
                )

    setattr(app.state, _INSTALLED_ATTR, True)
    return True


__all__ = [
    "CAN_EXECUTE",
    "LEDGER_TABLE",
    "OPERATION_BY_ROUTE",
    "build_receipt",
    "classify_auth_scheme",
    "classify_caller",
    "install_action_invocation_telemetry",
    "record_invocation",
]
