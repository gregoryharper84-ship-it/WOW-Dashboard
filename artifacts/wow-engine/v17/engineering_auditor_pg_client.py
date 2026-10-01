"""Restricted psycopg adapter for WOW Engineering Auditor persistence.

The adapter intentionally implements only the Supabase-style query methods
used by ``EngineeringAuditStore``. It is not a general SQL escape hatch.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


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
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class _PgResponse:
    data: list[dict[str, Any]]


def _identifier(value: str) -> sql.Identifier:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(f"ENGINEERING_AUDIT_DB_IDENTIFIER_FORBIDDEN:{text}")
    return sql.Identifier(text)


def _adapt(value: Any) -> Any:
    if isinstance(value, dict):
        return Jsonb(value)
    return value


class EngineeringAuditPgClient:
    """Least-privilege database client with a fixed table/operation surface."""

    def __init__(self, dsn: str):
        dsn = str(dsn or "").strip()
        if not dsn:
            raise ValueError("WOW_ENGINEERING_AUDIT_DB_URI is required")
        self._dsn = dsn

    @classmethod
    def from_env(cls) -> "EngineeringAuditPgClient":
        return cls(os.environ["WOW_ENGINEERING_AUDIT_DB_URI"])

    def table(self, table: str) -> "_TableRequest":
        table = str(table or "").strip()
        if table not in _ALLOWED_TABLES:
            raise ValueError(f"ENGINEERING_AUDIT_DB_TABLE_FORBIDDEN:{table}")
        return _TableRequest(self, table)

    def _execute(self, statement: sql.Composed, params: list[Any]) -> _PgResponse:
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10) as conn:
            with conn.cursor() as cursor:
                cursor.execute(statement, params)
                rows = cursor.fetchall() if cursor.description else []
            conn.commit()
        return _PgResponse(data=[dict(row) for row in rows])


class _TableRequest:
    def __init__(self, client: EngineeringAuditPgClient, table: str):
        self.client = client
        self.table_name = table
        self.operation: str | None = None
        self.columns = "*"
        self.payload: dict[str, Any] | None = None
        self.on_conflict: str | None = None
        self.filters: list[tuple[str, str, Any]] = []
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
        self.filters.append(("eq", str(column), value))
        return self

    def lte(self, column: str, value: Any) -> "_TableRequest":
        self.filters.append(("lte", str(column), value))
        return self

    def limit(self, value: int) -> "_TableRequest":
        limit = int(value)
        if limit < 1 or limit > 1000:
            raise ValueError("ENGINEERING_AUDIT_DB_LIMIT_OUT_OF_RANGE")
        self.row_limit = limit
        return self

    def _select_columns(self) -> sql.SQL | sql.Composed:
        if self.columns == "*":
            return sql.SQL("*")
        names = [item.strip() for item in self.columns.split(",") if item.strip()]
        if not names:
            raise ValueError("ENGINEERING_AUDIT_DB_SELECT_COLUMNS_REQUIRED")
        return sql.SQL(", ").join(_identifier(name) for name in names)

    def _where(self) -> tuple[sql.SQL, list[Any]]:
        if not self.filters:
            return sql.SQL(""), []
        clauses: list[sql.Composed] = []
        params: list[Any] = []
        for operator, column, value in self.filters:
            if operator not in _ALLOWED_FILTERS:
                raise ValueError(f"ENGINEERING_AUDIT_DB_FILTER_FORBIDDEN:{operator}")
            identifier = _identifier(column)
            if operator == "eq":
                if value is None:
                    clauses.append(sql.SQL("{} IS NULL").format(identifier))
                else:
                    clauses.append(sql.SQL("{} = %s").format(identifier))
                    params.append(_adapt(value))
            else:
                clauses.append(sql.SQL("{} <= %s").format(identifier))
                params.append(_adapt(value))
        return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses), params

    def execute(self) -> _PgResponse:
        operation = str(self.operation or "").strip().lower()
        if operation not in _ALLOWED_OPS:
            raise ValueError(f"ENGINEERING_AUDIT_DB_OPERATION_FORBIDDEN:{operation}")

        table = sql.Identifier("public", self.table_name)
        where_sql, where_params = self._where()

        if operation == "select":
            statement = sql.SQL("SELECT {} FROM {}").format(self._select_columns(), table) + where_sql
            params = where_params
            if self.row_limit is not None:
                statement += sql.SQL(" LIMIT %s")
                params.append(self.row_limit)
            return self.client._execute(statement, params)

        payload = self.payload or {}
        if not payload:
            raise ValueError("ENGINEERING_AUDIT_DB_PAYLOAD_REQUIRED")
        keys = list(payload)
        for key in keys:
            _identifier(key)
        values = [_adapt(payload[key]) for key in keys]

        if operation in {"insert", "upsert"}:
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                table,
                sql.SQL(", ").join(_identifier(key) for key in keys),
                sql.SQL(", ").join(sql.Placeholder() for _ in keys),
            )
            if operation == "upsert":
                conflicts = [item.strip() for item in str(self.on_conflict or "").split(",") if item.strip()]
                if not conflicts:
                    raise ValueError("ENGINEERING_AUDIT_DB_ON_CONFLICT_REQUIRED")
                conflict_sql = sql.SQL(", ").join(_identifier(item) for item in conflicts)
                update_keys = [key for key in keys if key not in conflicts]
                if update_keys:
                    assignments = sql.SQL(", ").join(
                        sql.SQL("{} = EXCLUDED.{}").format(_identifier(key), _identifier(key))
                        for key in update_keys
                    )
                    statement += sql.SQL(" ON CONFLICT ({}) DO UPDATE SET {}").format(conflict_sql, assignments)
                else:
                    statement += sql.SQL(" ON CONFLICT ({}) DO NOTHING").format(conflict_sql)
            statement += sql.SQL(" RETURNING *")
            return self.client._execute(statement, values)

        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(_identifier(key)) for key in keys
        )
        statement = sql.SQL("UPDATE {} SET {}").format(table, assignments) + where_sql + sql.SQL(" RETURNING *")
        return self.client._execute(statement, values + where_params)
