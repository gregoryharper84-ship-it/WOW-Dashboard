"""Hidden persistence bridge for the resident WOW Engineering Auditor.

The Render worker intentionally does not receive the Supabase service-role key.
This internal route runs inside the governed scorer process, where the existing
server-side Supabase client already lives, and exposes only the tiny persistence
surface used by ``EngineeringAuditStore``.

This route is not part of the Custom GPT/OpenAPI action surface. It has no
sporting probability authority and can never execute a wager or market order.
"""
from __future__ import annotations

import hmac
import os
import re
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
_ALLOWED_TABLES = frozenset(
    {
        "wow_agent_audit_events",
        "wow_engineering_audit_work_items",
        "wow_engineering_audit_findings",
        "wow_engineering_backlog",
        "wow_engineering_auditor_runtime",
    }
)
_ALLOWED_OPERATIONS = frozenset({"select", "insert", "upsert", "update"})
_ALLOWED_FILTERS = frozenset({"eq", "lte"})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_FIELDS = frozenset(
    {
        "model_probability",
        "sporting_probability",
        "projected_probability",
        "calibrated_probability",
        "implied_probability",
        "no_vig_probability",
        "probability_override",
        "probability_blend",
        "pick_probability",
        "wager",
        "market_order",
    }
)
_CONFLICT_KEYS = {
    "wow_engineering_audit_work_items": frozenset({"fingerprint"}),
    "wow_engineering_audit_findings": frozenset({"fingerprint"}),
    "wow_engineering_backlog": frozenset({"ticket_id"}),
    "wow_engineering_auditor_runtime": frozenset({"auditor_id"}),
}


class EngineeringAuditFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: str
    column: str
    value: Any = None


class EngineeringAuditBridgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    operation: str
    columns: str = "*"
    payload: dict[str, Any] | None = None
    on_conflict: str | None = None
    filters: list[EngineeringAuditFilter] = Field(default_factory=list, max_length=8)
    limit: int | None = Field(default=None, ge=1, le=1000)
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY


def _valid_identifier(value: str) -> bool:
    return bool(_IDENTIFIER.fullmatch(str(value or "").strip()))


def _validate_columns(value: str) -> None:
    if value == "*":
        return
    columns = [item.strip() for item in value.split(",") if item.strip()]
    if not columns or not all(_valid_identifier(column) for column in columns):
        raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_INVALID_COLUMNS"})


def _find_forbidden(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            child = f"{path}.{key}"
            if key in _FORBIDDEN_FIELDS:
                return child
            found = _find_forbidden(nested, child)
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _find_forbidden(nested, f"{path}[{index}]")
            if found:
                return found
    return None


def _require_bridge_token(provided: str | None) -> None:
    expected = os.getenv("WOW_ENGINEERING_AUDIT_BRIDGE_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail={"code": "ENGINEERING_AUDIT_BRIDGE_DISABLED"})
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail={"code": "ENGINEERING_AUDIT_BRIDGE_UNAUTHORIZED"})


def _validate_request(req: EngineeringAuditBridgeRequest) -> None:
    if req.can_execute is not False or req.terminal_authority != TERMINAL_AUTHORITY:
        raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_GOVERNANCE_BOUNDARY_REJECTED"})
    if req.table not in _ALLOWED_TABLES:
        raise HTTPException(status_code=403, detail={"code": "ENGINEERING_AUDIT_TABLE_FORBIDDEN"})

    operation = req.operation.lower()
    if operation not in _ALLOWED_OPERATIONS:
        raise HTTPException(status_code=403, detail={"code": "ENGINEERING_AUDIT_OPERATION_FORBIDDEN"})
    _validate_columns(req.columns)

    for item in req.filters:
        if item.operator.lower() not in _ALLOWED_FILTERS or not _valid_identifier(item.column):
            raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_FILTER_FORBIDDEN"})

    if operation == "update" and not req.filters:
        raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_UNFILTERED_UPDATE_FORBIDDEN"})

    if operation in {"insert", "upsert", "update"}:
        payload = req.payload or {}
        if not payload:
            raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_PAYLOAD_REQUIRED"})
        if not all(_valid_identifier(key) for key in payload):
            raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_PAYLOAD_KEY_FORBIDDEN"})
        forbidden = _find_forbidden(payload)
        if forbidden:
            raise HTTPException(
                status_code=400,
                detail={"code": "ENGINEERING_AUDIT_PROBABILITY_BOUNDARY_VIOLATION", "path": forbidden},
            )

        if req.table == "wow_agent_audit_events":
            if payload.get("can_execute") is not False:
                raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_CAN_EXECUTE_MUST_BE_FALSE"})
        elif req.table in {
            "wow_engineering_audit_work_items",
            "wow_engineering_audit_findings",
            "wow_engineering_auditor_runtime",
        }:
            if payload.get("can_execute") is not False:
                raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_CAN_EXECUTE_MUST_BE_FALSE"})
            if payload.get("terminal_authority") != TERMINAL_AUTHORITY:
                raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_TERMINAL_AUTHORITY_MISMATCH"})
        elif req.table == "wow_engineering_backlog":
            if operation in {"insert", "upsert"}:
                if payload.get("source") != "ENGINEERING_AUDITOR" or not str(payload.get("ticket_id") or "").startswith("AUDIT-"):
                    raise HTTPException(status_code=403, detail={"code": "ENGINEERING_AUDIT_BACKLOG_WRITE_FORBIDDEN"})
            else:
                ticket_filters = [
                    item
                    for item in req.filters
                    if item.operator.lower() == "eq" and item.column == "ticket_id"
                ]
                if len(ticket_filters) != 1 or not str(ticket_filters[0].value or "").startswith("AUDIT-"):
                    raise HTTPException(status_code=403, detail={"code": "ENGINEERING_AUDIT_BACKLOG_UPDATE_FORBIDDEN"})

    if operation == "upsert":
        keys = [item.strip() for item in str(req.on_conflict or "").split(",") if item.strip()]
        if not keys or frozenset(keys) != _CONFLICT_KEYS.get(req.table, frozenset()):
            raise HTTPException(status_code=400, detail={"code": "ENGINEERING_AUDIT_CONFLICT_KEY_FORBIDDEN"})


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def install_engineering_audit_bridge_routes(
    app: FastAPI,
    *,
    db_client_fn: Callable[[], Any],
) -> None:
    path = "/internal/engineering-audit-store"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(path, include_in_schema=False)
    def engineering_audit_store(
        req: EngineeringAuditBridgeRequest,
        x_wow_engineering_token: str | None = Header(default=None, alias="X-WOW-Engineering-Token"),
    ) -> dict[str, Any]:
        _require_bridge_token(x_wow_engineering_token)
        _validate_request(req)

        try:
            query = db_client_fn().table(req.table)
            operation = req.operation.lower()
            if operation == "select":
                query = query.select(req.columns)
            elif operation == "insert":
                query = query.insert(req.payload or {})
            elif operation == "upsert":
                query = query.upsert(req.payload or {}, on_conflict=req.on_conflict)
            else:
                query = query.update(req.payload or {})

            for item in req.filters:
                if item.operator.lower() == "eq":
                    query = query.eq(item.column, item.value)
                else:
                    query = query.lte(item.column, item.value)
            if req.limit is not None:
                query = query.limit(req.limit)
            response = query.execute()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "ENGINEERING_AUDIT_PERSISTENCE_FAILED", "error_type": type(exc).__name__},
            ) from exc

        return {
            "data": _rows(response),
            "can_execute": False,
            "probability_authority": "NONE",
            "terminal_authority": TERMINAL_AUTHORITY,
        }
