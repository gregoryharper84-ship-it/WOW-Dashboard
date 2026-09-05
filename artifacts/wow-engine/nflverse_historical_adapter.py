"""Governed nflverse historical adapter for future NFL prop specialists.

Only dataset names explicitly allowed by ``nflverse_dataset_policy_v1.json`` may
enter this adapter. The initial path intentionally excludes FTN participation and
charting data. This module normalizes settled sporting outcomes only; it does not
fit models, calculate probabilities, or enable execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from historical_data_backbone import (
    CanonicalIdentity,
    HistoricalDataContractError,
    NormalizedPlayerGameOutcome,
)


NFL_EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

STAT_FIELD_MAP: dict[str, str] = {
    "PASS_ATTEMPTS": "attempts",
    "PASS_COMPLETIONS": "completions",
    "PASSING_YARDS": "passing_yards",
    "PASSING_TDS": "passing_tds",
    "PASSING_INTERCEPTIONS": "passing_interceptions",
    "RUSH_ATTEMPTS": "carries",
    "RUSHING_YARDS": "rushing_yards",
    "RUSHING_TDS": "rushing_tds",
    "TARGETS": "targets",
    "RECEPTIONS": "receptions",
    "RECEIVING_YARDS": "receiving_yards",
    "RECEIVING_TDS": "receiving_tds",
}


class NFLVerseHistoricalAdapterError(HistoricalDataContractError):
    pass


@dataclass(frozen=True)
class NFLVerseDatasetPolicyEntry:
    dataset: str
    status: str
    evidence_domain: str
    license: str
    production_training_allowed: bool
    purpose: str
    can_execute: bool = field(default=False, init=False)


@dataclass(frozen=True)
class NFLVerseScheduleGame:
    game_id: str
    event_start_time: datetime
    home_team: str
    away_team: str
    season: int
    week: int
    game_type: str
    can_execute: bool = field(default=False, init=False)


def default_policy_path() -> Path:
    return Path(__file__).with_name("nflverse_dataset_policy_v1.json")


def load_nflverse_dataset_policy(
    path: str | Path | None = None,
) -> dict[str, NFLVerseDatasetPolicyEntry]:
    raw = json.loads(Path(path or default_policy_path()).read_text(encoding="utf-8"))
    if raw.get("schema_version") != "WOW_NFLVERSE_DATASET_POLICY_V1":
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_DATASET_POLICY_VERSION_INVALID",
            "expected WOW_NFLVERSE_DATASET_POLICY_V1",
        )
    if raw.get("provider") != "NFLVERSE" or raw.get("can_execute") is not False:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_DATASET_POLICY_INVALID",
            "provider must be NFLVERSE and can_execute must remain false",
        )

    entries: dict[str, NFLVerseDatasetPolicyEntry] = {}
    for item in raw.get("datasets", []):
        dataset = str(item.get("dataset") or "").strip().lower()
        if not dataset or dataset in entries:
            raise NFLVerseHistoricalAdapterError(
                "NFLVERSE_DATASET_POLICY_AMBIGUOUS",
                dataset or "missing dataset",
            )
        entries[dataset] = NFLVerseDatasetPolicyEntry(
            dataset=dataset,
            status=str(item.get("status") or ""),
            evidence_domain=str(item.get("evidence_domain") or ""),
            license=str(item.get("license") or ""),
            production_training_allowed=bool(item.get("production_training_allowed", False)),
            purpose=str(item.get("purpose") or ""),
        )
    if not entries:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_DATASET_POLICY_EMPTY", "dataset policy cannot be empty"
        )
    return entries


def require_allowed_dataset(
    dataset: str,
    *,
    policy: Mapping[str, NFLVerseDatasetPolicyEntry] | None = None,
) -> NFLVerseDatasetPolicyEntry:
    key = str(dataset or "").strip().lower()
    entries = dict(policy or load_nflverse_dataset_policy())
    entry = entries.get(key)
    if entry is None:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_DATASET_UNREGISTERED", key or "missing dataset"
        )
    if entry.evidence_domain != "SPORTING":
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_DATASET_DOMAIN_INVALID", f"{key}:{entry.evidence_domain}"
        )
    if not entry.production_training_allowed:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_DATASET_EXCLUDED", f"{key}:{entry.status}:{entry.license}"
        )
    return entry


def _nonempty(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_IDENTITY_INCOMPLETE", field_name
        )
    return text


def _integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise NFLVerseHistoricalAdapterError("NFLVERSE_STAT_INVALID", field_name)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_STAT_INVALID", field_name
        ) from exc
    if not number.is_integer():
        raise NFLVerseHistoricalAdapterError("NFLVERSE_STAT_INVALID", field_name)
    return int(number)


def parse_nflverse_kickoff(schedule_row: Mapping[str, Any]) -> datetime:
    """Parse nflverse's documented Eastern-time ``gameday`` + ``gametime``."""
    gameday = _nonempty(schedule_row.get("gameday"), "gameday")
    gametime = _nonempty(schedule_row.get("gametime"), "gametime")
    try:
        local = datetime.fromisoformat(f"{gameday}T{gametime}").replace(tzinfo=NFL_EASTERN)
    except ValueError as exc:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_SCHEDULE_TIMESTAMP_INVALID", f"{gameday} {gametime}"
        ) from exc
    return local.astimezone(UTC)


