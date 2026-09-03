"""Statistical independence guard for the V17 prop forward cohort.

A calibration observation is one exact pregame thesis, not one refreshed evidence
snapshot and not one MORE/LESS direction.  This module installs narrow runtime
overrides so immutable snapshot rows remain auditable while readiness counters and
autonomous capture use the independent thesis key:
(event_id, player, stat_type, line).

WOW-PATCH-2026-09-02-V17-PROP-FORWARD-THESIS-DEDUPE
can_execute=false; no scoring/publication semantics are changed.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import v17.prop_forward_cohort_runtime as runtime


def _normalized_line(value: Any) -> str | None:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, TypeError, ValueError):
        return None


def thesis_key(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    event_id = str(row.get("event_id") or "").strip()
    player = " ".join(str(row.get("player") or "").split()).casefold()
    stat_type = str(row.get("stat_type") or "").strip().upper()
    line = _normalized_line(row.get("line"))
    if not event_id or not player or not stat_type or line is None:
        return None
    return event_id, player, stat_type, line


def _eligible_snapshots(db: Any, limit: int, *, now: Any) -> list[dict[str, Any]]:
    rows = (
        db.table("wow_prop_evidence_snapshots")
        .select(
            "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,"
            "stat_type,line,hydration_status,blockers"
        )
        .eq("sport", runtime.SPORT)
        .eq("stat_type", runtime.STAT_TYPE)
        .eq("hydration_status", "PASS")
        .order("event_start_time")
        .order("captured_at")
        .limit(limit * 10)
        .execute().data or []
    )
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in rows:
        row = dict(raw)
        captured = runtime._aware(row.get("captured_at"))
        event_start = runtime._aware(row.get("event_start_time"))
        key = thesis_key(row)
        if row.get("blockers") or not row.get("source_snapshot_id") or key is None:
            continue
        if captured is None or event_start is None or captured >= event_start or event_start <= now:
            continue
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _forward_predictions(db: Any) -> list[dict[str, Any]]:
    rows = (
        db.table("wow_predictions")
        .select(
            "prediction_id,event_id,event_start_time,model_timestamp,locked_at,"
            "source_snapshot_id,player,stat_type,line,direction"
        )
        .eq("sport", runtime.SPORT)
        .eq("stat_type", runtime.STAT_TYPE)
        .eq("model_provider_identity", runtime.PROVIDER)
        .execute().data or []
    )
    eligible: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        start = runtime._aware(row.get("event_start_time"))
        model_ts = runtime._aware(row.get("model_timestamp"))
        locked = runtime._aware(row.get("locked_at"))
        if thesis_key(row) is None or start is None or model_ts is None or locked is None:
            continue
        if model_ts < start and locked < start and str(row.get("direction") or "").upper() in runtime.DIRECTIONS:
            eligible.append(row)
    return eligible


def _cohort_counts(predictions: list[dict[str, Any]], settled_ids: set[str]) -> tuple[int, int]:
    prediction_theses = {
        key for row in predictions if (key := thesis_key(row)) is not None
    }
    settled_theses = {
        key
        for row in predictions
        if str(row.get("prediction_id")) in settled_ids
        if (key := thesis_key(row)) is not None
    }
    return len(prediction_theses), len(settled_theses)


def _reconcile_capability(db: Any) -> dict[str, Any]:
    predictions = _forward_predictions(db)
    ids = [str(row["prediction_id"]) for row in predictions]
    settled = runtime._settled_prediction_ids(db, ids)
    prediction_n, settled_n = _cohort_counts(predictions, settled)
    evidence, exists = runtime._capability_evidence(db)
    readiness = runtime._readiness(evidence, prediction_n, settled_n)
    if exists:
        updated = dict(evidence)
        updated["forward_prediction_n"] = prediction_n
        updated["forward_settled_n"] = settled_n
        updated["forward_cohort_counting_basis"] = "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS"
        updated["forward_cohort_readiness"] = readiness
        db.table("wow_runtime_capabilities").update({"evidence": updated}).eq(
            "capability_key", runtime.CAPABILITY_KEY
        ).execute()
    return readiness


def install() -> None:
    """Install independence-preserving overrides into the existing runtime."""
    runtime._eligible_snapshots = _eligible_snapshots
    runtime._forward_predictions = _forward_predictions
    runtime._cohort_counts = _cohort_counts
    runtime._reconcile_capability = _reconcile_capability


install()
