"""Fail-closed automatic evidence hydration for certified NFL player props.

Current identity is verified through ESPN's NFL athlete/team + scoreboard APIs.
Historical player-game outcomes come from nflverse weekly player statistics
(CC-BY-4.0) and are cached in-process for bounded live scoring latency.

This module acquires evidence only. It never computes, calibrates, ranks, prices,
or executes a probability/wager.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

import httpx

from v17.player_availability_gate import normalize_designation

PROVIDER_ID = "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1"
EVIDENCE_VERSION = "PROP_EVIDENCE_V1"
ESPN_SEARCH_URL = "https://site.api.espn.com/apis/search/v2"
ESPN_CORE_BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_TEAM_SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team}/schedule"
# The site/v2 athlete path returns HTTP 404 for NFL athletes (verified
# 2026-11-09 against athlete 4241457). The common/v3 profile endpoint serves the
# same official record, including the `injuries` entries this hydrator reads.
ESPN_ATHLETE_URL = "https://site.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{athlete_id}"
ESPN_ATHLETE_FALLBACK_URLS = (
    "https://site.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{athlete_id}",
    "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes/{athlete_id}",
)
AVAILABILITY_SOURCE = "ESPN_NFL_ATHLETE_INJURY_REPORT"
NFLVERSE_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
HTTP_TIMEOUT_SECONDS = 12.0
HTTP_ATTEMPTS = 2
MIN_PRIOR_GAMES = 10
MAX_EVENT_START_DELTA_SECONDS = 90 * 60
CACHE_TTL_SECONDS = 15 * 60

STAT_CONFIG: dict[str, tuple[str, str]] = {
    "PASSING_YARDS": ("passing_yards", "attempts"),
    "RUSHING_YARDS": ("rushing_yards", "carries"),
    "RECEIVING_YARDS": ("receiving_yards", "targets"),
    "ANYTIME_TD": ("anytime_td", "touch_opportunity"),
}
STAT_ALIASES = {
    "PASS_YARDS": "PASSING_YARDS",
    "PASSING_YARDS": "PASSING_YARDS",
    "RUSH_YARDS": "RUSHING_YARDS",
    "RUSHING_YARDS": "RUSHING_YARDS",
    "RECEIVING_YARDS": "RECEIVING_YARDS",
    "REC_YARDS": "RECEIVING_YARDS",
    "ANYTIME_TD": "ANYTIME_TD",
    "ANYTIME_TDS": "ANYTIME_TD",
    "ANYTIME_TOUCHDOWN": "ANYTIME_TD",
    "ANYTIME_TOUCHDOWNS": "ANYTIME_TD",
}

# ESPN and nflverse mostly share current abbreviations, but a few identities
# differ. This mapping is identity-only and never contributes to probability.
ESPN_TO_NFLVERSE_TEAM = {
    "LAR": "LA",
    "WSH": "WAS",
    "JAC": "JAX",
    "GNB": "GB",
    "KAN": "KC",
    "NWE": "NE",
    "NOR": "NO",
    "SFO": "SF",
    "TBB": "TB",
}

_CACHE_LOCK = threading.RLock()
_CSV_CACHE: dict[int, tuple[float, list[dict[str, str]], str]] = {}
_SCHEDULE_CACHE: dict[tuple[int, str, str], tuple[float, list[dict[str, Any]], str]] = {}


class NFLPropHydrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().casefold()
    return " ".join(text.split())


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NFLPropHydrationError("PROP_EVENT_START_INVALID", "event_start_time must be ISO 8601") from exc
    if parsed.utcoffset() is None:
        raise NFLPropHydrationError("PROP_EVENT_START_INVALID", "event_start_time requires timezone")
    return parsed.astimezone(timezone.utc)


def _request(
    url: str,
    *,
    http_get: Callable[..., Any],
    params: Optional[dict[str, Any]] = None,
    json_expected: bool = True,
) -> Any:
    errors: list[str] = []
    http_statuses: list[int] = []
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            response = http_get(
                url,
                params=params or {},
                headers={"User-Agent": "WOW-Research/1.0", "Accept": "application/json,text/csv,*/*"},
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            status = int(getattr(response, "status_code", 200))
            http_statuses.append(status)
            if status >= 400:
                raise RuntimeError(f"HTTP_{status}")
            if json_expected:
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise TypeError("JSON_NOT_OBJECT")
                return dict(payload)
            content = bytes(getattr(response, "content", b""))
            if not content:
                text = str(getattr(response, "text", ""))
                content = text.encode("utf-8")
            if not content:
                raise TypeError("EMPTY_BODY")
            return content
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}")
            if attempt < HTTP_ATTEMPTS:
                time.sleep(0.05)
    raise NFLPropHydrationError(
        "NFL_PROP_SOURCE_UNAVAILABLE",
        "required NFL evidence source could not be retrieved",
        detail={"url": url, "errors": errors[-4:], "http_statuses": http_statuses},
    )


def _resolve_espn_athlete(player: str, *, http_get: Callable[..., Any]) -> tuple[str, str]:
    payload = _request(
        ESPN_SEARCH_URL,
        params={"query": player, "limit": 10, "type": "player"},
        http_get=http_get,
    )
    matches: list[tuple[str, str]] = []
    for result in payload.get("results", []):
        if not isinstance(result, Mapping) or result.get("type") != "player":
            continue
        for item in result.get("contents", []):
            if not isinstance(item, Mapping):
                continue
            if _name_key(item.get("displayName")) != _name_key(player):
                continue
            desc = str(item.get("description") or "").upper()
            if "NFL" not in desc:
                continue
            uid = str(item.get("uid") or "")
            if "~a:" not in uid:
                continue
            matches.append((uid.split("~a:")[-1], str(item.get("displayName") or player)))
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        raise NFLPropHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "ESPN NFL athlete search did not resolve exactly one player",
            detail={"player": player, "match_n": len(unique)},
        )
    return unique[0]


def _athlete_team(athlete_id: str, *, http_get: Callable[..., Any]) -> str:
    payload = _request(f"{ESPN_CORE_BASE}/athletes/{athlete_id}", http_get=http_get)
    team = payload.get("team")
    if not isinstance(team, Mapping) or not team.get("$ref"):
        raise NFLPropHydrationError("PROP_PLAYER_TEAM_UNRESOLVED", "ESPN athlete team reference missing")
    team_payload = _request(str(team["$ref"]), http_get=http_get)
    abbreviation = str(team_payload.get("abbreviation") or "").upper().strip()
    if not abbreviation:
        raise NFLPropHydrationError("PROP_PLAYER_TEAM_UNRESOLVED", "ESPN athlete team abbreviation missing")
    return abbreviation


def _nflverse_team_code(value: Any) -> str:
    code = str(value or "").upper().strip()
    return ESPN_TO_NFLVERSE_TEAM.get(code, code)


def _canonical_event_metadata(event: Mapping[str, Any]) -> dict[str, Any]:
    competitions = event.get("competitions")
    competition = competitions[0] if isinstance(competitions, list) and competitions and isinstance(competitions[0], Mapping) else {}
    competitors = competition.get("competitors") if isinstance(competition, Mapping) else None
    if not isinstance(competitors, list):
        raise NFLPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "ESPN target NFL event lacked home/away competitors needed for canonical identity",
        )

    home_team = ""
    away_team = ""
    for competitor in competitors:
        if not isinstance(competitor, Mapping):
            continue
        team_payload = competitor.get("team")
        abbreviation = (
            str(team_payload.get("abbreviation") or "").upper().strip()
            if isinstance(team_payload, Mapping)
            else ""
        )
        side = str(competitor.get("homeAway") or "").strip().lower()
        if side == "home":
            home_team = abbreviation
        elif side == "away":
            away_team = abbreviation

    season_payload = event.get("season")
    if not isinstance(season_payload, Mapping):
        season_payload = competition.get("season") if isinstance(competition, Mapping) else None
    week_payload = event.get("week")
    if not isinstance(week_payload, Mapping):
        week_payload = competition.get("week") if isinstance(competition, Mapping) else None
    try:
        raw_season = (season_payload or {}).get("year")
        raw_week = (week_payload or {}).get("number")
        season = int(raw_season)
        week = int(raw_week)
        if any(isinstance(value, bool) or float(value) != number for value, number in ((raw_season, season), (raw_week, week))):
            raise ValueError("nonintegral season/week")
    except (TypeError, ValueError, OverflowError, AttributeError) as exc:
        raise NFLPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "ESPN target NFL event lacked season/week metadata needed for canonical identity",
        ) from exc
    if season <= 0 or week <= 0 or not home_team or not away_team:
        raise NFLPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "ESPN target NFL event canonical identity metadata was incomplete",
            detail={
                "season": season,
                "week": week,
                "home_team": home_team,
                "away_team": away_team,
            },
        )

    canonical_home = _nflverse_team_code(home_team)
    canonical_away = _nflverse_team_code(away_team)
    return {
        "provider_season": season,
        "provider_week": week,
        "provider_home_team": home_team,
        "provider_away_team": away_team,
        "canonical_home_team": canonical_home,
        "canonical_away_team": canonical_away,
        "verified_canonical_event_id": f"{season}_{week:02d}_{canonical_away}_{canonical_home}",
    }


def _target_event(
    *,
    event_start: datetime,
    team: str,
    opponent: Optional[str],
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    # ESPN groups late U.S. NFL games by the local league slate date. A Sunday
    # night kickoff can therefore be Monday in UTC, so using the UTC calendar
    # date alone can miss the exact game. Search a bounded adjacent-date window,
    # dedupe provider aliases by ESPN event id, and keep all existing time/team/
    # status validation fail-closed.
    queried_dates = [
        (event_start + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in (-1, 0, 1)
    ]
    events_by_id: dict[str, dict[str, Any]] = {}
    anonymous_events: list[dict[str, Any]] = []
    for date_key in queried_dates:
        payload = _request(
            ESPN_SCOREBOARD_URL,
            params={"dates": date_key, "limit": 100},
            http_get=http_get,
        )
        for raw_event in payload.get("events", []):
            if not isinstance(raw_event, Mapping):
                continue
            event = dict(raw_event)
            provider_id = str(event.get("id") or "").strip()
            if provider_id:
                events_by_id.setdefault(provider_id, event)
            else:
                anonymous_events.append(event)

    candidates: list[tuple[float, dict[str, Any], set[str]]] = []
    for event in [*events_by_id.values(), *anonymous_events]:
        try:
            scheduled = _aware(event.get("date"))
        except NFLPropHydrationError:
            continue
        delta = abs((scheduled - event_start).total_seconds())
        if delta > MAX_EVENT_START_DELTA_SECONDS:
            continue
        competitions = event.get("competitions")
        if not isinstance(competitions, list) or not competitions:
            continue
        competitors = competitions[0].get("competitors") if isinstance(competitions[0], Mapping) else None
        if not isinstance(competitors, list):
            continue
        teams = {
            str((c.get("team") or {}).get("abbreviation") or "").upper().strip()
            for c in competitors
            if isinstance(c, Mapping) and isinstance(c.get("team"), Mapping)
        }
        teams.discard("")
        if team not in teams:
            continue
        candidates.append((delta, dict(event), teams))
    if len(candidates) != 1:
        raise NFLPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "ESPN schedule did not resolve exactly one event for the player's current team/start time",
            detail={
                "team": team,
                "event_start": event_start.isoformat(),
                "match_n": len(candidates),
                "espn_dates_queried": queried_dates,
            },
        )
    _delta, event, teams = candidates[0]
    if opponent:
        # Full-name and abbreviation reconciliation is enforced by the shared V17
        # identity binder. Here the provider must still yield exactly one opponent.
        if len(teams - {team}) != 1:
            raise NFLPropHydrationError("PROP_EVENT_IDENTITY_CONFLICT", "NFL opponent identity was ambiguous")
    status_type = ((event.get("status") or {}).get("type") or {}) if isinstance(event.get("status"), Mapping) else {}
    if bool(status_type.get("completed")) or str(status_type.get("state") or "pre").lower() != "pre":
        raise NFLPropHydrationError("EVENT_ALREADY_STARTED", "ESPN target NFL event is not pregame")
    other = next(iter(teams - {team}))
    canonical_metadata = _canonical_event_metadata(event)
    if {canonical_metadata["provider_home_team"], canonical_metadata["provider_away_team"]} != teams:
        raise NFLPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "ESPN competitor orientation did not match the resolved NFL event teams",
            detail={"teams": sorted(teams), **canonical_metadata},
        )
    return {
        "event_id": str(event.get("id") or ""),
        "provider_event_start_time": _aware(event["date"]).isoformat(),
        "team": team,
        "opponent": other,
        **canonical_metadata,
    }


def _cached_nflverse_rows(season: int, *, http_get: Callable[..., Any], now_ts: float) -> tuple[list[dict[str, str]], str]:
    use_cache = http_get is httpx.get
    url = NFLVERSE_URL.format(season=season)

    if use_cache:
        # Single-flight the expensive live download + CSV parse.  The prior
        # implementation released the lock before I/O, so parallel interactive
        # rows could download and materialize the same season file multiple
        # times at once.  That amplified latency and memory inside the 512-MB
        # web process without adding any evidence or probability authority.
        with _CACHE_LOCK:
            cached = _CSV_CACHE.get(season)
            if cached and now_ts - cached[0] < CACHE_TTL_SECONDS:
                return cached[1], cached[2]

            content = _request(url, http_get=http_get, json_expected=False)
            digest = __import__("hashlib").sha256(content).hexdigest()
            text = content.decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
            if not rows:
                raise NFLPropHydrationError("NFL_PROP_HISTORY_EMPTY", f"nflverse season {season} was empty")
            _CSV_CACHE[season] = (now_ts, rows, digest)
            return rows, digest

    content = _request(url, http_get=http_get, json_expected=False)
    digest = __import__("hashlib").sha256(content).hexdigest()
    text = content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise NFLPropHydrationError("NFL_PROP_HISTORY_EMPTY", f"nflverse season {season} was empty")
    return rows, digest


def _num(row: Mapping[str, Any], key: str) -> float:
    try:
        value = float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return value if math.isfinite(value) else 0.0


def _history_unverified(message: str, **detail: Any) -> NFLPropHydrationError:
    return NFLPropHydrationError("NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED", message, detail=detail)


def _cached_team_schedule(
    season: int,
    team: str,
    season_type: str,
    *,
    http_get: Callable[..., Any],
    now_ts: float,
) -> tuple[list[dict[str, Any]], str]:
    """Use per-event ESPN metadata; the schedule's root season can be current."""
    key = (season, team, season_type)
    provider_type = {"REG": 2, "POST": 3}[season_type]
    url = ESPN_TEAM_SCHEDULE_URL.format(team=team)

    def load() -> tuple[list[dict[str, Any]], str]:
        payload = _request(url, params={"season": season, "seasontype": provider_type}, http_get=http_get)
        events = payload.get("events")
        if not isinstance(events, list) or any(not isinstance(event, Mapping) for event in events):
            raise _history_unverified("ESPN NFL team schedule lacked an event array", url=url, season=season)
        try:
            source_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise _history_unverified("ESPN NFL schedule payload could not be hashed", url=url) from exc
        # Hash the complete source, then retain only identity/chronology fields.
        # ESPN repeats large team-logo/link payloads for every scheduled game.
        compact = []
        for event in events:
            item = {k: event[k] for k in ("id", "date", "season", "seasonType", "week", "status") if k in event}
            competitions = event.get("competitions")
            if isinstance(competitions, list):
                compact_competitions = []
                for competition in competitions:
                    if not isinstance(competition, Mapping):
                        raise _history_unverified("ESPN NFL schedule competition was malformed", url=url)
                    entry = {k: competition[k] for k in ("season", "week", "status") if k in competition}
                    competitors = competition.get("competitors")
                    if isinstance(competitors, list):
                        entry["competitors"] = [
                            {"homeAway": c.get("homeAway"), "team": {"abbreviation": (c.get("team") or {}).get("abbreviation")}}
                            for c in competitors if isinstance(c, Mapping) and isinstance(c.get("team"), Mapping)
                        ]
                    compact_competitions.append(entry)
                item["competitions"] = compact_competitions
            compact.append(item)
        return compact, hashlib.sha256(source_bytes).hexdigest()

    if http_get is not httpx.get:
        return load()
    # Keep the live single-flight boundary and a bounded, short-lived cache.
    with _CACHE_LOCK:
        for cached_key, cached in list(_SCHEDULE_CACHE.items()):
            if now_ts - cached[0] >= CACHE_TTL_SECONDS:
                _SCHEDULE_CACHE.pop(cached_key, None)
        cached = _SCHEDULE_CACHE.get(key)
        if cached:
            return cached[1], cached[2]
        events, digest = load()
        if len(_SCHEDULE_CACHE) >= 128:
            _SCHEDULE_CACHE.pop(next(iter(_SCHEDULE_CACHE)))
        _SCHEDULE_CACHE[key] = (now_ts, events, digest)
        return events, digest


