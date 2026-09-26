"""Fail-closed historical acquisition for NCAAF fitted-model research.

The hydrator stages raw read-only provider responses with deterministic hashes.
It does not transform raw provider rows into model-ready features, certify a
model, publish a probability, or enable execution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from ncaaf_cfbd_client import CFBDClient, CFBDResponse, CFBDUnavailable

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


@dataclass(frozen=True)
class SourceSnapshot:
    provider: str
    endpoint: str
    season: int
    week: Optional[int]
    requested_at: str
    retrieved_at: str
    request_params: Mapping[str, Any]
    response_rows: list[Mapping[str, Any]]
    response_row_count: int
    payload_sha256: str
    acquisition_status: str
    blocker_codes: list[str]
    can_execute: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    payload = json.dumps(list(rows), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot(response: CFBDResponse, *, season: int, week: Optional[int], requested_at: datetime) -> SourceSnapshot:
    rows = list(response.rows)
    status = "AVAILABLE" if rows else "EMPTY"
    blockers = [] if rows else ["NCAAF_SOURCE_EMPTY"]
    return SourceSnapshot(
        provider="CFBD",
        endpoint=response.endpoint,
        season=int(season),
        week=week,
        requested_at=requested_at.astimezone(timezone.utc).isoformat(),
        retrieved_at=_utc_now().isoformat(),
        request_params=dict(response.params),
        response_rows=rows,
        response_row_count=len(rows),
        payload_sha256=_canonical_hash(rows),
        acquisition_status=status,
        blocker_codes=blockers,
    )


def hydrate_cfbd_season(
    client: CFBDClient,
    *,
    season: int,
    weeks: Iterable[int],
    rating_families: Iterable[str] = ("elo",),
    classification: Optional[str] = "fbs",
) -> list[SourceSnapshot]:
    """Fetch one season into auditable raw source snapshots.

    Games are fetched per week. Rating families are fetched only through the
    client's allowlist. Provider failure is explicit and aborts the run rather
    than returning partial model-ready evidence.
    """
    requested_at = _utc_now()
    snapshots: list[SourceSnapshot] = []
    normalized_weeks = sorted({int(w) for w in weeks})
    if not normalized_weeks or any(w < 0 or w > 30 for w in normalized_weeks):
        raise ValueError("weeks must contain valid NCAAF week numbers")

    try:
        for week in normalized_weeks:
            games = client.games(year=season, week=week, classification=classification)
            snapshots.append(_snapshot(games, season=season, week=week, requested_at=requested_at))

            for family in rating_families:
                normalized = str(family).strip().lower()
                # Only Elo is week-addressable in the current narrow client.
                # Other families are acquired once per season below so we do
                # not pretend a full-season retrospective value was known in an
                # earlier week.
                if normalized == "elo":
                    rating = client.ratings(normalized, year=season, week=week)
                    snapshots.append(_snapshot(rating, season=season, week=week, requested_at=requested_at))

        for family in rating_families:
            normalized = str(family).strip().lower()
            if normalized != "elo":
                rating = client.ratings(normalized, year=season)
                snap = _snapshot(rating, season=season, week=None, requested_at=requested_at)
                snapshots.append(
                    SourceSnapshot(
                        **{**asdict(snap), "blocker_codes": [*snap.blocker_codes, "RETROSPECTIVE_RATING_NOT_PREGAME_FEATURE"]}
                    )
                )
    except CFBDUnavailable:
        raise

    return snapshots


def hydrate_cfbd_player_stats_season(
    client: CFBDClient,
    *,
    season: int,
    weeks: Iterable[int],
    classification: Optional[str] = "fbs",
    season_type: Optional[str] = "both",
) -> list[SourceSnapshot]:
    """Fetch immutable raw player box-score snapshots for one NCAAF season.

    This is the first governed input for the NCAAF player-prop build. The rows
    remain raw sporting outcomes; they are not features, fitted-model inputs,
    calibrators, probabilities, or publication authority.
    """
    requested_at = _utc_now()
    normalized_weeks = sorted({int(w) for w in weeks})
    if not normalized_weeks or any(w < 0 or w > 30 for w in normalized_weeks):
        raise ValueError("weeks must contain valid NCAAF week numbers")

    snapshots: list[SourceSnapshot] = []
    for week in normalized_weeks:
        response = client.player_game_stats(
            year=int(season),
            week=week,
            classification=classification,
            season_type=season_type,
        )
        snapshots.append(_snapshot(response, season=int(season), week=week, requested_at=requested_at))
    return snapshots


def profile_cfbd_player_stats(snapshots: Iterable[SourceSnapshot]) -> dict[str, Any]:
    """Validate/profile CFBD's nested player-box-score shape without fitting it.

    The profile intentionally emits only schema names and aggregate counts. It
    does not expose player-level data and does not map provider stat labels onto
    canonical WOW prop routes until the source shape has been observed and
    reviewed.
    """
    category_types: dict[str, set[str]] = {}
    source_snapshot_n = game_n = team_n = athlete_stat_n = participant_id_n = 0
    malformed_n = 0
    participant_ids: set[str] = set()

    for snapshot in snapshots:
        if snapshot.endpoint != "/games/players":
            malformed_n += 1
            continue
        source_snapshot_n += 1
        for game in snapshot.response_rows:
            if not isinstance(game, Mapping) or str(game.get("id") or "").strip() == "":
                malformed_n += 1
                continue
            teams = game.get("teams")
            if not isinstance(teams, list):
                malformed_n += 1
                continue
            game_n += 1
            for team in teams:
                if not isinstance(team, Mapping) or not str(team.get("team") or "").strip():
                    malformed_n += 1
                    continue
                categories = team.get("categories")
                if not isinstance(categories, list):
                    malformed_n += 1
                    continue
                team_n += 1
                for category in categories:
                    if not isinstance(category, Mapping):
                        malformed_n += 1
                        continue
                    category_name = str(category.get("name") or "").strip()
                    types = category.get("types")
                    if not category_name or not isinstance(types, list):
                        malformed_n += 1
                        continue
                    bucket = category_types.setdefault(category_name, set())
                    for stat_type in types:
                        if not isinstance(stat_type, Mapping):
                            malformed_n += 1
                            continue
                        type_name = str(stat_type.get("name") or "").strip()
                        athletes = stat_type.get("athletes")
                        if not type_name or not isinstance(athletes, list):
                            malformed_n += 1
                            continue
                        bucket.add(type_name)
                        for athlete in athletes:
                            if not isinstance(athlete, Mapping):
                                malformed_n += 1
                                continue
                            athlete_id = str(athlete.get("id") or "").strip()
                            stat = athlete.get("stat")
                            if not athlete_id or stat is None or str(stat).strip() == "":
                                malformed_n += 1
                                continue
                            athlete_stat_n += 1
                            participant_ids.add(athlete_id)

    participant_id_n = len(participant_ids)
    if malformed_n:
        status = "BLOCKED"
        blocker = "NCAAF_PROP_PLAYER_STATS_SCHEMA_INVALID"
    elif athlete_stat_n <= 0:
        status = "BLOCKED"
        blocker = "NCAAF_PROP_PLAYER_STATS_EMPTY"
    else:
        status = "READY_FOR_NORMALIZATION"
        blocker = None

    return {
        "status": status,
        "blocker": blocker,
        "source_snapshot_n": source_snapshot_n,
        "game_n": game_n,
        "team_n": team_n,
        "participant_id_n": participant_id_n,
        "athlete_stat_n": athlete_stat_n,
        "malformed_n": malformed_n,
        "category_types": {name: sorted(values) for name, values in sorted(category_types.items())},
        "source_provider": "CFBD",
        "source_endpoint": "/games/players",
        "evidence_domain": "SPORTING",
        "probability_publishable": False,
        "can_execute": False,
    }


def persist_source_snapshots(supabase_client: Any, snapshots: Iterable[SourceSnapshot]) -> int:
    """Persist staged raw observations using a service-role Supabase client.

    The target table is RLS-protected and not exposed to anon/authenticated.
    This function returns inserted/upserted row count only; it has no scoring
    side effects.
    """
    rows = [asdict(snapshot) for snapshot in snapshots]
    if not rows:
        return 0
    result = supabase_client.table("wow_ncaaf_source_snapshots").upsert(
        rows,
        on_conflict="provider,endpoint,season,week,payload_sha256",
    ).execute()
    data = getattr(result, "data", None)
    return len(data) if isinstance(data, list) else 0
