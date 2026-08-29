"""Shared fake Supabase/PostgREST client for agent_runtime tests.

Not a test module itself (no test_ prefix) — imported by
test_agent_runtime_repository.py and test_agent_runtime_integration.py.
Mimics just enough of supabase-py's fluent .table().select/insert/update()
...eq()...execute() interface for agent_runtime/repository.py's usage, plus
server-side default generation (uuid pk, now() timestamps, column defaults)
so tests don't need a live Supabase project — consistent with this
project's existing _FakeClient/_FakeQuery pattern in test_api_prod.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_PK_COLUMN = {
    "wow_agent_runs": "run_id",
    "wow_agent_run_candidates": "candidate_id",
    "wow_agent_jobs": "job_id",
    "wow_agent_job_outputs": "output_id",
    "wow_agent_audit_events": "audit_event_id",
}

_ROW_DEFAULTS = {
    "wow_agent_runs": {
        "rows_in": 0, "rows_completed": 0, "rows_held": 0, "rows_rejected": 0,
        "reconciliation_status": "NOT_EVALUATED", "can_execute": False,
        "dry_run_only": True, "updated_at": _now,
    },
    "wow_agent_run_candidates": {
        "blockers": lambda: [], "can_execute": False,
    },
    "wow_agent_jobs": {
        "attempt": 0, "blockers": lambda: [], "can_execute": False,
    },
    "wow_agent_job_outputs": {
        "can_execute": False,
    },
    "wow_agent_audit_events": {
        "detail_redacted": lambda: {}, "can_execute": False,
    },
}


def _apply_defaults(table: str, row: dict[str, Any]) -> None:
    for key, default in _ROW_DEFAULTS.get(table, {}).items():
        if key not in row:
            row[key] = default() if callable(default) else default
    row.setdefault("created_at", _now())
    pk = _PK_COLUMN.get(table)
    if pk and pk not in row:
        row[pk] = str(uuid.uuid4())


class FakeResult:
    def __init__(self, data: list[dict[str, Any]]):
        self.data = data


class _FakeQuery:
    def __init__(self, store: dict[str, list[dict]], table: str, mode: str, payload: dict | None = None):
        self.store = store
        self.table_name = table
        self.mode = mode
        self.payload = payload
        self.filters: list[tuple[str, Any]] = []
        self.order_col: str | None = None
        self.limit_n: int | None = None

    def select(self, *_args, **_kwargs) -> "_FakeQuery":
        return self

    def eq(self, column: str, value: Any) -> "_FakeQuery":
        self.filters.append((column, value))
        return self

    def order(self, column: str, **_kwargs) -> "_FakeQuery":
        self.order_col = column
        return self

    def limit(self, n: int) -> "_FakeQuery":
        self.limit_n = n
        return self

    def _matched(self) -> list[dict]:
        rows = self.store.setdefault(self.table_name, [])
        return [row for row in rows if all(row.get(col) == val for col, val in self.filters)]

    def execute(self) -> FakeResult:
        if self.mode == "insert":
            row = dict(self.payload or {})
            _apply_defaults(self.table_name, row)
            self.store.setdefault(self.table_name, []).append(row)
            return FakeResult([dict(row)])

        if self.mode == "update":
            matched = self._matched()
            for row in matched:
                row.update(self.payload or {})
            return FakeResult([dict(row) for row in matched])

        # select
        matched = self._matched()
        if self.order_col:
            matched = sorted(matched, key=lambda row: row.get(self.order_col) or "")
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        return FakeResult([dict(row) for row in matched])


class _FakeTable:
    def __init__(self, store: dict[str, list[dict]], name: str):
        self.store = store
        self.name = name

    def select(self, *_args, **_kwargs) -> _FakeQuery:
        return _FakeQuery(self.store, self.name, "select")

    def insert(self, payload: dict[str, Any]) -> _FakeQuery:
        return _FakeQuery(self.store, self.name, "insert", payload)

    def update(self, payload: dict[str, Any]) -> _FakeQuery:
        return _FakeQuery(self.store, self.name, "update", payload)


class FakeSupabaseClient:
    """In-memory stand-in for the real supabase-py Client. One instance per
    test — a fresh, empty store every time, matching how a real test project
    would start from a clean schema."""

    def __init__(self):
        self._store: dict[str, list[dict]] = {}

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self._store, name)