def _historical_game(
    row: Mapping[str, Any],
    *,
    season: int,
    schedules: dict[tuple[str, str], tuple[list[dict[str, Any]], str]],
    http_get: Callable[..., Any],
    now_ts: float,
) -> dict[str, Any]:
    """Bind a stats row to one official game, with no week-to-date inference.

    nflverse postseason weeks and ESPN round numbers are separate namespaces.
    A postseason team pair must resolve to exactly one official event in that
    season/type; both week values are retained instead of guessing an offset.
    """
    week_number = _num(row, "week")
    row_season = _num(row, "season")
    season_type = str(row.get("season_type") or "").strip().upper()
    team = _nflverse_team_code(row.get("team"))
    opponent = _nflverse_team_code(row.get("opponent_team"))
    if (row_season != season or week_number <= 0 or not week_number.is_integer()
            or season_type not in {"REG", "POST"} or not team or not opponent or team == opponent):
        raise _history_unverified("nflverse history lacked exact season/type/week/team identity", season=season)
    week = int(week_number)
    # ESPN accepts the current provider abbreviation in its team schedule path.
    provider_team = {"LA": "LAR", "WAS": "WSH"}.get(team, team)
    key = (provider_team, season_type)
    if key not in schedules:
        schedules[key] = _cached_team_schedule(
            season, provider_team, season_type, http_get=http_get, now_ts=now_ts,
        )
    events, digest = schedules[key]
    candidates: dict[str, dict[str, Any]] = {}
    official_aliases: dict[str, dict[str, Any]] = {}
    for event in events:
        try:
            identity = _canonical_event_metadata(event)
        except NFLPropHydrationError as exc:
            raise _history_unverified("ESPN historical event identity was incomplete", season=season) from exc
        season_payload = event.get("season") or {}
        type_payload = event.get("seasonType") or {}
        if not isinstance(season_payload, Mapping) or not isinstance(type_payload, Mapping):
            raise _history_unverified("ESPN historical event season type was malformed", season=season)
        raw_type = season_payload.get("type", type_payload.get("type"))
        try:
            provider_type = int(raw_type)
            if isinstance(raw_type, bool) or float(raw_type) != provider_type:
                raise ValueError("nonintegral season type")
        except (TypeError, ValueError, OverflowError) as exc:
            raise _history_unverified("ESPN historical event season type was not an exact integer", season=season) from exc
        if "type" in type_payload and "type" in season_payload and type_payload["type"] != season_payload["type"]:
            raise _history_unverified("ESPN historical event season types conflicted", season=season)
        provider_id = str(event.get("id") or "").strip()
        if not provider_id:
            raise _history_unverified("ESPN historical event id was missing", season=season, week=week)
        try:
            kickoff = _aware(event.get("date"))
        except NFLPropHydrationError as exc:
            raise _history_unverified("ESPN historical game date was not verifiable", event_id=provider_id) from exc
        competitions = event["competitions"]
        status_payloads = [event["status"]] if "status" in event else []
        status_payloads.extend(c["status"] for c in competitions if "status" in c)
        status_types = []
        for status in status_payloads:
            status_type = status.get("type") if isinstance(status, Mapping) else None
            if (not isinstance(status_type, Mapping) or not isinstance(status_type.get("completed"), bool)
                    or status_type.get("state") not in {"pre", "in", "post"}):
                raise _history_unverified("ESPN historical game completion status was not verifiable", event_id=provider_id)
            status_types.append((status_type["state"], status_type["completed"]))
        if not status_types:
            raise _history_unverified("ESPN historical game completion status was not verifiable", event_id=provider_id)
        if len(set(status_types)) != 1:
            raise _history_unverified("ESPN historical game completion statuses conflicted", event_id=provider_id)
        status_type = {"state": status_types[0][0], "completed": status_types[0][1]}
        # Validate aliases across the complete requested source before applying
        # stats-row filters. A changed week/team/type cannot hide a conflicting
        # second identity for the same provider event id.
        alias = {
            **identity,
            "provider_season_type": provider_type,
            "date": kickoff.isoformat(),
            "state": status_type["state"],
            "completed": status_type["completed"],
        }
        if provider_id in official_aliases and official_aliases[provider_id] != alias:
            raise _history_unverified("ESPN historical aliases conflicted", event_id=provider_id)
        official_aliases[provider_id] = alias
        if identity["provider_season"] != season or provider_type != {"REG": 2, "POST": 3}[season_type]:
            continue
        if {identity["canonical_home_team"], identity["canonical_away_team"]} != {team, opponent}:
            continue
        if season_type == "REG" and identity["provider_week"] != week:
            continue
        canonical_game_id = f"{season}_{week:02d}_{identity['canonical_away_team']}_{identity['canonical_home_team']}"
        source_game_id = str(row.get("game_id") or "").strip()
        if source_game_id and source_game_id != canonical_game_id:
            raise _history_unverified("nflverse history game id conflicted with official teams/week", game_id=source_game_id)
        candidate = {
            "date": kickoff.isoformat(),
            "season": season,
            "season_type": season_type,
            "week": week,
            "game_id": source_game_id or canonical_game_id,
            "provider_event_id": provider_id,
            "provider_season": identity["provider_season"],
            "provider_season_type": provider_type,
            "provider_week": identity["provider_week"],
            "completed": status_type["completed"] is True and status_type["state"] == "post",
            "schedule_source_url": ESPN_TEAM_SCHEDULE_URL.format(team=provider_team),
            "schedule_source_sha256": digest,
        }
        if provider_id in candidates and candidates[provider_id] != candidate:
            raise _history_unverified("ESPN historical aliases conflicted", event_id=provider_id)
        candidates[provider_id] = candidate
    if len(candidates) != 1:
        raise _history_unverified(
            "nflverse history did not resolve to exactly one official game",
            season=season, season_type=season_type, week=week, team=team, opponent=opponent, match_n=len(candidates),
        )
    return next(iter(candidates.values()))


