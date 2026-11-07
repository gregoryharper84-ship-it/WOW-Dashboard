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

    Games are normally fetched per week so week-addressable rating evidence can
    share the same temporal acquisition contract. When no rating families are
    requested, the caller is consuming settled games only; in that mode CFBD's
    documented season-wide /games query is used once and the raw game rows retain
    their provider week values. This avoids spending twenty provider calls to
    retrieve the same season ledger without changing model inputs or authority.

    Provider failure is explicit and aborts the run rather than returning partial
    model-ready evidence.
    """
    requested_at = _utc_now()
    snapshots: list[SourceSnapshot] = []
    normalized_weeks = sorted({int(w) for w in weeks})
    if not normalized_weeks or any(w < 0 or w > 30 for w in normalized_weeks):
        raise ValueError("weeks must contain valid NCAAF week numbers")

    normalized_rating_families = tuple(str(family).strip().lower() for family in rating_families)

    try:
        if not normalized_rating_families:
            games = client.games(year=season, classification=classification)
            snapshots.append(_snapshot(games, season=season, week=None, requested_at=requested_at))
            return snapshots

        for week in normalized_weeks:
            games = client.games(year=season, week=week, classification=classification)
            snapshots.append(_snapshot(games, season=season, week=week, requested_at=requested_at))

            for normalized in normalized_rating_families:
                # Only Elo is week-addressable in the current narrow client.
                # Other families are acquired once per season below so we do
                # not pretend a full-season retrospective value was known in an
                # earlier week.
                if normalized == "elo":
                    rating = client.ratings(normalized, year=season, week=week)
                    snapshots.append(_snapshot(rating, season=season, week=week, requested_at=requested_at))

        for normalized in normalized_rating_families:
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


def hydrate_cfbd_venues(client: CFBDClient, *, season: int) -> SourceSnapshot:
    """Stage one immutable /venues catalog snapshot.

    ``season`` is only the staging identity bucket. The catalog itself is not a
    game-season statistic, and this function does not turn coordinates into a
    feature row.
    """
    if int(season) < 2000 or int(season) > 2100:
        raise ValueError("year is outside supported research bounds")
    return _snapshot(client.venues(), season=int(season), week=None, requested_at=_utc_now())


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


_SNAPSHOT_TABLE = "wow_ncaaf_source_snapshots"
_SNAPSHOT_IDENTITY = ("provider", "endpoint", "season", "week", "payload_sha256")
_SNAPSHOT_IMMUTABLE_FIELDS = (
    "provider",
    "endpoint",
    "season",
    "week",
    "payload_sha256",
    "response_row_count",
    "acquisition_status",
    "blocker_codes",
    "can_execute",
)


def _snapshot_row(snapshot: SourceSnapshot) -> dict[str, Any]:
    row = asdict(snapshot)
    if row.get("can_execute") is not False:
        raise RuntimeError("NCAAF_SOURCE_SNAPSHOT_EXECUTION_FLAG_INVALID")
    if not str(row.get("payload_sha256") or "").strip():
        raise RuntimeError("NCAAF_SOURCE_SNAPSHOT_HASH_MISSING")
    return row


def _identity_value(row: Mapping[str, Any], field: str) -> Any:
    value = row.get(field)
    if field == "week":
        return None if value is None else int(value)
    if field == "season" or field == "response_row_count":
        return int(value)
    if field == "blocker_codes":
        if value is None:
            return []
        if not isinstance(value, list):
            raise RuntimeError("NCAAF_SOURCE_SNAPSHOT_IMMUTABLE_CONFLICT")
        return [str(item) for item in value]
    if field == "can_execute":
        return value is True
    return value


def _same_snapshot_identity(stored: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    try:
        return all(
            _identity_value(stored, field) == _identity_value(incoming, field)
            for field in _SNAPSHOT_IDENTITY
        )
    except (TypeError, ValueError, RuntimeError):
        return False


def _same_immutable_snapshot(stored: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    try:
        return all(
            _identity_value(stored, field) == _identity_value(incoming, field)
            for field in _SNAPSHOT_IMMUTABLE_FIELDS
        )
    except (TypeError, ValueError, RuntimeError):
        return False


def _existing_snapshots(client: Any, row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    query = (
        client.table(_SNAPSHOT_TABLE)
        .select(
            "provider,endpoint,season,week,payload_sha256,response_row_count,"
            "acquisition_status,blocker_codes,can_execute"
        )
        .eq("provider", row["provider"])
        .eq("endpoint", row["endpoint"])
        .eq("season", int(row["season"]))
        .eq("payload_sha256", row["payload_sha256"])
    )
    if row["week"] is None:
        # Postgres UNIQUE treats NULL week as distinct, so season-wide
        # /games receipts must be matched explicitly rather than upserted.
        query = query.is_("week", "null")
    else:
        query = query.eq("week", int(row["week"]))
    try:
        result = query.execute()
    except Exception as exc:
        raise RuntimeError("NCAAF_SOURCE_SNAPSHOT_READ_FAILED") from exc
    data = getattr(result, "data", None)
    if not isinstance(data, list):
        raise RuntimeError("NCAAF_SOURCE_SNAPSHOT_READ_INVALID")
    return [item for item in data if isinstance(item, Mapping)]


def persist_source_snapshots(supabase_client: Any, snapshots: Iterable[SourceSnapshot]) -> int:
    """Insert immutable raw observations. Never rewrite a stored receipt.

    The target table is RLS-protected and not exposed to anon/authenticated.
    A repeated identical provider/endpoint/season/week/hash is an acknowledgement
    of the existing receipt. A same-hash body whose status, count, or blockers
    disagree is a conflict and is not updated. NULL week is part of the identity
    even though a plain UNIQUE constraint does not treat NULL as equal.

    Writes stay one snapshot per call. A 20-week player-history batch previously
    failed as a single upsert and lost every successful week.
    """
    persisted = 0
    seen: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for snapshot in snapshots:
        row = _snapshot_row(snapshot)
        key = tuple(_identity_value(row, field) for field in _SNAPSHOT_IDENTITY)
        prior = seen.get(key)
        if prior is not None:
            if not _same_immutable_snapshot(prior, row):
                raise RuntimeError("NCAAF_SOURCE_SNAPSHOT_IMMUTABLE_CONFLICT")
            persisted += 1
            continue
        stored = [
            item for item in _existing_snapshots(supabase_client, row)
            if _same_snapshot_identity(item, row)
        ]
        if stored:
            if any(not _same_immutable_snapshot(item, row) for item in stored):
                raise RuntimeError("NCAAF_SOURCE_SNAPSHOT_IMMUTABLE_CONFLICT")
            seen[key] = row
            persisted += 1
            continue
        result = supabase_client.table(_SNAPSHOT_TABLE).insert(row).execute()
        data = getattr(result, "data", None)
        if not isinstance(data, list) or len(data) != 1:
            raise RuntimeError("NCAAF_SOURCE_SNAPSHOT_WRITE_ACK_INVALID")
        seen[key] = row
        persisted += 1
    return persisted
