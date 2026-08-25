"""
gate_engine/universal_agent/model_validation/feature_store.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Point-in-Time Feature Store.

Stores immutable feature snapshots keyed by (snapshot_id, as_of_date).
Once a snapshot is committed (locked), its feature values cannot be modified.
This prevents look-ahead bias in walk-forward backtesting.

Design
------
- In-memory store backed by a plain dict. Persistence is the caller's
  responsibility (serialise to/from JSON if needed).
- FeatureSnapshot is a frozen dataclass — immutable once created.
- commit_snapshot() rejects duplicate snapshot_ids (fail-closed).
- No network I/O. No database calls. No app.py import.

can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


@dataclass(frozen=True)
class FeatureSnapshot:
    """
    Immutable point-in-time feature record.

    snapshot_id  Unique identifier for this snapshot.
    as_of_date   YYYY-MM-DD string representing the data cut-off date.
                 No features from after this date may be included.
    features     Dict of feature_name -> value. Immutable once locked.
    locked_at    ISO-8601 timestamp when this snapshot was committed.
    model_family Optional label grouping snapshots by model family.
    """
    snapshot_id:  str
    as_of_date:   str
    features:     dict     # feature_name -> value (treated as immutable by contract)
    locked_at:    str      # ISO-8601
    model_family: str | None = None


class PointInTimeFeatureStore:
    """
    Advisory-only in-memory feature store.
    can_execute = False — no production mutations.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, FeatureSnapshot] = {}

    def commit_snapshot(
        self,
        *,
        snapshot_id:  str,
        as_of_date:   str,
        features:     dict[str, Any],
        model_family: str | None = None,
        locked_at:    str | None = None,
    ) -> FeatureSnapshot:
        """
        Commit a feature snapshot. Raises ValueError on duplicate snapshot_id.

        Parameters
        ----------
        snapshot_id   Unique run/snapshot identifier.
        as_of_date    YYYY-MM-DD data cut-off (enforced by caller).
        features      Flat dict of feature values at as_of_date.
        model_family  Optional family label.
        locked_at     Override locked_at timestamp (default: now UTC).
        """
        if snapshot_id in self._snapshots:
            raise ValueError(
                f"PointInTimeFeatureStore: duplicate snapshot_id {snapshot_id!r}. "
                "Snapshots are immutable once committed."
            )
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be a non-empty string")
        if not isinstance(as_of_date, str) or len(as_of_date) != 10:
            raise ValueError("as_of_date must be a YYYY-MM-DD string")

        ts = locked_at or datetime.now(timezone.utc).isoformat()
        snap = FeatureSnapshot(
            snapshot_id=snapshot_id,
            as_of_date=as_of_date,
            features=dict(features),   # shallow copy to avoid external mutation
            locked_at=ts,
            model_family=model_family,
        )
        self._snapshots[snapshot_id] = snap
        return snap

    def get_snapshot(self, snapshot_id: str) -> FeatureSnapshot | None:
        """Retrieve a snapshot by id. Returns None if not found."""
        return self._snapshots.get(snapshot_id)

    def list_snapshots_before(self, as_of_date: str) -> list[FeatureSnapshot]:
        """
        Return all snapshots whose as_of_date <= the given date,
        sorted chronologically.
        """
        result = [
            s for s in self._snapshots.values()
            if s.as_of_date <= as_of_date
        ]
        return sorted(result, key=lambda s: s.as_of_date)

    def count(self) -> int:
        return len(self._snapshots)

    def snapshot_ids(self) -> list[str]:
        return list(self._snapshots.keys())
