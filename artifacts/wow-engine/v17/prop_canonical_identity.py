"""Canonical sporting identity for V17 prop source instances.

Source snapshot/provider identifiers are provenance, not sporting identity.
This helper never changes model math, calibration, terminal semantics, or execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

_SPORT_ALIASES = {"CFB": "NCAAF", "COLLEGE_FOOTBALL": "NCAAF", "NCAA_FOOTBALL": "NCAAF"}

def _text(value: Any) -> str:
    return "_".join(str(value or "").strip().upper().replace("-", " ").split())

def _player(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()

def _line(value: Any) -> str | None:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, TypeError, ValueError):
        return None

def _time(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return raw
    if parsed.utcoffset() is None:
        return raw
    return parsed.astimezone(timezone.utc).isoformat()

def canonical_sporting_key(row: dict[str, Any]) -> tuple[str, ...] | None:
    sport = _SPORT_ALIASES.get(_text(row.get("sport")), _text(row.get("sport")))
    event_id = str(row.get("event_id") or row.get("official_event_id") or "").strip()
    event_start = _time(row.get("event_start_time") or row.get("event_start_time_utc"))
    player = _player(row.get("player") or row.get("participant"))
    stat_type = _text(row.get("stat_type") or row.get("market_stat"))
    line = _line(row.get("line"))
    if not sport or not event_id or not event_start or not player or not stat_type or line is None:
        return None
    direction = _text(row.get("direction"))
    period = _text(row.get("period"))
    settlement = _text(row.get("settlement_identity") or row.get("settlement_basis"))
    return sport, event_id, event_start, player, stat_type, line, direction, period, settlement

def _snapshot_ids(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for value in row.get("source_snapshot_ids") or []:
        text = str(value or "").strip()
        if text and text not in values:
            values.append(text)
    single = str(row.get("source_snapshot_id") or "").strip()
    if single and single not in values:
        values.append(single)
    return values

def _captured_at(row: dict[str, Any]) -> datetime:
    raw = str(row.get("captured_at") or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    return datetime.min.replace(tzinfo=timezone.utc)

def canonicalize_prop_manifest(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, ...], int] = {}
    for raw in rows:
        row = dict(raw)
        key = canonical_sporting_key(row)
        ids = _snapshot_ids(row)
        if key is None:
            row["source_snapshot_ids"] = ids
            row["source_instance_count"] = max(len(ids), 1)
            row["canonical_sporting_key"] = None
            selected.append(row)
            continue
        index = index_by_key.get(key)
        if index is None:
            row["source_snapshot_ids"] = ids
            row["source_instance_count"] = max(len(ids), 1)
            row["canonical_sporting_key"] = "|".join(key)
            index_by_key[key] = len(selected)
            selected.append(row)
            continue
        current = selected[index]
        merged_ids = _snapshot_ids(current)
        for snapshot_id in ids:
            if snapshot_id not in merged_ids:
                merged_ids.append(snapshot_id)
        if _captured_at(row) > _captured_at(current):
            replacement = row
            replacement["canonical_sporting_key"] = current.get("canonical_sporting_key")
            selected[index] = replacement
            current = replacement
        current["source_snapshot_ids"] = merged_ids
        current["source_instance_count"] = max(len(merged_ids), 1)
    return selected

def canonical_source_snapshot_ids(rows: Iterable[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for row in rows:
        ids.update(_snapshot_ids(row))
    return ids

__all__ = ["canonical_sporting_key", "canonicalize_prop_manifest", "canonical_source_snapshot_ids"]
