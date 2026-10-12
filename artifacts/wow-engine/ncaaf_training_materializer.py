"""Materialize settled NCAAF game identities from staged CFBD /games snapshots.

This intentionally creates only the settled game/result ledger. It does not
invent missing model features, train a model, publish a probability, or enable
execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ncaaf_cfbd_hydrator import SourceSnapshot

CAN_EXECUTE = False


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
    # CFBD source-neutral is an actual fitted-model feature. Missing values
    # and strings such as "false" must not silently become a home-field
    # assumption in the immutable settled research corpus.
    neutral_site = raw.get("neutralSite")
    if type(neutral_site) is not bool:
        return None
    return {
        "official_event_id": str(event_id),
        "season": int(season),
        "week": int(week),
        "season_type": str(season_type),
        "event_start_time": str(start),
        "venue": raw.get("venue"),
        "neutral_site": neutral_site,
        "home_team": str(home),
        "away_team": str(away),
        "home_points": int(home_points),
        "away_points": int(away_points),
        "home_won": int(home_points) > int(away_points),
        "result_source": "CFBD:/games",
        "result_source_timestamp": snapshot.retrieved_at,
        "can_execute": False,
    }


def materialize_training_games(supabase_client: Any, snapshots: Iterable[SourceSnapshot]) -> MaterializationResult:
    rows: list[dict[str, Any]] = []
    skipped = 0
    missing_neutral_site_n = 0
    for snapshot in snapshots:
        if snapshot.endpoint != "/games" or snapshot.acquisition_status != "AVAILABLE":
            continue
        for raw in snapshot.response_rows:
            row = _game_row(raw, snapshot=snapshot)
            if row is None:
                skipped += 1
                if raw.get("completed") is True and type(raw.get("neutralSite")) is not bool:
                    missing_neutral_site_n += 1
            else:
                rows.append(row)

    neutral_blockers = (
        ["NCAAF_TRAINING_NEUTRAL_SITE_EVIDENCE_MISSING"]
        if missing_neutral_site_n else []
    )
    if not rows:
        return MaterializationResult(
            candidate_rows=0,
            persisted_rows=0,
            skipped_rows=skipped,
            blocker_codes=tuple(neutral_blockers + ["NCAAF_NO_SETTLED_TRAINING_GAMES_MATERIALIZED"]),
        )

    result = supabase_client.table("wow_ncaaf_training_games").upsert(
        rows,
        on_conflict="official_event_id",
    ).execute()
    data = getattr(result, "data", None)
    persisted = len(data) if isinstance(data, list) else 0
    blockers: list[str] = list(neutral_blockers)
    if persisted < len(rows):
        blockers.append("NCAAF_TRAINING_GAME_PERSISTENCE_COUNT_MISMATCH")
    return MaterializationResult(
        candidate_rows=len(rows),
        persisted_rows=persisted,
        skipped_rows=skipped,
        blocker_codes=tuple(blockers),
    )
