"""Read-only, injectable snapshot sink for governed prop producers.

The MLB, NFL and WNBA forward-evidence producers persist immutable rows into
``wow_prop_evidence_snapshots`` through a Supabase-shaped client. A read-only
canary must reuse the same acquisition and provenance logic without writing.

This sink is injected through each producer's existing ``db`` parameter. It
answers existence probes with "no row" (so a prior capture cannot short-circuit
acquisition) and records every upsert it suppresses. It opens no connection and
changes no producer call site, gate or receipt semantic: ``can_execute`` remains
false and the sink reports ``NO_WRITE`` so a persisted bundle can prove it was
acquired without governed writes.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

NO_WRITE_MODE = "NO_WRITE"


class _EmptyResult:
    """Read envelope matching the shape producers consume."""

    __slots__ = ("data",)

    def __init__(self) -> None:
        self.data: list[dict[str, Any]] = []


class _Query:
    """One duck-typed table handle accepting the producer chains."""

    def __init__(self, store: "NoWriteSnapshotStore", table: str) -> None:
        self._store = store
        self._table = table
        self._row: dict[str, Any] | None = None
        self._on_conflict: str | None = None

    def select(self, *_args: Any, **_kwargs: Any) -> "_Query":
        return self

    def eq(self, *_args: Any, **_kwargs: Any) -> "_Query":
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "_Query":
        return self

    def limit(self, *_args: Any, **_kwargs: Any) -> "_Query":
        return self

    def upsert(self, row: dict[str, Any], on_conflict: str | None = None, **_kwargs: Any) -> "_Query":
        self._row = row
        self._on_conflict = on_conflict
        return self

    def execute(self) -> _EmptyResult:
        if self._row is not None:
            self._store.writes.append(
                {"table": self._table, "on_conflict": self._on_conflict, "row": deepcopy(self._row)}
            )
        return _EmptyResult()


class NoWriteSnapshotStore:
    """Supabase-shaped store that records intended upserts and writes nothing."""

    mode = NO_WRITE_MODE

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    @property
    def write_count(self) -> int:
        return len(self.writes)


__all__ = ["NO_WRITE_MODE", "NoWriteSnapshotStore"]