def normalize_schedule_row(schedule_row: Mapping[str, Any]) -> NFLVerseScheduleGame:
    require_allowed_dataset("schedules")
    game_id = _nonempty(schedule_row.get("game_id"), "game_id")
    home = _nonempty(schedule_row.get("home_team"), "home_team").upper()
    away = _nonempty(schedule_row.get("away_team"), "away_team").upper()
    if home == away:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_SCHEDULE_TEAMS_INVALID", game_id
        )
    try:
        season = int(schedule_row.get("season"))
        week = int(schedule_row.get("week"))
    except (TypeError, ValueError) as exc:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_SCHEDULE_IDENTITY_INVALID", game_id
        ) from exc
    return NFLVerseScheduleGame(
        game_id=game_id,
        event_start_time=parse_nflverse_kickoff(schedule_row),
        home_team=home,
        away_team=away,
        season=season,
        week=week,
        game_type=_nonempty(schedule_row.get("game_type"), "game_type").upper(),
    )


def build_schedule_index(
    schedule_rows: Iterable[Mapping[str, Any]],
) -> dict[str, NFLVerseScheduleGame]:
    index: dict[str, NFLVerseScheduleGame] = {}
    for raw in schedule_rows:
        game = normalize_schedule_row(raw)
        if game.game_id in index:
            raise NFLVerseHistoricalAdapterError(
                "NFLVERSE_SCHEDULE_DUPLICATE_GAME", game.game_id
            )
        index[game.game_id] = game
    return index


def _validate_team_pair(
    *, game: NFLVerseScheduleGame, team: str, opponent: str
) -> None:
    expected = {game.home_team, game.away_team}
    if {team, opponent} != expected or team == opponent:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_PLAYER_GAME_TEAM_MISMATCH",
            f"{game.game_id}:{team}:{opponent}:{game.away_team}@{game.home_team}",
        )


def normalize_player_stats_row(
    player_row: Mapping[str, Any],
    *,
    schedule_index: Mapping[str, NFLVerseScheduleGame],
    source_retrieved_at: datetime,
    source_payload_hash: str,
    stat_types: Iterable[str] | None = None,
) -> tuple[NormalizedPlayerGameOutcome, ...]:
    """Convert one nflverse player-game row into governed settled outcomes.

    Only fields in ``STAT_FIELD_MAP`` can become sporting labels. Odds/spreads/market
    columns, even if present in input mappings, are intentionally ignored.
    """
    require_allowed_dataset("player_stats")
    if source_retrieved_at.tzinfo is None or source_retrieved_at.utcoffset() is None:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_SOURCE_TIMESTAMP_TIMEZONE_REQUIRED", "source_retrieved_at"
        )
    if not str(source_payload_hash or "").strip():
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_SOURCE_HASH_MISSING", "source_payload_hash"
        )

    game_id = _nonempty(player_row.get("game_id"), "game_id")
    game = schedule_index.get(game_id)
    if game is None:
        raise NFLVerseHistoricalAdapterError(
            "NFLVERSE_SCHEDULE_GAME_UNRESOLVED", game_id
        )

    player_id = _nonempty(player_row.get("player_id"), "player_id")
    team = _nonempty(player_row.get("team"), "team").upper()
    opponent = _nonempty(player_row.get("opponent_team"), "opponent_team").upper()
    _validate_team_pair(game=game, team=team, opponent=opponent)

    identity = CanonicalIdentity(
        sport="NFL",
        event_id=game.game_id,
        participant_id=player_id,
        team_id=team,
        opponent_id=opponent,
        provider_ids={"NFLVERSE_GSIS_ID": player_id, "NFLVERSE_GAME_ID": game.game_id},
    )

    requested = tuple(stat_types or STAT_FIELD_MAP.keys())
    outcomes: list[NormalizedPlayerGameOutcome] = []
    for stat_type in requested:
        canonical = str(stat_type or "").strip().upper()
        field_name = STAT_FIELD_MAP.get(canonical)
        if field_name is None:
            raise NFLVerseHistoricalAdapterError(
                "NFLVERSE_STAT_TYPE_UNSUPPORTED", canonical
            )
        value = player_row.get(field_name)
        if value is None or value == "":
            continue
        outcomes.append(
            NormalizedPlayerGameOutcome(
                identity=identity,
                event_start_time=game.event_start_time,
                outcome_as_of=source_retrieved_at.astimezone(UTC),
                stat_type=canonical,
                actual_value=float(_integer(value, field_name)),
                source_provider="NFLVERSE",
                source_payload_hash=source_payload_hash,
            )
        )
    return tuple(outcomes)


def normalize_player_stats_corpus(
    player_rows: Iterable[Mapping[str, Any]],
    *,
    schedule_rows: Iterable[Mapping[str, Any]],
    source_retrieved_at: datetime,
    player_stats_payload_hash: str,
    stat_types: Iterable[str] | None = None,
) -> tuple[NormalizedPlayerGameOutcome, ...]:
    index = build_schedule_index(schedule_rows)
    output: list[NormalizedPlayerGameOutcome] = []
    for row in player_rows:
        output.extend(
            normalize_player_stats_row(
                row,
                schedule_index=index,
                source_retrieved_at=source_retrieved_at,
                source_payload_hash=player_stats_payload_hash,
                stat_types=stat_types,
            )
        )
    return tuple(output)