def _history(
    *,
    player: str,
    canonical_stat: str,
    event_start: datetime,
    http_get: Callable[..., Any],
    now_ts: float,
    provider_season: Optional[int] = None,
) -> tuple[list[float], list[dict[str, Any]], dict[str, str], str]:
    if not isinstance(provider_season, int) or isinstance(provider_season, bool) or provider_season < 2020:
        raise _history_unverified("verified target provider season is required for NFL history")
    target_col, opp_col = STAT_CONFIG[canonical_stat]
    collected: list[dict[str, Any]] = []
    seen_games: set[str] = set()
    hashes: dict[str, str] = {}
    # NFL's January/February calendar dates still belong to the official season
    # obtained from the target event. Never try a guessed calendar-year file.
    for season in range(provider_season, max(provider_season - 4, 2019), -1):
        try:
            rows, digest = _cached_nflverse_rows(season, http_get=http_get, now_ts=now_ts)
        except NFLPropHydrationError as exc:
            statuses = exc.detail.get("http_statuses") or []
            if (exc.code == "NFL_PROP_SOURCE_UNAVAILABLE" and statuses
                    and len(statuses) == len(exc.detail.get("errors") or []) and all(s == 404 for s in statuses)):
                # Only the verified current provider season can be unpublished.
                # Never silently cross a missing prior season, even if no older
                # CSV has yet loaded; the fit window must be contiguous.
                if season != provider_season:
                    raise _history_unverified(
                        "nflverse history had a missing prior season",
                        missing_seasons=[season],
                    ) from exc
                continue
            raise
        hashes[str(season)] = digest
        schedules: dict[tuple[str, str], tuple[list[dict[str, Any]], str]] = {}
        for row in rows:
            if _name_key(row.get("player_display_name") or row.get("player_name")) != _name_key(player):
                continue
            carries = _num(row, "carries")
            targets = _num(row, "targets")
            if canonical_stat == "ANYTIME_TD":
                value = 1.0 if (_num(row, "rushing_tds") + _num(row, "receiving_tds") + _num(row, "special_teams_tds")) > 0 else 0.0
                opportunity = carries + targets
            else:
                value = _num(row, target_col)
                opportunity = _num(row, opp_col)
            if opportunity <= 0:
                continue
            game = _historical_game(row, season=season, schedules=schedules, http_get=http_get, now_ts=now_ts)
            if not game["completed"] or _aware(game["date"]) >= event_start:
                continue
            if game["provider_event_id"] in seen_games:
                raise _history_unverified("duplicate nflverse player-game history", event_id=game["provider_event_id"])
            seen_games.add(game["provider_event_id"])
            collected.append({
                **game,
                "stats_source_sha256": digest,
                "team": str(row.get("team") or "").upper(),
                "opponent": str(row.get("opponent_team") or "").upper(),
                "position": str(row.get("position") or "").upper(),
                "value": value,
                "opportunity": opportunity,
            })
        for (team, season_type), (_events, schedule_digest) in schedules.items():
            hashes[f"ESPN_NFL_TEAM_SCHEDULE:{season}:{team}:{season_type}"] = schedule_digest
        if len(collected) >= MIN_PRIOR_GAMES:
            break
    collected.sort(key=lambda r: (r["date"], r["provider_event_id"]))
    recent = collected[-MIN_PRIOR_GAMES:]
    if len(recent) < MIN_PRIOR_GAMES:
        raise NFLPropHydrationError(
            "NFL_RECENT_GAMES_INSUFFICIENT",
            "fewer than ten prior relevant NFL games were available from nflverse",
            detail={"games_found": len(recent), "required": MIN_PRIOR_GAMES},
        )
    game_log = [float(r["value"]) for r in recent]
    box = [
        {
            "date": r["date"],
            "season": r["season"],
            "season_type": r["season_type"],
            "week": r["week"],
            "game_id": r["game_id"],
            "provider_event_id": r["provider_event_id"],
            "provider_season": r["provider_season"],
            "provider_season_type": r["provider_season_type"],
            "provider_week": r["provider_week"],
            "completed": r["completed"],
            "stats_source_sha256": r["stats_source_sha256"],
            "schedule_source_url": r["schedule_source_url"],
            "schedule_source_sha256": r["schedule_source_sha256"],
            "team": r["team"],
            "opponent": r["opponent"],
            "position": r["position"],
            "opportunity": float(r["opportunity"]),
        }
        for r in recent
    ]
    player_id = "NFLVERSE_NAME:" + _name_key(player).replace(" ", "_")
    return game_log, box, hashes, player_id


