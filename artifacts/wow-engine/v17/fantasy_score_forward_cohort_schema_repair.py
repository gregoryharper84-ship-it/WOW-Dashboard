"""Compatibility repair for the Fantasy Score forward snapshot selector.

PR #458's collector selected ``team`` and ``opponent`` from
``wow_prop_evidence_snapshots`` even though those columns are not part of the
live governed evidence schema.  The scoring request never requires those two
fields, so the correct repair is to select only canonical snapshot identity and
leave optional prediction-ledger team/opponent values null.

This module replaces only the selector function; all package validation,
persistence, readiness, calibration, and terminal behavior remain owned by
``fantasy_score_forward_cohort_runtime``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import v17.fantasy_score_forward_cohort_runtime as runtime

_INSTALLED = False


def _eligible_snapshots_live_schema(
    db: Any,
    spec: runtime.FantasyScoreLaneSpec,
    limit: int,
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    rows = runtime._db_call(
        f"wow_prop_evidence_snapshots.select_fantasy_{spec.lane.lower()}",
        lambda: db.table("wow_prop_evidence_snapshots")
        .select(
            "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,"
            "stat_type,line,hydration_status,blockers"
        )
        .eq("sport", spec.sport)
        .eq("stat_type", spec.stat_type)
        .eq("hydration_status", "PASS")
        .gt("event_start_time", now.isoformat())
        .order("event_start_time")
        .limit(limit * 3)
        .execute().data or [],
    )
    selected: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        captured = runtime._aware(row.get("captured_at"))
        event_start = runtime._aware(row.get("event_start_time"))
        if row.get("blockers") or not row.get("source_snapshot_id"):
            continue
        if captured is None or event_start is None or captured >= event_start or event_start <= now:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def install_fantasy_score_forward_schema_repair() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    runtime._eligible_snapshots = _eligible_snapshots_live_schema
    _INSTALLED = True
    return True


__all__ = [
    "install_fantasy_score_forward_schema_repair",
]
