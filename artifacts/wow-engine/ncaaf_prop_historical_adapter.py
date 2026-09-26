"""Governed CFBD historical adapter for NCAAF Phase-1 player props.

This adapter converts settled CFBD `/games/players` sporting box-score rows into
WOW's shared `NormalizedPlayerGameOutcome` contract. It does not fit a model,
calibrate probabilities, register a specialist, publish probabilities, or enable
execution.

The mapping is intentionally narrow. Only the seven Phase-1 stat families are
admitted and every outcome is bound to a completed `/games` event with canonical
team/opponent identity and immutable source hashes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping, Sequence

from historical_data_backbone import (
    CanonicalIdentity,
    HistoricalDataContractError,
    NormalizedPlayerGameOutcome,
)
from ncaaf_cfbd_hydrator import SourceSnapshot

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False

PHASE1_STAT_TYPES = (
    "PASSING_YARDS",
    "COMPLETIONS",
    "RUSHING_YARDS",
    "RECEPTIONS",
    "RECEIVING_YARDS",
    "RUSH_ATTEMPTS",
    "PASS_ATTEMPTS",
)

_COUNT_PATTERN = re.compile(r"^[+-]?\d+(?:\.0+)?$")


class NCAAFPropHistoricalAdapterError(HistoricalDataContractError):
    pass


@dataclass(frozen=True)
class NCAAFScheduleGame:
    game_id: str
    event_start_time: datetime
    home_team: str
    away_team: str
    season: int
    week: int
    season_type: str
    completed: bool
    source_payload_hash: str
    source_retrieved_at: datetime
    can_execute: bool = field(default=False, init=False)


def _required_text(value: Any, code: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NCAAFPropHistoricalAdapterError(code, field_name)
    return text


def _aware_datetime(value: Any, code: str, field_name: str) -> datetime:
    text = _required_text(value, code, field_name)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise NCAAFPropHistoricalAdapterError(code, field_name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NCAAFPropHistoricalAdapterError(code, field_name)
    return parsed.astimezone(timezone.utc)


def _snapshot_retrieved_at(snapshot: SourceSnapshot) -> datetime:
    return _aware_datetime(
        snapshot.retrieved_at,
        "NCAAF_PROP_SOURCE_TIMESTAMP_INVALID",
        "retrieved_at",
    )


def build_schedule_index(
    snapshots: Iterable[SourceSnapshot],
) -> dict[str, NCAAFScheduleGame]:
    """Index completed CFBD game rows by official event id.

    Duplicated event ids are accepted only when immutable identity fields agree;
    the most recently retrieved identical observation is retained. Conflicting
    identity fails closed rather than silently selecting a source row.
    """
    index: dict[str, NCAAFScheduleGame] = {}
    for snapshot in snapshots:
        if snapshot.endpoint != "/games" or snapshot.acquisition_status != "AVAILABLE":
            continue
        retrieved_at = _snapshot_retrieved_at(snapshot)
        for raw in snapshot.response_rows:
            if not isinstance(raw, Mapping) or raw.get("completed") is not True:
                continue
            game_id = _required_text(
                raw.get("id"), "NCAAF_PROP_SCHEDULE_IDENTITY_INVALID", "id"
            )
            home = _required_text(
                raw.get("homeTeam"), "NCAAF_PROP_SCHEDULE_IDENTITY_INVALID", "homeTeam"
            )
            away = _required_text(
                raw.get("awayTeam"), "NCAAF_PROP_SCHEDULE_IDENTITY_INVALID", "awayTeam"
            )
            if home == away:
                raise NCAAFPropHistoricalAdapterError(
                    "NCAAF_PROP_SCHEDULE_TEAMS_INVALID", game_id
                )
            try:
                season = int(raw.get("season"))
                week = int(raw.get("week"))
            except (TypeError, ValueError) as exc:
                raise NCAAFPropHistoricalAdapterError(
                    "NCAAF_PROP_SCHEDULE_IDENTITY_INVALID", game_id
                ) from exc
            start = _aware_datetime(
                raw.get("startDate"),
                "NCAAF_PROP_SCHEDULE_TIMESTAMP_INVALID",
                f"{game_id}:startDate",
            )
            game = NCAAFScheduleGame(
                game_id=game_id,
                event_start_time=start,
                home_team=home,
                away_team=away,
                season=season,
                week=week,
                season_type=_required_text(
                    raw.get("seasonType"),
                    "NCAAF_PROP_SCHEDULE_IDENTITY_INVALID",
                    f"{game_id}:seasonType",
                ),
                completed=True,
                source_payload_hash=snapshot.payload_sha256,
                source_retrieved_at=retrieved_at,
            )
            previous = index.get(game_id)
            if previous is not None:
                identity_before = (
                    previous.event_start_time,
                    previous.home_team,
                    previous.away_team,
                    previous.season,
                    previous.week,
                    previous.season_type,
                )
                identity_after = (
                    game.event_start_time,
                    game.home_team,
                    game.away_team,
                    game.season,
                    game.week,
                    game.season_type,
                )
                if identity_before != identity_after:
                    raise NCAAFPropHistoricalAdapterError(
                        "NCAAF_PROP_SCHEDULE_IDENTITY_CONFLICT", game_id
                    )
                if previous.source_retrieved_at >= game.source_retrieved_at:
                    continue
            index[game_id] = game
    return index


def _parse_integer_stat(value: Any, *, field_name: str) -> float:
    text = str(value).strip()
    if not _COUNT_PATTERN.fullmatch(text):
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_STAT_VALUE_INVALID", f"{field_name}:{text}"
        )
    number = float(text)
    if not number.is_integer():
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_STAT_VALUE_INVALID", f"{field_name}:{text}"
        )
    return number


def _parse_completions_attempts(value: Any) -> tuple[float, float]:
    text = str(value or "").strip()
    pieces = text.split("/")
    if len(pieces) != 2:
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_PASSING_C_ATT_INVALID", text or "missing"
        )
    completions = _parse_integer_stat(pieces[0], field_name="passing:C/ATT:completions")
    attempts = _parse_integer_stat(pieces[1], field_name="passing:C/ATT:attempts")
    if completions < 0 or attempts < 0 or completions > attempts:
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_PASSING_C_ATT_INVALID", text
        )
    return completions, attempts


def _requested_types(stat_types: Iterable[str] | None) -> tuple[str, ...]:
    requested = tuple(str(value or "").strip().upper() for value in (stat_types or PHASE1_STAT_TYPES))
    unsupported = sorted({value for value in requested if value not in PHASE1_STAT_TYPES})
    if unsupported:
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_STAT_TYPE_UNSUPPORTED", ",".join(unsupported)
        )
    return requested


def _emit(
    outcomes: list[NormalizedPlayerGameOutcome],
    seen: set[tuple[str, str]],
    *,
    game: NCAAFScheduleGame,
    player_id: str,
    team: str,
    opponent: str,
    stat_type: str,
    actual_value: float,
    source_snapshot: SourceSnapshot,
) -> None:
    key = (player_id, stat_type)
    if key in seen:
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_DUPLICATE_PLAYER_STAT", f"{game.game_id}:{player_id}:{stat_type}"
        )
    seen.add(key)
    outcome_as_of = _snapshot_retrieved_at(source_snapshot)
    if outcome_as_of < game.event_start_time:
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_OUTCOME_TIMESTAMP_PREMATURE", game.game_id
        )
    identity = CanonicalIdentity(
        sport="NCAAF",
        event_id=game.game_id,
        participant_id=player_id,
        team_id=team,
        opponent_id=opponent,
        provider_ids={
            "CFBD_PLAYER_ID": player_id,
            "CFBD_GAME_ID": game.game_id,
        },
    )
    outcomes.append(
        NormalizedPlayerGameOutcome(
            identity=identity,
            event_start_time=game.event_start_time,
            outcome_as_of=outcome_as_of,
            stat_type=stat_type,
            actual_value=float(actual_value),
            source_provider="CFBD",
            source_payload_hash=source_snapshot.payload_sha256,
        )
    )


def normalize_player_game(
    raw_game: Mapping[str, Any],
    *,
    source_snapshot: SourceSnapshot,
    schedule_index: Mapping[str, NCAAFScheduleGame],
    stat_types: Iterable[str] | None = None,
) -> tuple[NormalizedPlayerGameOutcome, ...]:
    """Normalize one CFBD `/games/players` game into Phase-1 settled outcomes."""
    if source_snapshot.endpoint != "/games/players":
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_SOURCE_ENDPOINT_INVALID", source_snapshot.endpoint
        )
    if source_snapshot.acquisition_status != "AVAILABLE":
        return ()
    requested = set(_requested_types(stat_types))
    game_id = _required_text(
        raw_game.get("id"), "NCAAF_PROP_PLAYER_GAME_IDENTITY_INVALID", "id"
    )
    game = schedule_index.get(game_id)
    if game is None:
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_SCHEDULE_GAME_UNRESOLVED", game_id
        )
    teams = raw_game.get("teams")
    if not isinstance(teams, list) or len(teams) != 2:
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_PLAYER_GAME_TEAMS_INVALID", game_id
        )
    team_names = [
        _required_text(
            row.get("team") if isinstance(row, Mapping) else None,
            "NCAAF_PROP_PLAYER_GAME_TEAM_IDENTITY_INVALID",
            game_id,
        )
        for row in teams
    ]
    if len(set(team_names)) != 2 or set(team_names) != {game.home_team, game.away_team}:
        raise NCAAFPropHistoricalAdapterError(
            "NCAAF_PROP_PLAYER_GAME_TEAM_MISMATCH",
            f"{game_id}:{team_names}:{game.away_team}@{game.home_team}",
        )

    outcomes: list[NormalizedPlayerGameOutcome] = []
    seen: set[tuple[str, str]] = set()
    for team_row in teams:
        if not isinstance(team_row, Mapping):
            raise NCAAFPropHistoricalAdapterError(
                "NCAAF_PROP_PLAYER_GAME_TEAM_IDENTITY_INVALID", game_id
            )
        team = _required_text(
            team_row.get("team"), "NCAAF_PROP_PLAYER_GAME_TEAM_IDENTITY_INVALID", game_id
        )
        opponent = game.away_team if team == game.home_team else game.home_team
        categories = team_row.get("categories")
        if not isinstance(categories, list):
            raise NCAAFPropHistoricalAdapterError(
                "NCAAF_PROP_PLAYER_GAME_CATEGORIES_INVALID", f"{game_id}:{team}"
            )
        for category in categories:
            if not isinstance(category, Mapping):
                continue
            category_name = str(category.get("name") or "").strip().lower()
            if category_name not in {"passing", "rushing", "receiving"}:
                continue
            types = category.get("types")
            if not isinstance(types, list):
                raise NCAAFPropHistoricalAdapterError(
                    "NCAAF_PROP_PLAYER_GAME_TYPES_INVALID",
                    f"{game_id}:{team}:{category_name}",
                )
            for stat_row in types:
                if not isinstance(stat_row, Mapping):
                    continue
                type_name = str(stat_row.get("name") or "").strip().upper()
                athletes = stat_row.get("athletes")
                if not isinstance(athletes, list):
                    raise NCAAFPropHistoricalAdapterError(
                        "NCAAF_PROP_PLAYER_GAME_ATHLETES_INVALID",
                        f"{game_id}:{team}:{category_name}:{type_name}",
                    )
                for athlete in athletes:
                    if not isinstance(athlete, Mapping):
                        continue
                    player_id = _required_text(
                        athlete.get("id"),
                        "NCAAF_PROP_PLAYER_IDENTITY_INVALID",
                        f"{game_id}:{team}:{category_name}:{type_name}",
                    )
                    raw_value = athlete.get("stat")
                    if raw_value is None or str(raw_value).strip() == "":
                        continue

                    if category_name == "passing" and type_name in {"YDS", "YARDS"}:
                        if "PASSING_YARDS" in requested:
                            _emit(outcomes, seen, game=game, player_id=player_id, team=team, opponent=opponent, stat_type="PASSING_YARDS", actual_value=_parse_integer_stat(raw_value, field_name="passing:YDS"), source_snapshot=source_snapshot)
                    elif category_name == "passing" and type_name in {"C/ATT", "CMP/ATT"}:
                        completions, attempts = _parse_completions_attempts(raw_value)
                        if "COMPLETIONS" in requested:
                            _emit(outcomes, seen, game=game, player_id=player_id, team=team, opponent=opponent, stat_type="COMPLETIONS", actual_value=completions, source_snapshot=source_snapshot)
                        if "PASS_ATTEMPTS" in requested:
                            _emit(outcomes, seen, game=game, player_id=player_id, team=team, opponent=opponent, stat_type="PASS_ATTEMPTS", actual_value=attempts, source_snapshot=source_snapshot)
                    elif category_name == "rushing" and type_name in {"YDS", "YARDS"}:
                        if "RUSHING_YARDS" in requested:
                            _emit(outcomes, seen, game=game, player_id=player_id, team=team, opponent=opponent, stat_type="RUSHING_YARDS", actual_value=_parse_integer_stat(raw_value, field_name="rushing:YDS"), source_snapshot=source_snapshot)
                    elif category_name == "rushing" and type_name in {"CAR", "ATT"}:
                        if "RUSH_ATTEMPTS" in requested:
                            _emit(outcomes, seen, game=game, player_id=player_id, team=team, opponent=opponent, stat_type="RUSH_ATTEMPTS", actual_value=_parse_integer_stat(raw_value, field_name="rushing:CAR"), source_snapshot=source_snapshot)
                    elif category_name == "receiving" and type_name in {"REC", "RECEPTIONS"}:
                        if "RECEPTIONS" in requested:
                            _emit(outcomes, seen, game=game, player_id=player_id, team=team, opponent=opponent, stat_type="RECEPTIONS", actual_value=_parse_integer_stat(raw_value, field_name="receiving:REC"), source_snapshot=source_snapshot)
                    elif category_name == "receiving" and type_name in {"YDS", "YARDS"}:
                        if "RECEIVING_YARDS" in requested:
                            _emit(outcomes, seen, game=game, player_id=player_id, team=team, opponent=opponent, stat_type="RECEIVING_YARDS", actual_value=_parse_integer_stat(raw_value, field_name="receiving:YDS"), source_snapshot=source_snapshot)

    return tuple(outcomes)


def normalize_player_stats_corpus(
    player_snapshots: Sequence[SourceSnapshot],
    *,
    game_snapshots: Sequence[SourceSnapshot],
    stat_types: Iterable[str] | None = None,
) -> tuple[NormalizedPlayerGameOutcome, ...]:
    schedule_index = build_schedule_index(game_snapshots)
    requested = _requested_types(stat_types)
    output: list[NormalizedPlayerGameOutcome] = []
    seen_games: set[tuple[str, str]] = set()
    for snapshot in player_snapshots:
        if snapshot.endpoint != "/games/players" or snapshot.acquisition_status != "AVAILABLE":
            continue
        for raw_game in snapshot.response_rows:
            if not isinstance(raw_game, Mapping):
                raise NCAAFPropHistoricalAdapterError(
                    "NCAAF_PROP_PLAYER_GAME_ROW_INVALID", type(raw_game).__name__
                )
            game_id = _required_text(
                raw_game.get("id"), "NCAAF_PROP_PLAYER_GAME_IDENTITY_INVALID", "id"
            )
            duplicate_key = (game_id, snapshot.payload_sha256)
            if duplicate_key in seen_games:
                continue
            seen_games.add(duplicate_key)
            output.extend(
                normalize_player_game(
                    raw_game,
                    source_snapshot=snapshot,
                    schedule_index=schedule_index,
                    stat_types=requested,
                )
            )
    return tuple(output)


__all__ = [
    "CAN_EXECUTE",
    "NCAAFPropHistoricalAdapterError",
    "NCAAFScheduleGame",
    "PHASE1_STAT_TYPES",
    "PROBABILITY_PUBLISHABLE",
    "build_schedule_index",
    "normalize_player_game",
    "normalize_player_stats_corpus",
]
