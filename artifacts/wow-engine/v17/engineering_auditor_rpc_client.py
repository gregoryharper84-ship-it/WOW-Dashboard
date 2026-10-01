"""Purpose-scoped client for the Supabase Engineering Auditor Edge bridge.

The bridge exists so the resident Render worker does not need the Supabase
service-role key. It exposes only the tiny query surface used by
``EngineeringAuditStore`` and carries no sporting probability authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import requests


_ALLOWED_TABLES = frozenset(
    {
        "wow_agent_audit_events",
        "wow_engineering_audit_work_items",
        "wow_engineering_audit_findings",
        "wow_engineering_backlog",
        "wow_engineering_auditor_runtime",
    }
)
_ALLOWED_OPS = frozenset({"select", "insert", "upsert", "update"})
_ALLOWED_FILTERS = frozenset({"eq", "lte"})


@dataclass
class _RpcResponse:
    data: list[dict[str, Any]]


class EngineeringAuditRpcClient:
    """Small Supabase-like adapter backed by a locked-down Edge Function."""

    def __init__(self, endpoint: str, token: str, *, timeout_seconds: float = 20.0):
        endpoint = str(endpoint or "").strip().rstrip("/")
        token = str(token or "").strip()
        if not endpoint:
            raise ValueError("WOW_ENGINEERING_AUDIT_BRIDGE_URL is required")
        if not token:
            raise ValueError("WOW_ENGINEERING_AUDIT_BRIDGE_TOKEN is required")
        self.endpoint = endpoint
        self._token = token
        self.timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_env(cls) -> "EngineeringAuditRpcClient":
        return cls(
            os.environ["WOW_ENGINEERING_AUDIT_BRIDGE_URL"],
            os.environ["WOW_ENGINEERING_AUDIT_BRIDGE_TOKEN"],
        )

    def table(self, table: str) -> "_TableRequest":
        table = str(table or "").strip()
        if table not in _ALLOWED_TABLES:
            raise ValueError(f"ENGINEERING_AUDIT_RPC_TABLE_FORBIDDEN:{table}")
        return _TableRequest(self, table)

    def _execute(self, request: dict[str, Any]) -> _RpcResponse:
        response = requests.post(
            self.endpoint,
            headers={
                "content-type": "application/json",
                "x-wow-engineering-token": self._token,
            },
            json=request,
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            error_type = "UNKNOWN"
            try:
                payload = response.json()
                error_type = str(payload.get("error") or error_type)
            except Exception:
                pass
            raise RuntimeError(
                f"ENGINEERING_AUDIT_RPC_FAILED:{response.status_code}:{error_type}"
            )
        payload = response.json()
        rows = payload.get("data")
        if rows is None:
            rows = []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise RuntimeError("ENGINEERING_AUDIT_RPC_MALFORMED_RESPONSE")
        return _RpcResponse(data=[row for row in rows if isinstance(row, dict)])


class _TableRequest:
    def __init__(self, client: EngineeringAuditRpcClient, table: str):
        self.client = client
        self.table_name = table
        self.operation: str | None = None
        self.columns = "*"
        self.payload: dict[str, Any] | None = None
        self.on_conflict: str | None = None
        self.filters: list[dict[str, Any]] = []
        self.row_limit: int | None = None

    def select(self, columns: str = "*") -> "_TableRequest":
        self.operation = "select"
        self.columns = str(columns or "*")
        return self

    def insert(self, payload: dict[str, Any]) -> "_TableRequest":
        self.operation = "insert"
        self.payload = dict(payload)
        return self

    def upsert(self, payload: dict[str, Any], *, on_conflict: str | None = None) -> "_TableRequest":
        self.operation = "upsert"
        self.payload = dict(payload)
        self.on_conflict = str(on_conflict or "").strip() or None
        return self

    def update(self, payload: dict[str, Any]) -> "_TableRequest":
        self.operation = "update"
        self.payload = dict(payload)
        return self

    def eq(self, column: str, value: Any) -> "_TableRequest":
        self.filters.append({"operator": "eq", "column": str(column), "value": value})
        return self

    def lte(self, column: str, value: Any) -> "_TableRequest":
        self.filters.append({"operator": "lte", "column": str(column), "value": value})
        return self

    def limit(self, value: int) -> "_TableRequest":
        limit = int(value)
        if limit < 1 or limit > 1000:
            raise ValueError("ENGINEERING_AUDIT_RPC_LIMIT_OUT_OF_RANGE")
        self.row_limit = limit
        return self

    def execute(self) -> _RpcResponse:
        operation = str(self.operation or "").strip().lower()
        if operation not in _ALLOWED_OPS:
            raise ValueError(f"ENGINEERING_AUDIT_RPC_OPERATION_FORBIDDEN:{operation}")
        for item in self.filters:
            if item["operator"] not in _ALLOWED_FILTERS:
                raise ValueError("ENGINEERING_AUDIT_RPC_FILTER_FORBIDDEN")
        return self.client._execute(
            {
                "table": self.table_name,
                "operation": operation,
                "columns": self.columns,
                "payload": self.payload,
                "on_conflict": self.on_conflict,
                "filters": self.filters,
                "limit": self.row_limit,
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
            }
        )
