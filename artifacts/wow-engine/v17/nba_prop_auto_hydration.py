"""Research-only official-source hydration for NBA scalar/composite prop candidates.

Sources:
- public NBA CDN current-season schedule;
- NBA Stats CommonTeamRoster (LeagueID=00);
- NBA Stats LeagueGameLog (LeagueID=00), strictly prior games only.

This module owns evidence acquisition only. It never creates a sporting
probability, calibration, certification, publication/ranking authority, or
execution authority. NBA candidate routes remain fail-closed for production.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import time
import unicodedata
from typing import Any, Callable, Mapping, Optional

import httpx

NBA_SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
NBA_STATS_BASE = "https://stats.nba.com/stats"
PROVIDER_ID = "NBA_OFFICIAL_STATS_CDN_RESEARCH_V1"
EVIDENCE_VERSION = "PROP_EVIDENCE_V1"
HTTP_TIMEOUT_SECONDS = 10.0
HTTP_ATTEMPTS = 2
MIN_PRIOR_GAMES = 10
MAX_EVENT_START_DELTA_SECONDS = 30 * 60
CAN_EXECUTE = False

COMPONENT_COLUMNS: dict[str, tuple[str, ...]] = {
    "POINTS": ("PTS",),
    "REBOUNDS": ("REB",),
    "ASSISTS": ("AST",),
    "PRA": ("PTS", "REB", "AST"),
    "POINTS_REBOUNDS": ("PTS", "REB"),
    "POINTS_ASSISTS": ("PTS", "AST"),
    "REBOUNDS_ASSISTS": ("REB", "AST"),
}
ALIASES = {
    "PTS": "POINTS", "REB": "REBOUNDS", "AST": "ASSISTS",
    "PTS+REB+AST": "PRA", "POINTS+REBOUNDS+ASSISTS": "PRA",
    "POINTS_REBOUNDS_ASSISTS": "PRA", "PTS_REB_AST": "PRA",
    "PTS+REB": "POINTS_REBOUNDS", "POINTS+REBOUNDS": "POINTS_REBOUNDS",
    "PTS+AST": "POINTS_ASSISTS", "POINTS+ASSISTS": "POINTS_ASSISTS",
    "REB+AST": "REBOUNDS_ASSISTS", "REBOUNDS+ASSISTS": "REBOUNDS_ASSISTS",
}


class NBAPropHydrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def canonical_stat(value: str) -> str:
    raw = "_".join(str(value or "").strip().upper().replace("-", " ").split())
    raw = ALIASES.get(raw, raw)
    if raw not in COMPONENT_COLUMNS:
        raise NBAPropHydrationError(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "NBA automatic hydration is not registered for this research route",
            detail={"sport": "NBA", "stat_type": raw},
        )
    return raw


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().casefold()
    return " ".join(text.split())


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NBAPropHydrationError("PROP_EVENT_START_INVALID", "event_start_time must be ISO 8601") from exc
    if parsed.utcoffset() is None:
        raise NBAPropHydrationError("PROP_EVENT_START_INVALID", "event_start_time must include a timezone")
    return parsed.astimezone(timezone.utc)


def _season_label(event_start: datetime, *, offset: int = 0) -> str:
    year = event_start.year if event_start.month >= 7 else event_start.year - 1
    year += offset
    return f"{year}-{str(year + 1)[-2:]}"


def _stats_headers() -> dict[str, str]:
    return {
        "Host": "stats.nba.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Origin": "https://www.nba.com",
        "Referer": "https://www.nba.com/",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
    }


def _cdn_headers() -> dict[str, str]:
    return {
        "Host": "cdn.nba.com",
        "User-Agent": _stats_headers()["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nba.com",
        "Referer": "https://www.nba.com/",
        "Cache-Control": "no-cache",
    }


def _request(url: str, *, http_get: Callable[..., Any], params: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            response = http_get(
                url,
                params=params or {},
                headers=headers or {},
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            status = int(getattr(response, "status_code", 200))
            if status >= 400:
                raise RuntimeError(f"HTTP_{status}")
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise TypeError("JSON_NOT_OBJECT")
            return dict(payload)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}:{exc}")
            if attempt < HTTP_ATTEMPTS:
                time.sleep(0.05)
    raise NBAPropHydrationError(
        "NBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "a required official NBA evidence source could not be retrieved",
        detail={"url": url, "attempts": HTTP_ATTEMPTS, "errors": errors[-4:]},
    )


def _result_rows(payload: Mapping[str, Any], preferred_name: str) -> list[dict[str, Any]]:
    result_sets = payload.get("resultSets")
    if not isinstance(result_sets, list):
        single = payload.get("resultSet")
        result_sets = [single] if isinstance(single, Mapping) else []
    target: Mapping[str, Any] | None = None
    for item in result_sets:
        if isinstance(item, Mapping) and str(item.get("name") or "").casefold() == preferred_name.casefold():
            target = item
            break
    if target is None and len(result_sets) == 1 and isinstance(result_sets[0], Mapping):
        target = result_sets[0]
    if target is None:
        raise NBAPropHydrationError("NBA_STATS_RESULT_SET_MISSING", preferred_name)
    headers = target.get("headers")
    rows = target.get("rowSet")
    if not isinstance(headers, list) or not isinstance(rows, list):
        raise NBAPropHydrationError("NBA_STATS_RESULT_SET_INVALID", preferred_name)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(headers):
            raise NBAPropHydrationError("NBA_STATS_ROW_SHAPE_INVALID", preferred_name)
        out.append(dict(zip(headers, row)))
    return out


def _schedule_candidates(event_start: datetime, *, http_get: Callable[..., Any]) -> list[dict[str, Any]]:
    payload = _request(NBA_SCHEDULE_URL, http_get=http_get, headers=_cdn_headers())
    league = payload.get("leagueSchedule")
    blocks = league.get("gameDates") if isinstance(league, Mapping) else None
    if not isinstance(blocks, list):
        raise NBAPropHydrationError("NBA_SCHEDULE_INVALID", "leagueSchedule.gameDates missing")
    candidates: list[tuple[float, dict[str, Any]]] = []
    for block in blocks:
        games = block.get("games") if isinstance(block, Mapping) else None
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, Mapping):
                continue
            raw_start = game.get("gameDateTimeUTC") or game.get("gameDateUTC")
            if not raw_start:
                continue
            try:
                scheduled = _aware(raw_start)
            except NBAPropHydrationError:
                continue
            delta = abs((scheduled - event_start).total_seconds())
            if delta <= MAX_EVENT_START_DELTA_SECONDS:
                candidates.append((delta, dict(game)))
    if not candidates:
        raise NBAPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "no official NBA schedule event matched the requested start time",
            detail={"event_start_time": event_start.isoformat()},
        )
    nearest = min(delta for delta, _ in candidates)
    return [game for delta, game in candidates if abs(delta - nearest) < 1.0]


def _team_node(game: Mapping[str, Any], side: str) -> dict[str, Any]:
    node = game.get(side)
    if not isinstance(node, Mapping):
        raise NBAPropHydrationError("NBA_SCHEDULE_TEAM_INVALID", side)
    return dict(node)


def _roster(team_id: str, season: str, *, http_get: Callable[..., Any]) -> list[dict[str, Any]]:
    payload = _request(
        f"{NBA_STATS_BASE}/commonteamroster",
        params={"LeagueID": "00", "Season": season, "TeamID": str(team_id)},
        headers=_stats_headers(),
        http_get=http_get,
    )
    return _result_rows(payload, "CommonTeamRoster")


def _resolve_game_player(candidates: list[dict[str, Any]], player: str, season: str, *, opponent: Optional[str], http_get: Callable[..., Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    matches: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for game in candidates:
        for side in ("homeTeam", "awayTeam"):
            team = _team_node(game, side)
            team_id = str(team.get("teamId") or "").strip()
            if not team_id:
                continue
            rows = _roster(team_id, season, http_get=http_get)
            exact = [row for row in rows if _name_key(row.get("PLAYER")) == _name_key(player)]
            if len(exact) != 1:
                continue
            other = _team_node(game, "awayTeam" if side == "homeTeam" else "homeTeam")
            if opponent:
                opp_keys = {
                    _name_key(other.get("teamTricode")),
                    _name_key(f"{other.get('teamCity', '')} {other.get('teamName', '')}"),
                    _name_key(other.get("teamName")),
                }
                if _name_key(opponent) not in opp_keys:
                    continue
            matches.append((game, team, other, exact[0]))
    if len(matches) != 1:
        raise NBAPropHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "official NBA schedule/roster data did not bind the player to exactly one target event",
            detail={"player": player, "matching_event_player_n": len(matches)},
        )
    return matches[0]


def _season_game_rows(player_id: str, player_name: str, season: str, *, http_get: Callable[..., Any]) -> list[dict[str, Any]]:
    payload = _request(
        f"{NBA_STATS_BASE}/leaguegamelog",
        params={
            "LeagueID": "00", "PlayerOrTeam": "P", "Season": season,
            "SeasonType": "Regular Season", "Counter": "0", "DateFrom": "", "DateTo": "",
            "Direction": "ASC", "Sorter": "DATE",
        },
        headers=_stats_headers(),
        http_get=http_get,
    )
    rows = _result_rows(payload, "LeagueGameLog")
    selected: list[dict[str, Any]] = []
    for row in rows:
        row_pid = str(row.get("PLAYER_ID") or row.get("PERSON_ID") or "").strip()
        if row_pid and row_pid != str(player_id):
            continue
        if not row_pid and _name_key(row.get("PLAYER_NAME") or row.get("PLAYER")) != _name_key(player_name):
            continue
        selected.append(dict(row))
    return selected


def _prior_game_log(player_id: str, player_name: str, stat: str, event_start: datetime, *, http_get: Callable[..., Any]) -> tuple[list[float], list[dict[str, Any]]]:
    components = COMPONENT_COLUMNS[stat]
    rows = _season_game_rows(player_id, player_name, _season_label(event_start), http_get=http_get)
    rows += _season_game_rows(player_id, player_name, _season_label(event_start, offset=-1), http_get=http_get)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        game_id = str(row.get("GAME_ID") or "").strip()
        raw_date = str(row.get("GAME_DATE") or "")[:10]
        try:
            game_date = datetime.fromisoformat(raw_date).date()
        except ValueError:
            continue
        if game_date >= event_start.date() or not game_id or game_id in seen:
            continue
        try:
            minutes = float(row.get("MIN"))
            values = {column: float(row.get(column)) for column in components}
        except (TypeError, ValueError):
            continue
        if not math.isfinite(minutes) or not 0 < minutes <= 70:
            continue
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            continue
        seen.add(game_id)
        selected.append({
            "date": raw_date,
            "game_id": game_id,
            "team": str(row.get("TEAM_ABBREVIATION") or ""),
            "matchup": str(row.get("MATCHUP") or ""),
            "minutes": minutes,
            "components": {key: float(value) for key, value in values.items()},
            "stat": float(sum(values.values())),
        })
    selected.sort(key=lambda row: (row["date"], row["game_id"]), reverse=True)
    recent = selected[:MIN_PRIOR_GAMES]
    if len(recent) < MIN_PRIOR_GAMES:
        raise NBAPropHydrationError(
            "NBA_RECENT_GAMES_INSUFFICIENT",
            "fewer than ten strictly-prior official NBA games were available",
            detail={"games_found": len(recent), "required": MIN_PRIOR_GAMES},
        )
    return [row["stat"] for row in recent], recent


def hydrate_nba_prop_evidence(
    *,
    player: str,
    stat_type: str,
    event_start_time: str,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
    opponent: Optional[str] = None,
) -> dict[str, Any]:
    stat = canonical_stat(stat_type)
    normalized_player = " ".join(str(player or "").strip().split())
    if not normalized_player:
        raise NBAPropHydrationError("PROP_PLAYER_IDENTITY_REQUIRED", "player is required")
    captured = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(event_start_time)
    if event_start <= captured:
        raise NBAPropHydrationError("EVENT_ALREADY_STARTED", "pregame evidence cannot be hydrated after event start")

    candidates = _schedule_candidates(event_start, http_get=http_get)
    season = _season_label(event_start)
    game, team, opp, roster = _resolve_game_player(
        candidates, normalized_player, season, opponent=opponent, http_get=http_get,
    )
    if int(game.get("gameStatus") or 0) != 1:
        raise NBAPropHydrationError(
            "EVENT_ALREADY_STARTED",
            "official NBA schedule no longer marks the target event as scheduled",
            detail={"game_status": game.get("gameStatus"), "game_status_text": game.get("gameStatusText")},
        )

    player_id = str(roster.get("PLAYER_ID") or "").strip()
    if not player_id:
        raise NBAPropHydrationError("PROP_PLAYER_IDENTITY_UNRESOLVED", "official NBA roster player ID missing")
    official_name = str(roster.get("PLAYER") or normalized_player)
    game_log, box_score_log = _prior_game_log(
        player_id, official_name, stat, event_start, http_get=http_get,
    )
    l10_minutes = [float(row["minutes"]) for row in box_score_log]
    timestamp = captured.isoformat()
    source_timestamps = {
        "NBA_CDN_SCHEDULE_CURRENT": timestamp,
        "NBA_STATS_COMMON_TEAM_ROSTER": timestamp,
        "NBA_STATS_LEAGUE_GAME_LOG": timestamp,
    }
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp

    opponent_name = " ".join(
        str(opp.get("teamCity") or "").split() + str(opp.get("teamName") or "").split()
    ).strip()
    team_name = " ".join(
        str(team.get("teamCity") or "").split() + str(team.get("teamName") or "").split()
    ).strip()
    return {
        "captured_at": timestamp,
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": {
            "status": "CURRENT_ROSTER_CONFIRMED_RESEARCH_ONLY",
            "role": str(roster.get("POSITION") or "NBA_ROTATION_PLAYER"),
            "confirmation_strength": "OFFICIAL_SCHEDULE_PLUS_CURRENT_TEAM_ROSTER",
            "player_id": player_id,
            "team_id": str(team.get("teamId") or ""),
            "team": team_name,
            "team_tricode": str(team.get("teamTricode") or "").strip().upper(),
            "opponent": opponent_name,
            "opponent_tricode": str(opp.get("teamTricode") or "").strip().upper(),
            "official_game_id": str(game.get("gameId") or ""),
            "official_game_start_utc": str(game.get("gameDateTimeUTC") or ""),
            "schedule_status": str(game.get("gameStatusText") or "Scheduled"),
            "availability": "UNVERIFIED_RESEARCH_ONLY",
            "production_availability_certified": False,
            "source": "NBA official CDN schedule + NBA Stats roster/game log",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY_RESEARCH_ONLY",
            "stat_type": stat,
            "stat_source_columns": list(COMPONENT_COLUMNS[stat]),
            "prior_games": len(box_score_log),
            "l10_minutes_mean": sum(l10_minutes) / len(l10_minutes),
            "l5_minutes_mean": sum(l10_minutes[:5]) / 5.0,
            "current_roster_confirmation": "PASS",
            "availability_gate": "NOT_CERTIFIED_FOR_PRODUCTION",
        },
        "source_timestamps": source_timestamps,
        "evidence_version": EVIDENCE_VERSION,
        "rate_provenance": "Official NBA LeagueGameLog strictly-prior exact-player rows; current event/team/player from official NBA schedule/roster",
        "hydration_provider": PROVIDER_ID,
        "research_only": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE", "COMPONENT_COLUMNS", "NBAPropHydrationError", "PROVIDER_ID",
    "canonical_stat", "hydrate_nba_prop_evidence",
]