def _injury_entries(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries: list[Mapping[str, Any]] = []
    for container in (payload, payload.get("athlete")):
        if not isinstance(container, Mapping):
            continue
        raw = container.get("injuries")
        if isinstance(raw, list):
            entries.extend(item for item in raw if isinstance(item, Mapping))
    return entries


def _athlete_availability(
    athlete_id: str,
    *,
    http_get: Callable[..., Any],
    captured: datetime,
) -> dict[str, Any]:
    """Read the official injury designation for the target athlete.

    Roster identity is already established by ``_athlete_team``; participation
    is a separate fact. A failed retrieval is reported as ``UNVERIFIED`` with
    its own reason so a downstream gate can distinguish "no designation on the
    report" from "the report was never read".
    """
    base: dict[str, Any] = {
        "source": AVAILABILITY_SOURCE,
        "captured_at": captured.isoformat(),
        "evidence_status": "RETRIEVED",
        # A fetch timestamp is not an independently published injury-report
        # timestamp. The athlete-profile endpoint does not certify a complete
        # official report, even when its injuries list is empty.
        "fresh": False,
        "report_complete": False,
        "athlete_id": athlete_id,
    }
    payload: Mapping[str, Any] | None = None
    endpoint_used: str | None = None
    failures: list[str] = []
    for url_template in ESPN_ATHLETE_FALLBACK_URLS:
        url = url_template.format(athlete_id=athlete_id)
        try:
            payload = _request(url, http_get=http_get)
        except Exception as exc:
            failures.append(f"{type(exc).__name__}:{url}")
            continue
        endpoint_used = url
        break
    if payload is None:
        return {
            **base,
            "status": "UNVERIFIED",
            "designation": None,
            "evidence_status": "RETRIEVAL_FAILED",
            "source_endpoint": None,
            "reason": f"INJURY_REPORT_RETRIEVAL_FAILED:{type(failures[-1]).__name__ if failures else 'UNKNOWN'}",
            "retrieval_failures": failures[-3:],
        }
    base["source_endpoint"] = endpoint_used
    athlete = payload.get("athlete") if isinstance(payload.get("athlete"), Mapping) else payload
    provider_id = str(athlete.get("id") or "").strip() if isinstance(athlete, Mapping) else ""
    if provider_id != str(athlete_id):
        return {
            **base,
            "status": "UNVERIFIED",
            "designation": None,
            "evidence_status": "ATHLETE_IDENTITY_UNVERIFIED",
            "reason": "INJURY_SOURCE_ATHLETE_IDENTITY_MISMATCH",
        }
    entries = _injury_entries(payload)
    if not entries:
        # Missing/empty athlete-profile injuries are NOT proof that the current
        # complete team injury report was retrieved. Avoid false AVAILABLE
        # even after valid event/team identity binding.
        return {
            **base,
            "status": "UNVERIFIED",
            "designation": None,
            "evidence_status": "OFFICIAL_REPORT_COMPLETENESS_UNVERIFIED",
            "reason": "COMPLETE_PUBLISHED_INJURY_REPORT_NOT_VERIFIED",
        }
    latest = max(entries, key=lambda entry: str(entry.get("date") or ""))
    designation = normalize_designation(latest.get("status")) or normalize_designation(latest.get("type"))
    if not designation:
        return {
            **base,
            "status": "UNVERIFIED",
            "designation": None,
            "reason": "INJURY_DESIGNATION_UNPARSED",
        }
    return {
        **base,
        "status": designation,
        "designation": designation,
        "reason": f"OFFICIAL_DESIGNATION_{designation}",
    }


def bind_availability_to_event(
    availability: Mapping[str, Any],
    *,
    team: str,
    event_id: str,
) -> dict[str, Any]:
    """Attach the resolved event/team identity to availability evidence.

    The athlete injury record is read without knowing which game it will be
    used for. Binding happens after the target event resolves so the gate sees
    one athlete, one event, and one team, exactly as the provenance contract
    requires for clearance.
    """
    bound = dict(availability)
    bound["team"] = str(team or "").strip() or None
    bound["event_id"] = str(event_id or "").strip() or None
    return bound


def hydrate_nfl_prop_evidence(
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
    event_start = _aware(event_start_time)
    captured = _aware(source_capture_timestamp) if source_capture_timestamp else (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if captured >= event_start:
        raise NFLPropHydrationError("EVENT_ALREADY_STARTED", "NFL prop hydration requires a pregame capture")
    canonical = STAT_ALIASES.get(str(stat_type or "").strip().upper(), str(stat_type or "").strip().upper())
    if canonical not in STAT_CONFIG:
        raise NFLPropHydrationError(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE", f"unsupported NFL prop stat: {canonical}"
        )

    athlete_id, official_name = _resolve_espn_athlete(player, http_get=http_get)
    availability = _athlete_availability(athlete_id, http_get=http_get, captured=captured)
    team = _athlete_team(athlete_id, http_get=http_get)
    target = _target_event(event_start=event_start, team=team, opponent=opponent, http_get=http_get)
    # Availability must name the event and team it was captured for, otherwise
    # the gate cannot distinguish this game's report from a stale one.
    availability = bind_availability_to_event(availability, team=team, event_id=target["event_id"])
    official_start = _aware(target["provider_event_start_time"])
    if captured >= official_start:
        raise NFLPropHydrationError("EVENT_ALREADY_STARTED", "NFL prop capture must precede the official kickoff")
    game_log, box_score_log, source_hashes, nflverse_player_id = _history(
        player=official_name,
        canonical_stat=canonical,
        event_start=official_start,
        provider_season=target["provider_season"],
        http_get=http_get,
        now_ts=captured.timestamp(),
    )
    # Latest historical row must agree with current ESPN team unless the player
    # changed teams after his most recent game; ESPN identity is authoritative
    # for the target event, so this mismatch is recorded rather than used to
    # rewrite the current team.
    recent_opp_mean = sum(float(r["opportunity"]) for r in box_score_log) / len(box_score_log)
    return {
        "captured_at": captured.isoformat(),
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": {
            "status": "ACTIVE_CURRENT_ESPN_ROSTER",
            "availability": availability,
            "player": official_name,
            "espn_athlete_id": athlete_id,
            "nflverse_player_id": nflverse_player_id,
            "team": team,
            "opponent": target["opponent"],
            "event_id": target["event_id"],
            "provider_season": target["provider_season"],
            "provider_event_start_time": target["provider_event_start_time"],
            "provider_week": target["provider_week"],
            "provider_home_team": target["provider_home_team"],
            "provider_away_team": target["provider_away_team"],
            "canonical_home_team": target["canonical_home_team"],
            "canonical_away_team": target["canonical_away_team"],
            "verified_canonical_event_id": target["verified_canonical_event_id"],
        },
        "role_timestamp": captured.isoformat(),
        "opportunity_ledger": {
            "status": "PASS",
            "stat_type": canonical,
            "prior_game_n": len(game_log),
            "l10_opportunity_mean": recent_opp_mean,
            "source": "NFLVERSE_WEEKLY_PLAYER_STATS",
        },
        "source_timestamps": {
            AVAILABILITY_SOURCE: captured.isoformat(),
            "ESPN_NFL_IDENTITY_SCOREBOARD": captured.isoformat(),
            "NFLVERSE_WEEKLY_PLAYER_STATS": captured.isoformat(),
            "ESPN_NFL_TEAM_SCHEDULE": captured.isoformat(),
        },
        "evidence_version": EVIDENCE_VERSION,
        "rate_provenance": (
            f"{PROVIDER_ID};source_label={source_label};nflverse_sha256="
            + ",".join(f"{season}:{digest}" for season, digest in sorted(source_hashes.items()) if season.isdigit())
            + ";espn_schedule_canonical_json_sha256="
            + ",".join(f"{key.removeprefix('ESPN_NFL_TEAM_SCHEDULE:')}:{digest}"
                       for key, digest in sorted(source_hashes.items()) if key.startswith("ESPN_NFL_TEAM_SCHEDULE:"))
        ),
        "opponent_context": None,
        "hydration_provider": PROVIDER_ID,
    }


__all__ = [
    "ESPN_TO_NFLVERSE_TEAM",
    "PROVIDER_ID",
    "STAT_CONFIG",
    "STAT_ALIASES",
    "NFLPropHydrationError",
    "hydrate_nfl_prop_evidence",
]
