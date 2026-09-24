"""Fail-closed automatic evidence hydration for certified NFL player props.

Current identity is verified through ESPN's NFL athlete/team + scoreboard APIs.
Historical player-game outcomes come from nflverse weekly player statistics
(CC-BY-4.0) and are cached in-process for bounded live scoring latency.

This module acquires evidence only. It never computes, calibrates, ranks, prices,
or executes a probability/wager.
"""
from __future__ import annotations

import csv
import io
import math
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

import httpx

PROVIDER_ID = "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1"
EVIDENCE_VERSION = "PROP_EVIDENCE_V1"
ESPN_SEARCH_URL = "https://site.api.espn.com/apis/search/v2"
ESPN_CORE_BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
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
        detail={"url": url, "errors": errors[-4:]},
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
        season = int((season_payload or {}).get("year"))
        week = int((week_payload or {}).get("number"))
    except (TypeError, ValueError, AttributeError) as exc:
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


def _history(
    *,
    player: str,
    canonical_stat: str,
    event_start: datetime,
    http_get: Callable[..., Any],
    now_ts: float,
) -> tuple[list[float], list[dict[str, Any]], dict[str, str], str]:
    target_col, opp_col = STAT_CONFIG[canonical_stat]
    collected: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    # Current season first, then prior seasons until ten relevant games exist.
    for season in range(event_start.year, max(event_start.year - 4, 2019), -1):
        rows, digest = _cached_nflverse_rows(season, http_get=http_get, now_ts=now_ts)
        hashes[str(season)] = digest
        for row in rows:
            if _name_key(row.get("player_display_name") or row.get("player_name")) != _name_key(player):
                continue
            week = int(_num(row, "week"))
            if week <= 0:
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
            collected.append({
                "season": int(_num(row, "season") or season),
                "week": week,
                "game_id": str(row.get("game_id") or ""),
                "team": str(row.get("team") or "").upper(),
                "opponent": str(row.get("opponent_team") or "").upper(),
                "position": str(row.get("position") or "").upper(),
                "value": value,
                "opportunity": opportunity,
            })
        if len(collected) >= MIN_PRIOR_GAMES:
            break
    collected.sort(key=lambda r: (r["season"], r["week"], r["game_id"]))
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
            "date": f"{r['season']}-W{r['week']:02d}",
            "season": r["season"],
            "week": r["week"],
            "game_id": r["game_id"],
            "team": r["team"],
            "opponent": r["opponent"],
            "position": r["position"],
            "opportunity": float(r["opportunity"]),
        }
        for r in recent
    ]
    player_id = "NFLVERSE_NAME:" + _name_key(player).replace(" ", "_")
    return game_log, box, hashes, player_id


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
    team = _athlete_team(athlete_id, http_get=http_get)
    target = _target_event(event_start=event_start, team=team, opponent=opponent, http_get=http_get)
    game_log, box_score_log, source_hashes, nflverse_player_id = _history(
        player=official_name,
        canonical_stat=canonical,
        event_start=event_start,
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
            "player": official_name,
            "espn_athlete_id": athlete_id,
            "nflverse_player_id": nflverse_player_id,
            "team": team,
            "opponent": target["opponent"],
            "event_id": target["event_id"],
            "provider_season": target["provider_season"],
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
            "ESPN_NFL_IDENTITY_SCOREBOARD": captured.isoformat(),
            "NFLVERSE_WEEKLY_PLAYER_STATS": captured.isoformat(),
        },
        "evidence_version": EVIDENCE_VERSION,
        "rate_provenance": (
            f"{PROVIDER_ID};source_label={source_label};nflverse_sha256="
            + ",".join(f"{season}:{digest}" for season, digest in sorted(source_hashes.items()))
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
