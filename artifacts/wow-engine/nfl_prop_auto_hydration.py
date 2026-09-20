"""Automatic pregame evidence hydration for certified NFL direct props.

Only approved nflverse SPORTING datasets are consumed.  Every history row is a
completed player-game strictly before the target event.  Caller event identity
is validated against the player's resolved current team; mismatches fail closed.
"""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from nflverse_historical_adapter import parse_nflverse_kickoff, require_allowed_dataset

PLAYER_STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{season}.csv"
SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
SUPPORTED = {"PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"}
MIN_HISTORY = 10
NFL_EASTERN = ZoneInfo("America/New_York")

TEAM_ALIASES = {
    "ARIZONA CARDINALS":"ARI","ATLANTA FALCONS":"ATL","BALTIMORE RAVENS":"BAL","BUFFALO BILLS":"BUF",
    "CAROLINA PANTHERS":"CAR","CHICAGO BEARS":"CHI","CINCINNATI BENGALS":"CIN","CLEVELAND BROWNS":"CLE",
    "DALLAS COWBOYS":"DAL","DENVER BRONCOS":"DEN","DETROIT LIONS":"DET","GREEN BAY PACKERS":"GB",
    "HOUSTON TEXANS":"HOU","INDIANAPOLIS COLTS":"IND","JACKSONVILLE JAGUARS":"JAX","KANSAS CITY CHIEFS":"KC",
    "LAS VEGAS RAIDERS":"LV","LOS ANGELES CHARGERS":"LAC","LOS ANGELES RAMS":"LAR","MIAMI DOLPHINS":"MIA",
    "MINNESOTA VIKINGS":"MIN","NEW ENGLAND PATRIOTS":"NE","NEW ORLEANS SAINTS":"NO","NEW YORK GIANTS":"NYG",
    "NEW YORK JETS":"NYJ","PHILADELPHIA EAGLES":"PHI","PITTSBURGH STEELERS":"PIT","SAN FRANCISCO 49ERS":"SF",
    "SEATTLE SEAHAWKS":"SEA","TAMPA BAY BUCCANEERS":"TB","TENNESSEE TITANS":"TEN","WASHINGTON COMMANDERS":"WAS",
}


class NFLPropHydrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NFLPropHydrationError("PROP_EVENT_START_INVALID", "event_start_time must be ISO-8601") from exc
    if parsed.utcoffset() is None:
        raise NFLPropHydrationError("PROP_EVENT_START_INVALID", "event_start_time must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _norm_name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace(".", "").split())


def _team(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return TEAM_ALIASES.get(raw, raw)


def _float(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NFLPropHydrationError("NFLVERSE_STAT_INVALID", f"{key}={value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise NFLPropHydrationError("NFLVERSE_STAT_INVALID", f"{key}={value!r}")
    return number


def _fetch_csv(url: str, http_get: Callable[..., Any]) -> list[dict[str, str]]:
    try:
        response = http_get(url, timeout=12.0, headers={"User-Agent": "WOW-V17-NFL-Props/1.0"})
        response.raise_for_status()
        text = getattr(response, "text", None)
        if text is None:
            text = response.content.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))
    except NFLPropHydrationError:
        raise
    except Exception as exc:
        raise NFLPropHydrationError(
            "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
            "nflverse evidence source could not be retrieved",
            detail={"source": url, "error_type": type(exc).__name__},
        ) from exc


def _player_rows(player: str, seasons: list[int], http_get: Callable[..., Any]) -> tuple[str, list[dict[str, str]]]:
    require_allowed_dataset("player_stats")
    target = _norm_name(player)
    matches: dict[str, list[dict[str, str]]] = {}
    for season in seasons:
        rows = _fetch_csv(PLAYER_STATS_URL.format(season=season), http_get)
        for row in rows:
            names = [row.get("player_display_name"), row.get("player_name"), row.get("player_name_display")]
            if target not in {_norm_name(name) for name in names if name}:
                continue
            pid = str(row.get("player_id") or row.get("gsis_id") or "").strip()
            if not pid:
                continue
            matches.setdefault(pid, []).append(row)
    if not matches:
        raise NFLPropHydrationError("PROP_PLAYER_IDENTITY_UNRESOLVED", "nflverse player identity not found", detail={"player": player})
    ranked = sorted(matches.items(), key=lambda item: max((int(float(r.get("season") or 0)), int(float(r.get("week") or 0))) for r in item[1]), reverse=True)
    if len(ranked) > 1 and max((int(float(r.get("season") or 0)), int(float(r.get("week") or 0))) for r in ranked[0][1]) == max((int(float(r.get("season") or 0)), int(float(r.get("week") or 0))) for r in ranked[1][1]):
        raise NFLPropHydrationError("PROP_PLAYER_IDENTITY_AMBIGUOUS", "multiple nflverse player IDs match the supplied name")
    return ranked[0][0], ranked[0][1]


def _resolve_event(event_start: datetime, current_team: str, opponent: str | None, http_get: Callable[..., Any]) -> dict[str, Any]:
    require_allowed_dataset("schedules")
    rows = _fetch_csv(SCHEDULE_URL, http_get)
    opp = _team(opponent) if opponent else ""
    candidates: list[tuple[float, dict[str, str], datetime]] = []
    for row in rows:
        home, away = _team(row.get("home_team")), _team(row.get("away_team"))
        if current_team not in {home, away}:
            continue
        if opp and opp not in {home, away}:
            continue
        try:
            kickoff = parse_nflverse_kickoff(row)
        except Exception:
            continue
        delta = abs((kickoff - event_start).total_seconds())
        if delta <= 3 * 60 * 60:
            candidates.append((delta, row, kickoff))
    if not candidates:
        raise NFLPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "verified player team is not present in the requested NFL event",
            detail={"verified_team": current_team, "requested_opponent": opp or None, "event_start_time": event_start.isoformat()},
        )
    candidates.sort(key=lambda x: x[0])
    _, row, kickoff = candidates[0]
    return {
        "game_id": str(row.get("game_id") or ""), "season": int(float(row.get("season") or 0)),
        "week": int(float(row.get("week") or 0)), "home_team": _team(row.get("home_team")),
        "away_team": _team(row.get("away_team")), "kickoff": kickoff,
    }


def _before_target(row: dict[str, str], season: int, week: int) -> bool:
    try:
        s, w = int(float(row.get("season") or 0)), int(float(row.get("week") or 0))
    except (TypeError, ValueError):
        return False
    season_type = str(row.get("season_type") or "REG").upper()
    return season_type in {"REG", "REGULAR"} and (s < season or (s == season and w < week))


def _route_value(row: dict[str, str], route: str) -> float:
    if route == "PASSING_YARDS": return _float(row, "passing_yards")
    if route == "RUSHING_YARDS": return _float(row, "rushing_yards")
    if route == "RECEIVING_YARDS": return _float(row, "receiving_yards")
    if route == "ANYTIME_TD":
        if "special_teams_tds" not in row:
            raise NFLPropHydrationError("NFLVERSE_ANYTIME_TD_SPECIAL_TEAMS_COLUMN_MISSING", "cannot certify Anytime TD without special-teams TD coverage")
        return 1.0 if (_float(row, "rushing_tds") + _float(row, "receiving_tds") + _float(row, "special_teams_tds")) > 0 else 0.0
    raise NFLPropHydrationError("PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE", route)


def hydrate_nfl_direct_prop_evidence(
    *, player: str, stat_type: str, event_start_time: str, opponent: str | None = None,
    http_get: Callable[..., Any] = httpx.get, now: datetime | None = None,
    source_capture_timestamp: str | None = None, source_label: str = "NORMALIZED_PICK_REQUEST",
) -> dict[str, Any]:
    route = str(stat_type or "").strip().upper()
    if route not in SUPPORTED:
        raise NFLPropHydrationError("PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE", "NFL direct-prop route is unsupported", detail={"stat_type": route})
    event_start = _aware(event_start_time)
    captured = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if event_start <= captured:
        raise NFLPropHydrationError("EVENT_ALREADY_STARTED", "pregame hydration requested after event start")
    seasons = list(range(event_start.year, event_start.year - 3, -1))
    pid, rows = _player_rows(player, seasons, http_get)
    rows.sort(key=lambda r: (int(float(r.get("season") or 0)), int(float(r.get("week") or 0))))
    prior_candidates = [r for r in rows if int(float(r.get("season") or 0)) <= event_start.year]
    if not prior_candidates:
        raise NFLPropHydrationError("NFL_PROP_HISTORY_INSUFFICIENT", "no prior NFL player rows")
    latest = prior_candidates[-1]
    current_team = _team(latest.get("recent_team") or latest.get("team"))
    if not current_team:
        raise NFLPropHydrationError("PROP_PLAYER_TEAM_UNRESOLVED", "nflverse row has no current team")
    event = _resolve_event(event_start, current_team, opponent, http_get)
    prior = [r for r in rows if _before_target(r, event["season"], event["week"])][-MIN_HISTORY:]
    if len(prior) < MIN_HISTORY:
        raise NFLPropHydrationError("NFL_PROP_HISTORY_INSUFFICIENT", "fewer than ten completed prior games", detail={"found": len(prior), "required": MIN_HISTORY})
    game_log = [_route_value(row, route) for row in prior]
    box = []
    for row, value in zip(prior, game_log):
        box.append({
            "season": int(float(row.get("season") or 0)), "week": int(float(row.get("week") or 0)),
            "team": _team(row.get("recent_team") or row.get("team")), "opponent": _team(row.get("opponent_team")),
            "position": str(row.get("position") or "UNK").upper(), "attempts": _float(row, "attempts"),
            "carries": _float(row, "carries"), "targets": _float(row, "targets"),
            "rushing_tds": _float(row, "rushing_tds"), "receiving_tds": _float(row, "receiving_tds"),
            "special_teams_tds": _float(row, "special_teams_tds"), "stat_value": value,
        })
    opp_key = {"PASSING_YARDS":"attempts","RUSHING_YARDS":"carries","RECEIVING_YARDS":"targets"}.get(route)
    if route == "ANYTIME_TD":
        opp_values = [b["carries"] + b["targets"] for b in box]
        min_opp = 1.0
    else:
        opp_values = [float(b[opp_key]) for b in box]
        min_opp = 5.0 if route == "PASSING_YARDS" else 1.0
    if sum(opp_values) / len(opp_values) < min_opp:
        raise NFLPropHydrationError("NFL_PROP_PRIOR_OPPORTUNITY_BELOW_TRAINING_SUPPORT", "prior opportunity is outside fitted-model support")
    timestamp = captured.isoformat()
    source_timestamps = {"NFLVERSE_PLAYER_STATS": timestamp, "NFLVERSE_SCHEDULES": timestamp}
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp
    return {
        "captured_at": timestamp, "game_log": game_log, "box_score_log": box,
        "role_status": {
            "status": "ACTIVE_PRIOR_GAME_ROLE", "role": str(latest.get("position") or "UNK").upper(),
            "team": current_team, "opponent": _team(opponent) if opponent else None,
            "player_id": pid, "nflverse_game_id": event["game_id"],
            "target_home_team": event["home_team"], "target_away_team": event["away_team"],
            "source": "nflverse player_stats + schedules",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY", "route": route, "prior_games": len(prior),
            "mean_opportunity": sum(opp_values) / len(opp_values), "box_score_alignment": "1:1",
            "history_selection": "STRICTLY_PRIOR_COMPLETED_NFLVERSE_PLAYER_GAMES_NO_IMPUTATION",
        },
        "source_timestamps": source_timestamps, "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "nflverse approved player_stats/schedules; strictly-prior L10; no market probability; no target-game outcome leakage",
    }
