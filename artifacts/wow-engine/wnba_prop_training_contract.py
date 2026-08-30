"""Governed historical training contract for future WNBA prop fitted models.

This module is deliberately pre-certification. It validates auditable historical
rows for offline training/readiness work and never produces a probability,
registers a model family, or changes runtime capability state.

can_execute is false by construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

SUPPORTED_STATS = {"PTS", "REB", "AST", "3PM"}
MIN_PLAYER_GAMES = 20
MIN_TOTAL_ROWS = 500


class WNBAPropTrainingContractError(ValueError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class WNBAPropHistoricalRow:
    event_id: str
    game_date: date
    event_start_time: datetime
    player_id: str
    player_name: str
    team: str
    opponent: str
    minutes: float
    starter: bool
    pts: int
    reb: int
    ast: int
    three_pm: int
    source_identity: str
    source_timestamp: datetime
    ingested_at: datetime

    @property
    def can_execute(self) -> bool:
        return False

    def stat_value(self, stat_type: str) -> int:
        stat = canonical_stat(stat_type)
        return {"PTS": self.pts, "REB": self.reb, "AST": self.ast, "3PM": self.three_pm}[stat]


def _aware_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise WNBAPropTrainingContractError("WNBA_TRAINING_TIMESTAMP_INVALID", field) from exc
    if parsed.utcoffset() is None:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_TIMESTAMP_TIMEZONE_REQUIRED", field)
    return parsed.astimezone(timezone.utc)


def canonical_stat(value: str) -> str:
    raw = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "POINTS": "PTS",
        "REBOUNDS": "REB",
        "ASSISTS": "AST",
        "3_POINTERS_MADE": "3PM",
        "THREES_MADE": "3PM",
        "THREE_POINTERS_MADE": "3PM",
    }
    stat = aliases.get(raw, raw)
    if stat not in SUPPORTED_STATS:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_STAT_UNSUPPORTED", stat)
    return stat


def _nonempty(value: Any, field: str) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_IDENTITY_INCOMPLETE", field)
    return normalized


def _integer_stat(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise WNBAPropTrainingContractError("WNBA_TRAINING_STAT_INVALID", field)
    try:
        parsed = int(value)
        original = float(value)
    except (TypeError, ValueError) as exc:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_STAT_INVALID", field) from exc
    if parsed < 0 or original != float(parsed):
        raise WNBAPropTrainingContractError("WNBA_TRAINING_STAT_INVALID", field)
    return parsed


def normalize_historical_row(payload: dict[str, Any]) -> WNBAPropHistoricalRow:
    if str(payload.get("sport") or "WNBA").strip().upper() != "WNBA":
        raise WNBAPropTrainingContractError("WNBA_TRAINING_SPORT_MISMATCH")

    event_start = _aware_datetime(payload.get("event_start_time"), "event_start_time")
    source_timestamp = _aware_datetime(payload.get("source_timestamp"), "source_timestamp")
    ingested_at = _aware_datetime(payload.get("ingested_at") or datetime.now(timezone.utc), "ingested_at")
    try:
        game_date = date.fromisoformat(str(payload.get("game_date")))
    except (TypeError, ValueError) as exc:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_GAME_DATE_INVALID") from exc

    if source_timestamp < event_start:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_SOURCE_PRE_RESULT")
    if ingested_at < source_timestamp:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_INGEST_PRE_SOURCE")

    try:
        minutes = float(payload.get("minutes"))
    except (TypeError, ValueError) as exc:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_MINUTES_INVALID") from exc
    if not 0 <= minutes <= 60:
        raise WNBAPropTrainingContractError("WNBA_TRAINING_MINUTES_INVALID")

    starter = payload.get("starter")
    if not isinstance(starter, bool):
        raise WNBAPropTrainingContractError("WNBA_TRAINING_ROLE_INVALID")

    return WNBAPropHistoricalRow(
        event_id=_nonempty(payload.get("event_id"), "event_id"),
        game_date=game_date,
        event_start_time=event_start,
        player_id=_nonempty(payload.get("player_id"), "player_id"),
        player_name=_nonempty(payload.get("player_name"), "player_name"),
        team=_nonempty(payload.get("team"), "team").upper(),
        opponent=_nonempty(payload.get("opponent"), "opponent").upper(),
        minutes=minutes,
        starter=starter,
        pts=_integer_stat(payload.get("pts"), "pts"),
        reb=_integer_stat(payload.get("reb"), "reb"),
        ast=_integer_stat(payload.get("ast"), "ast"),
        three_pm=_integer_stat(payload.get("three_pm"), "three_pm"),
        source_identity=_nonempty(payload.get("source_identity"), "source_identity"),
        source_timestamp=source_timestamp,
        ingested_at=ingested_at,
    )


def training_readiness(rows: Iterable[WNBAPropHistoricalRow], stat_type: str) -> dict[str, Any]:
    stat = canonical_stat(stat_type)
    materialized = list(rows)
    player_counts: dict[str, int] = {}
    seasons: set[int] = set()
    for row in materialized:
        player_counts[row.player_id] = player_counts.get(row.player_id, 0) + 1
        seasons.add(row.game_date.year)

    eligible_players = sum(1 for n in player_counts.values() if n >= MIN_PLAYER_GAMES)
    blockers: list[str] = []
    if len(materialized) < MIN_TOTAL_ROWS:
        blockers.append("WNBA_TRAINING_ROWS_BELOW_MINIMUM")
    if eligible_players == 0:
        blockers.append("WNBA_PLAYER_HISTORY_BELOW_MINIMUM")
    if not seasons:
        blockers.append("WNBA_TRAINING_SEASONS_EMPTY")

    return {
        "sport": "WNBA",
        "stat_type": stat,
        "historical_row_n": len(materialized),
        "unique_player_n": len(player_counts),
        "eligible_player_n": eligible_players,
        "season_years": sorted(seasons),
        "min_total_rows": MIN_TOTAL_ROWS,
        "min_player_games": MIN_PLAYER_GAMES,
        "training_status": "READY_FOR_OFFLINE_FIT" if not blockers else "TRAINING_DATA_UNAVAILABLE",
        "blockers": blockers,
        "runtime_model_status": "MODEL_UNAVAILABLE",
        "probability_publishable": False,
        "can_execute": False,
    }
