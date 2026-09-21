"""Exact official-stat settlement overlay for WNBA P/R/A composite candidates.

The existing settlement engine already owns WNBA event/player identity and official
LeagueGameLog retrieval. This overlay extends that exact same source to composite
stats by summing PTS/REB/AST from one official player-event row. It never infers a
neighboring event, combines games, uses sportsbook settlement, or grants model,
calibration, publication, ranking, certification, promotion or execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import httpx

import v17.prop_exact_route_settlement as settlement

COMPOSITE_COLUMNS: dict[str, tuple[str, ...]] = {
    "PRA": ("PTS", "REB", "AST"),
    "POINTS_REBOUNDS": ("PTS", "REB"),
    "POINTS_ASSISTS": ("PTS", "AST"),
    "REBOUNDS_ASSISTS": ("REB", "AST"),
}
COMPOSITE_ROUTES = frozenset(("WNBA", stat) for stat in COMPOSITE_COLUMNS)
_ORIGINAL_SETTLE_WNBA_SCALAR = settlement.settle_wnba_scalar


def _settle_composite(
    prediction: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    role = snapshot.get("role_status") if isinstance(snapshot.get("role_status"), Mapping) else {}
    game_id = str(role.get("official_game_id") or "").strip()
    player_id = str(role.get("player_id") or "").strip()
    if not game_id or not player_id:
        return {
            "status": "IDENTITY_UNRESOLVED",
            "blocker": "WNBA_OFFICIAL_GAME_AND_PLAYER_ID_REQUIRED",
            "can_execute": False,
        }

    try:
        schedule = settlement._request_json(
            settlement.WNBA_SCHEDULE_URL,
            http_get=http_get,
            headers={"User-Agent": settlement._wnba_stats_headers()["User-Agent"], "Accept": "application/json"},
        )
    except Exception as exc:
        return {"status": "OFFICIAL_SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "can_execute": False}
    game = settlement._wnba_game(schedule, game_id)
    if game is None:
        return {"status": "OFFICIAL_EVENT_ID_MISMATCH", "can_execute": False}
    if int(game.get("gameStatus") or 0) != 3:
        return {"status": "NOT_FINAL", "game_status": game.get("gameStatus"), "can_execute": False}

    event_start = settlement._aware(prediction.get("event_start_time"))
    if event_start is None:
        return {"status": "IDENTITY_UNRESOLVED", "blocker": "EVENT_START_REQUIRED", "can_execute": False}
    try:
        payload = settlement._request_json(
            settlement.WNBA_STATS_URL,
            http_get=http_get,
            params={
                "LeagueID": "10", "PlayerOrTeam": "P", "Season": str(event_start.year),
                "SeasonType": "Regular Season", "Counter": "0", "DateFrom": "", "DateTo": "",
                "Direction": "ASC", "Sorter": "DATE",
            },
            headers=settlement._wnba_stats_headers(),
        )
        rows = settlement._result_rows(payload)
    except Exception as exc:
        return {"status": "OFFICIAL_SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "can_execute": False}

    matches = [
        row for row in rows
        if str(row.get("GAME_ID") or "") == game_id
        and str(row.get("PLAYER_ID") or row.get("PERSON_ID") or "") == player_id
    ]
    source = f"{settlement.WNBA_STATS_URL}?LeagueID=10&Season={event_start.year}&PlayerOrTeam=P"
    if not matches:
        return {
            "status": "SETTLED_VOID_DNP",
            "outcome": settlement._settlement_result(
                prediction=prediction,
                actual_stat=None,
                source=source,
                now=now,
                void_reason="PLAYER_DNP",
            ),
            "can_execute": False,
        }
    if len(matches) != 1:
        return {"status": "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS", "match_n": len(matches), "can_execute": False}

    stat_type = str(prediction.get("stat_type") or "").upper()
    columns = COMPOSITE_COLUMNS.get(stat_type)
    if columns is None:
        return _ORIGINAL_SETTLE_WNBA_SCALAR(prediction, snapshot, http_get=http_get, now=now)
    values = [settlement._finite_number(matches[0].get(column)) for column in columns]
    if any(value is None for value in values):
        return {
            "status": "OFFICIAL_STAT_MISSING",
            "stat_columns": list(columns),
            "can_execute": False,
        }
    actual = float(sum(value for value in values if value is not None))
    return {
        "status": "SETTLED",
        "outcome": settlement._settlement_result(
            prediction=prediction,
            actual_stat=actual,
            source=source,
            now=now,
        ),
        "settlement_components": {column: value for column, value in zip(columns, values)},
        "composite_method": "EXACT_SAME_OFFICIAL_PLAYER_EVENT_ROW_SUM",
        "can_execute": False,
    }


def install() -> None:
    settlement.WNBA_SUPPORTED = frozenset(set(settlement.WNBA_SUPPORTED) | set(COMPOSITE_ROUTES))
    settlement.SUPPORTED_SETTLEMENT_ROUTES = settlement.MLB_SUPPORTED | settlement.WNBA_SUPPORTED
    settlement.settle_wnba_scalar = _settle_composite


install()


__all__ = ["COMPOSITE_COLUMNS", "COMPOSITE_ROUTES", "install"]
