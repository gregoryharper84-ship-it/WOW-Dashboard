"""Normalize staged CFBD player box scores for NCAAF prop-model research.

This module is deliberately pre-model. It turns immutable read-only
``/games/players`` source snapshots into exact primitive player/game/stat rows.
It does not fit, calibrate, certify, publish, rank, or execute a probability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional

from ncaaf_cfbd_hydrator import SourceSnapshot

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
FEATURE_SCHEMA_VERSION = "NCAAF_PLAYER_STAT_HISTORY_V1"

# Primitive stat families needed by the initial CFB prop build. Derived/combo
# markets intentionally remain out of this materializer.
_TYPE_MAP: dict[tuple[str, str], str] = {
    ("passing", "yds"): "PASS_YARDS",
    ("passing", "td"): "PASS_TDS",
    ("passing", "int"): "INTERCEPTIONS_THROWN",
    ("rushing", "yds"): "RUSH_YARDS",
    ("rushing", "td"): "RUSH_TDS",
    ("rushing", "car"): "RUSH_ATTEMPTS",
    ("receiving", "yds"): "RECEIVING_YARDS",
    ("receiving", "td"): "RECEIVING_TDS",
    ("receiving", "rec"): "RECEPTIONS",
}


@dataclass(frozen=True)
class PlayerStatObservation:
    provider: str
    source_endpoint: str
    source_payload_sha256: str
    source_retrieved_at: str
    season: int
    week: int
    game_id: str
    team: str
    conference: Optional[str]
    home_away: Optional[str]
    athlete_id: str
    athlete_name: str
    stat_type: str
    stat_value: float
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    can_execute: bool = False


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "")


def _number(value: Any) -> float:
    text = str(value or "").strip().replace(",", "")
    try:
        result = float(Decimal(text))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid numeric player stat {value!r}") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"non-finite player stat {value!r}")
    return result


def _split_attempts(value: Any) -> tuple[float, float]:
    text = str(value or "").strip()
    parts = text.split("/")
    if len(parts) != 2:
        raise ValueError(f"invalid C/ATT player stat {value!r}")
    return _number(parts[0]), _number(parts[1])


def _stat_values(category: str, type_name: str, raw_stat: Any) -> list[tuple[str, float]]:
    cat = _norm(category)
    typ = _norm(type_name)
    if cat == "passing" and typ in {"c/att", "catt", "cmpatt", "compatt"}:
        completions, attempts = _split_attempts(raw_stat)
        return [("PASS_COMPLETIONS", completions), ("PASS_ATTEMPTS", attempts)]
    stat_type = _TYPE_MAP.get((cat, typ))
    if stat_type is None:
        return []
    return [(stat_type, _number(raw_stat))]


def materialize_player_stats(snapshot: SourceSnapshot) -> list[PlayerStatObservation]:
    """Flatten one staged ``/games/players`` snapshot.

    Invalid structural/numeric rows fail the run rather than silently becoming
    training zeros. Unknown non-core stat types are ignored deliberately; the
    raw source snapshot remains available for later certified expansion.
    """
    if snapshot.provider != "CFBD" or snapshot.endpoint != "/games/players":
        return []
    if snapshot.week is None:
        raise ValueError("player-stat snapshots must be week-addressed")

    observations: list[PlayerStatObservation] = []
    for game in snapshot.response_rows:
        game_id = str(game.get("id") or "").strip()
        teams = game.get("teams")
        if not game_id or not isinstance(teams, list):
            raise ValueError("CFBD player game row missing id/teams")
        for team_row in teams:
            if not isinstance(team_row, Mapping):
                raise ValueError("CFBD player team row must be an object")
            team = str(team_row.get("team") or "").strip()
            if not team:
                raise ValueError("CFBD player team row missing team")
            categories = team_row.get("categories")
            if not isinstance(categories, list):
                raise ValueError("CFBD player team row missing categories")
            for category_row in categories:
                if not isinstance(category_row, Mapping):
                    raise ValueError("CFBD player category row must be an object")
                category = str(category_row.get("name") or "").strip()
                types = category_row.get("types")
                if not category or not isinstance(types, list):
                    raise ValueError("CFBD player category row missing name/types")
                for type_row in types:
                    if not isinstance(type_row, Mapping):
                        raise ValueError("CFBD player stat type row must be an object")
                    type_name = str(type_row.get("name") or "").strip()
                    athletes = type_row.get("athletes")
                    if not type_name or not isinstance(athletes, list):
                        raise ValueError("CFBD player stat type row missing name/athletes")
                    for athlete in athletes:
                        if not isinstance(athlete, Mapping):
                            raise ValueError("CFBD athlete row must be an object")
                        athlete_id = str(athlete.get("id") or "").strip()
                        athlete_name = str(athlete.get("name") or "").strip()
                        if not athlete_id or not athlete_name:
                            raise ValueError("CFBD athlete row missing identity")
                        for stat_type, stat_value in _stat_values(category, type_name, athlete.get("stat")):
                            observations.append(
                                PlayerStatObservation(
                                    provider="CFBD",
                                    source_endpoint=snapshot.endpoint,
                                    source_payload_sha256=snapshot.payload_sha256,
                                    source_retrieved_at=snapshot.retrieved_at,
                                    season=int(snapshot.season),
                                    week=int(snapshot.week),
                                    game_id=game_id,
                                    team=team,
                                    conference=(str(team_row.get("conference")).strip() if team_row.get("conference") is not None else None),
                                    home_away=(str(team_row.get("homeAway")).strip() if team_row.get("homeAway") is not None else None),
                                    athlete_id=athlete_id,
                                    athlete_name=athlete_name,
                                    stat_type=stat_type,
                                    stat_value=stat_value,
                                )
                            )
    return observations


def persist_player_stats(supabase_client: Any, rows: Iterable[PlayerStatObservation]) -> int:
    payload = [asdict(row) for row in rows]
    if not payload:
        return 0
    result = supabase_client.table("wow_ncaaf_player_stat_history").upsert(
        payload,
        on_conflict="season,week,game_id,team,athlete_id,stat_type,source_payload_sha256",
    ).execute()
    data = getattr(result, "data", None)
    return len(data) if isinstance(data, list) else 0
