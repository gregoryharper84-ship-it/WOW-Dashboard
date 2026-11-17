"""Materialize settled NCAAF game identities from staged CFBD /games snapshots.

This intentionally creates only the settled game/result ledger. It does not
invent missing model features, train a model, publish a probability, or enable
execution. An official event id is insert-once: a later snapshot that disagrees
on season, week, kickoff, teams, neutral site, or final score is rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ncaaf_cfbd_hydrator import SourceSnapshot

CAN_EXECUTE = False
_IDENTITY_FIELDS = (
    "official_event_id",
    "season",
    "week",
    "season_type",
    "event_start_time",
    "neutral_site",
    "home_team",
    "away_team",
    "home_points",
    "away_points",
    "home_won",
    "result_source",
)


@dataclass(frozen=True)
class MaterializationResult:
    candidate_rows: int
    persisted_rows: int
    skipped_rows: int
    blocker_codes: tuple[str, ...]
    can_execute: bool = False


def _game_row(raw: Mapping[str, Any], *, snapshot: SourceSnapshot) -> dict[str, Any] | None:
    if raw.get("completed") is not True:
        return None
    home_points = raw.get("homePoints")
    away_points = raw.get("awayPoints")
    if home_points is None or away_points is None or int(home_points) == int(away_points):
        return None
    event_id = raw.get("id")
    start = raw.get("startDate")
    home = raw.get("homeTeam")
    away = raw.get("awayTeam")
    week = raw.get("week")
    season = raw.get("season")
    season_type = raw.get("seasonType")
    if any(value is None for value in (event_id, start, home, away, week, season, season_type)):
        return None
    return {
        "official_event_id": str(event_id),
        "season": int(season),
        "week": int(week),
        "season_type": str(season_type),
        "event_start_time": str(start),
        "venue": raw.get("venue"),
        "neutral_site": bool(raw.get("neutralSite", False)),
        "home_team": str(home),
        "away_team": str(away),
        "home_points": int(home_points),
        "away_points": int(away_points),
        "home_won": int(home_points) > int(away_points),
        "result_source": "CFBD:/games",
        "result_source_timestamp": snapshot.retrieved_at,
        "can_execute": False,
    }


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value or "").replace("Z", "+00:00")
    )
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    start = _aware(row.get("event_start_time")).isoformat()
    values: list[Any] = []
    for field in _IDENTITY_FIELDS:
        if field == "event_start_time":
            values.append(start)
        elif field == "neutral_site" or field == "home_won":
            values.append(bool(row.get(field)))
        elif field in {"season", "week", "home_points", "away_points"}:
            values.append(int(row[field]))
        else:
            values.append(str(row.get(field) or ""))
    return tuple(values)


def _load_existing(client: Any, event_ids: list[str]) -> dict[str, Mapping[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    for start in range(0, len(event_ids), 100):
        chunk = event_ids[start:start + 100]
        try:
            result = (
                client.table("wow_ncaaf_training_games")
                .select(",".join(_IDENTITY_FIELDS))
                .in_("official_event_id", chunk)
                .execute()
            )
        except Exception as exc:
            raise RuntimeError("NCAAF_TRAINING_GAME_IDENTITY_READ_FAILED") from exc
        data = getattr(result, "data", None)
        if not isinstance(data, list):
            raise RuntimeError("NCAAF_TRAINING_GAME_IDENTITY_READ_INVALID")
        for row in data:
            if isinstance(row, Mapping) and str(row.get("official_event_id") or "").strip():
                found[str(row["official_event_id"])] = row
    return found


def materialize_training_games(supabase_client: Any, snapshots: Iterable[SourceSnapshot]) -> MaterializationResult:
    rows: list[dict[str, Any]] = []
    skipped = 0
    blockers: list[str] = []
    for snapshot in snapshots:
        if snapshot.endpoint != "/games" or snapshot.acquisition_status != "AVAILABLE":
            continue
        try:
            retrieved_at = _aware(snapshot.retrieved_at)
        except Exception:
            skipped += len(list(snapshot.response_rows))
            blockers.append("NCAAF_SOURCE_RETRIEVED_AT_INVALID")
            continue
        for raw in snapshot.response_rows:
            row = _game_row(raw, snapshot=snapshot)
            if row is None:
                skipped += 1
                continue
            try:
                kickoff = _aware(row["event_start_time"])
            except Exception:
                skipped += 1
                blockers.append("NCAAF_EVENT_START_INVALID")
                continue
            if retrieved_at <= kickoff:
                skipped += 1
                blockers.append("NCAAF_SETTLEMENT_EVIDENCE_NOT_POST_START")
                continue
            row["event_start_time"] = kickoff.isoformat()
            rows.append(row)

    grouped: dict[str, dict[str, Any]] = {}
    conflicted: set[str] = set()
    for row in rows:
        event_id = str(row["official_event_id"])
        prior = grouped.get(event_id)
        if prior is None:
            grouped[event_id] = row
            continue
        if _identity(prior) != _identity(row):
            conflicted.add(event_id)
    if conflicted:
        blockers.append("NCAAF_TRAINING_GAME_IDENTITY_CONFLICT")
        skipped += sum(1 for row in rows if str(row["official_event_id"]) in conflicted)
        for event_id in conflicted:
            grouped.pop(event_id, None)

    if not rows:
        if "NCAAF_NO_SETTLED_TRAINING_GAMES_MATERIALIZED" not in blockers:
            blockers.append("NCAAF_NO_SETTLED_TRAINING_GAMES_MATERIALIZED")
        return MaterializationResult(
            candidate_rows=0,
            persisted_rows=0,
            skipped_rows=skipped,
            blocker_codes=tuple(dict.fromkeys(blockers)),
        )

    try:
        existing = _load_existing(supabase_client, sorted(grouped))
    except RuntimeError as exc:
        return MaterializationResult(
            candidate_rows=len(rows),
            persisted_rows=0,
            skipped_rows=skipped + len(grouped),
            blocker_codes=tuple(dict.fromkeys([*blockers, str(exc)])),
        )

    to_insert: list[dict[str, Any]] = []
    for event_id, row in grouped.items():
        stored = existing.get(event_id)
        if stored is None:
            to_insert.append(row)
            continue
        try:
            same = _identity(stored) == _identity(row)
        except Exception:
            same = False
        if same:
            skipped += 1
            continue
        skipped += 1
        blockers.append("NCAAF_TRAINING_GAME_IDENTITY_CONFLICT")

    persisted = 0
    if to_insert:
        try:
            result = supabase_client.table("wow_ncaaf_training_games").insert(to_insert).execute()
        except Exception:
            blockers.append("NCAAF_TRAINING_GAME_PERSISTENCE_FAILED")
        else:
            data = getattr(result, "data", None)
            persisted = len(data) if isinstance(data, list) else 0
            if persisted != len(to_insert):
                blockers.append("NCAAF_TRAINING_GAME_PERSISTENCE_COUNT_MISMATCH")
    return MaterializationResult(
        candidate_rows=len(rows),
        persisted_rows=persisted,
        skipped_rows=skipped,
        blocker_codes=tuple(dict.fromkeys(blockers)),
    )
