"""Restricted HTTP persistence client for the resident Engineering Auditor.

The worker uses this adapter to reach the scorer's hidden internal audit bridge
without receiving Supabase service-role credentials. The API surface mirrors
only the query-chain methods consumed by ``EngineeringAuditStore``.
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
class _BridgeResponse:
    data: list[dict[str, Any]]


class EngineeringAuditHttpClient:
    def __init__(self, endpoint: str, token: str, *, timeout_seconds: float = 20.0):
        endpoint = str(endpoint or "").strip()
        token = str(token or "").strip()
        if not endpoint.startswith("https://"):
            raise ValueError("WOW_ENGINEERING_AUDIT_BRIDGE_URL must use https")
        if not token:
            raise ValueError("WOW_ENGINEERING_AUDIT_BRIDGE_TOKEN is required")
        self.endpoint = endpoint
        self._token = token
        self.timeout_seconds = float(timeout_seconds)

    @classmethod
    def from_env(cls) -> "EngineeringAuditHttpClient":
        return cls(
            os.environ["WOW_ENGINEERING_AUDIT_BRIDGE_URL"],
            os.environ["WOW_ENGINEERING_AUDIT_BRIDGE_TOKEN"],
        )

    def table(self, table: str) -> "_TableRequest":
        table = str(table or "").strip()
        if table not in _ALLOWED_TABLES:
            raise ValueError(f"ENGINEERING_AUDIT_BRIDGE_TABLE_FORBIDDEN:{table}")
        return _TableRequest(self, table)

    def _execute(self, request: dict[str, Any]) -> _BridgeResponse:
        response = requests.post(
            self.endpoint,
            headers={
                "content-type": "application/json",
                "X-WOW-Engineering-Token": self._token,
            },
            json=request,
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            code = "UNKNOWN"
            try:
                payload = response.json()
                detail = payload.get("detail") if isinstance(payload, dict) else None
                if isinstance(detail, dict):
                    code = str(detail.get("code") or code)
            except Exception:
                pass
            raise RuntimeError(
                f"ENGINEERING_AUDIT_BRIDGE_FAILED:{response.status_code}:{code}"
            )
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if rows is None:
            rows = []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise RuntimeError("ENGINEERING_AUDIT_BRIDGE_MALFORMED_RESPONSE")
        return _BridgeResponse(data=[row for row in rows if isinstance(row, dict)])


class _TableRequest:
    def __init__(self, client: EngineeringAuditHttpClient, table: str):
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
            raise ValueError("ENGINEERING_AUDIT_BRIDGE_LIMIT_OUT_OF_RANGE")
        self.row_limit = limit
        return self

    def execute(self) -> _BridgeResponse:
        operation = str(self.operation or "").strip().lower()
        if operation not in _ALLOWED_OPS:
            raise ValueError(f"ENGINEERING_AUDIT_BRIDGE_OPERATION_FORBIDDEN:{operation}")
        for item in self.filters:
            if item["operator"] not in _ALLOWED_FILTERS:
                raise ValueError("ENGINEERING_AUDIT_BRIDGE_FILTER_FORBIDDEN")
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
