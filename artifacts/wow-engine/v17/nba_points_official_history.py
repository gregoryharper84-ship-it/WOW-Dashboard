"""Bounded official NBA POINTS L10 retrieval; research only, no DB/model writes.

Two official source contracts: NBA Stats playergamelog and per-game NBA CDN
boxscore. A logged game does not become evidence until it matches a final NBA
boxscore on exact player, game ID, actual points and UTC start time. Missing
current roster/line/role must be acquired separately; no scoring authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo
import math
import re

CAN_EXECUTE = False
STATS_URL = "https://stats.nba.com/stats/playergamelog"
BOX_BASE = "https://cdn.nba.com/static/json/liveData/boxscore/"
NBA_ZONE = ZoneInfo("America/New_York")
MAX_GAME_FETCHES = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WOW-V17-ReadOnly-Evidence/1.0)",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


class NBAHistoryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc(value: Any) -> datetime:
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise NBAHistoryError("NBA_HISTORY_TIME_INVALID") from exc
    if d.utcoffset() is None:
        raise NBAHistoryError("NBA_HISTORY_TIME_NAIVE")
    return d.astimezone(timezone.utc)


def _date(value: Any):
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except (ValueError, TypeError):
            pass
    raise NBAHistoryError("NBA_HISTORY_GAME_DATE_INVALID")


def _points(value: Any) -> float:
    if isinstance(value, bool):
        raise NBAHistoryError("NBA_HISTORY_POINTS_INVALID")
    try:
        n = float(value)
    except (ValueError, TypeError) as exc:
        raise NBAHistoryError("NBA_HISTORY_POINTS_INVALID") from exc
    if not math.isfinite(n) or n < 0 or not n.is_integer():
        raise NBAHistoryError("NBA_HISTORY_POINTS_INVALID")
    return n


def _get(url: str, *, http_get: Callable[..., Any],
         params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    try:
        response = http_get(url, params=dict(params or {}), headers=HEADERS, timeout=12.0)
        if int(response.status_code) != 200:
            raise NBAHistoryError("NBA_HISTORY_SOURCE_HTTP_FAILURE")
        data = response.json()
    except NBAHistoryError:
        raise
    except Exception as exc:
        raise NBAHistoryError("NBA_HISTORY_SOURCE_UNAVAILABLE") from exc
    if not isinstance(data, Mapping):
        raise NBAHistoryError("NBA_HISTORY_SOURCE_INVALID")
    return data


def _gamelog_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("resultSets")
    if not isinstance(groups, list):
        raise NBAHistoryError("NBA_HISTORY_GAMELOG_SCHEMA_INVALID")
    matches = [group for group in groups
               if isinstance(group, Mapping) and group.get("name") == "PlayerGameLog"]
    if len(matches) != 1:
        raise NBAHistoryError("NBA_HISTORY_GAMELOG_SCHEMA_INVALID")
    group = matches[0]
    headers, rows = group.get("headers"), group.get("rowSet")
    if (not isinstance(headers, list) or not isinstance(rows, list)
            or len(set(headers)) != len(headers)
            or not {"Game_ID", "GAME_DATE", "PTS", "Player_ID"}.issubset(set(headers))
            or any(not isinstance(row, list) or len(row) != len(headers) for row in rows)):
        raise NBAHistoryError("NBA_HISTORY_GAMELOG_SCHEMA_INVALID")
    return [dict(zip(headers, row)) for row in rows]


def acquire_nba_points_l10(
    *, player_id: str, event_start_time: str, seasons: tuple[str, ...],
    now: datetime, http_get: Callable[..., Any],
) -> dict[str, Any]:
    """Acquire 10 exact prior settled games, up to 2 provided seasons and 12 GETs.

    GAME_DATE never substitutes for a game start. CDN boxscore provides exact
    gameTimeUTC and independently verifies the player and POINTS observation.
    """
    if not re.fullmatch(r"[1-9][0-9]{0,9}", str(player_id)):
        raise NBAHistoryError("NBA_HISTORY_PLAYER_ID_INVALID")
    if (not seasons or len(seasons) > 2 or len(set(seasons)) != len(seasons)
            or any(re.fullmatch(r"20[0-9]{2}-[0-9]{2}", s) is None for s in seasons)):
        raise NBAHistoryError("NBA_HISTORY_SEASON_SCOPE_INVALID")
    now_utc, start = _utc(now.isoformat()), _utc(event_start_time)
    if start <= now_utc:
        raise NBAHistoryError("NBA_HISTORY_EVENT_NOT_PREGAME")
    cutoff_day = start.astimezone(NBA_ZONE).date()
    candidates: dict[str, dict[str, Any]] = {}
    for season in seasons:
        payload = _get(STATS_URL, http_get=http_get, params={
            "PlayerID": str(player_id), "Season": season,
            "SeasonType": "Regular Season", "LeagueID": "00",
        })
        for row in _gamelog_rows(payload):
            if str(row["Player_ID"]) != str(player_id):
                raise NBAHistoryError("NBA_HISTORY_PLAYER_ID_MISMATCH")
            game_id = str(row["Game_ID"])
            if not re.fullmatch(r"[0-9]{10}", game_id):
                raise NBAHistoryError("NBA_HISTORY_GAME_ID_INVALID")
            day = _date(row["GAME_DATE"])
            if day >= cutoff_day:
                continue
            pts = _points(row["PTS"])
            if game_id in candidates:
                if candidates[game_id]["points"] != pts:
                    raise NBAHistoryError("NBA_HISTORY_DUPLICATE_GAME_CONTRADICTION")
                continue
            candidates[game_id] = {"game_id": game_id, "game_date": day, "points": pts}
    chosen = sorted(
        candidates.values(), key=lambda r: (r["game_date"], r["game_id"]), reverse=True
    )[:MAX_GAME_FETCHES]
    if len(chosen) < MAX_GAME_FETCHES:
        raise NBAHistoryError("NBA_HISTORY_L10_INSUFFICIENT")
    records = []
    for item in chosen:
        payload = _get(f"{BOX_BASE}boxscore_{item['game_id']}.json", http_get=http_get)
        game = payload.get("game")
        if not isinstance(game, Mapping) or str(game.get("gameId")) != item["game_id"]:
            raise NBAHistoryError("NBA_HISTORY_BOX_GAME_ID_MISMATCH")
        if str(game.get("gameStatus")) != "3":
            raise NBAHistoryError("NBA_HISTORY_GAME_NOT_FINAL")
        kickoff = _utc(game.get("gameTimeUTC"))
        if (kickoff >= start or kickoff >= now_utc
                or kickoff.astimezone(NBA_ZONE).date() != item["game_date"]):
            raise NBAHistoryError("NBA_HISTORY_BOX_TIME_CONTRADICTION")
        players = []
        for side in ("homeTeam", "awayTeam"):
            team = game.get(side)
            if not isinstance(team, Mapping) or not isinstance(team.get("players"), list):
                raise NBAHistoryError("NBA_HISTORY_BOX_SCORE_SCHEMA_INVALID")
            players.extend(team["players"])
        matches = [p for p in players
                   if isinstance(p, Mapping) and str(p.get("personId")) == str(player_id)]
        if len(matches) != 1:
            raise NBAHistoryError("NBA_HISTORY_BOX_PLAYER_ID_MISSING_OR_DUPLICATE")
        stats = matches[0].get("statistics")
        if not isinstance(stats, Mapping) or _points(stats.get("points")) != item["points"]:
            raise NBAHistoryError("NBA_HISTORY_POINTS_DISAGREE_WITH_BOX")
        records.append({
            "event_id": f"nba-{item['game_id']}",
            "event_start_time": kickoff.isoformat(),
            "player_id": str(player_id),
            "points": item["points"],
            "official_game_id": item["game_id"],
        })
    return {
        "status": "NBA_HISTORY_L10_VERIFIED_NO_CURRENT_ROLE_OR_LINE",
        "player_id": str(player_id),
        "game_log": [record["points"] for record in records],
        "box_score_log": records,
        "historical_source_provider": "NBA_STATS_PLAYERGAMELOG_AND_NBA_CDN_BOXSCORE",
        "source_captured_at": now_utc.isoformat(),
        "provider_calls_max": len(seasons) + MAX_GAME_FETCHES,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "NBAHistoryError", "acquire_nba_points_l10"]
