"""Secondary research-only acquisition for WOW V17 Scout.

This adapter is intentionally outside every governed probability lane. It can
supply schedule identity and h2h market evidence when the primary Odds API path
is unavailable, but it cannot create model probabilities, calibration, value,
or executable betting instructions.

The current secondary source is ESPN's public site scoreboard feed. ESPN's site
API is not a certified sportsbook/exact-line authority, so all returned market
evidence is explicitly marked research-only and prediction_authority=false.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ESPN_BASE = os.environ.get("WOW_SCOUT_ESPN_BASE_URL", "https://site.api.espn.com/apis/site/v2/sports").rstrip("/")
SECONDARY_ENABLED = os.environ.get("WOW_SCOUT_SECONDARY_SOURCE_ENABLED", "true").strip().lower() == "true"
SECONDARY_TIMEOUT_SECONDS = float(os.environ.get("WOW_SCOUT_SECONDARY_SOURCE_TIMEOUT_SECONDS", "10"))

ESPN_SPORT_MAP: dict[str, tuple[str, str, str]] = {
    "americanfootball_nfl": ("football", "nfl", "NFL"),
    "americanfootball_ncaaf": ("football", "college-football", "College Football"),
    "baseball_mlb": ("baseball", "mlb", "MLB"),
    "basketball_nba": ("basketball", "nba", "NBA"),
    "basketball_wnba": ("basketball", "wnba", "WNBA"),
    "basketball_ncaab": ("basketball", "mens-college-basketball", "NCAA Men's Basketball"),
    "icehockey_nhl": ("hockey", "nhl", "NHL"),
}

_SPORTS_RE = re.compile(r"^/odds-api/v4/sports$")
_EVENTS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events$")
_EVENT_DATA_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events/([^/]+)/(markets|odds)$")
_SCOREBOARD_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


@dataclass
class SecondaryResult:
    ok: bool
    data: Any = None
    status: int | None = None
    code: str | None = None


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _date_key(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).strftime("%Y%m%d")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y%m%d")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y%m%d")


def _date_range(params: dict[str, Any] | None) -> str:
    params = params or {}
    start = _date_key(str(params.get("commenceTimeFrom") or ""))
    end = _date_key(str(params.get("commenceTimeTo") or ""))
    return start if start == end else f"{start}-{end}"


def _http_json(url: str, params: dict[str, Any] | None = None) -> SecondaryResult:
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    req = Request(
        url + (f"?{query}" if query else ""),
        headers={"Accept": "application/json", "User-Agent": "WOW-V17-Scout-Research/1.0"},
    )
    try:
        with urlopen(req, timeout=SECONDARY_TIMEOUT_SECONDS) as response:
            return SecondaryResult(True, json.loads(response.read().decode("utf-8")), response.status)
    except HTTPError as exc:
        return SecondaryResult(False, status=exc.code, code=f"ESPN_HTTP_{exc.code}")
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return SecondaryResult(False, code=f"ESPN_{type(exc).__name__}")


def _scoreboard(sport_key: str, params: dict[str, Any] | None = None) -> SecondaryResult:
    mapped = ESPN_SPORT_MAP.get(sport_key)
    if not mapped:
        return SecondaryResult(False, code="SECONDARY_SOURCE_UNSUPPORTED_SPORT")
    sport, league, _title = mapped
    dates = _date_range(params)
    cache_key = (sport_key, dates)
    cached = _SCOREBOARD_CACHE.get(cache_key)
    if cached is not None:
        return SecondaryResult(True, cached, 200)
    result = _http_json(f"{ESPN_BASE}/{sport}/{league}/scoreboard", {"dates": dates, "limit": 1000})
    if result.ok and isinstance(result.data, dict):
        _SCOREBOARD_CACHE[cache_key] = result.data
    return result


def _competitors(event: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    competitions = event.get("competitions") or []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    home = None
    away = None
    for comp in competition.get("competitors") or []:
        if not isinstance(comp, dict):
            continue
        if comp.get("homeAway") == "home":
            home = comp
        elif comp.get("homeAway") == "away":
            away = comp
    return home, away


def _team_name(comp: dict[str, Any] | None) -> str | None:
    if not isinstance(comp, dict):
        return None
    team = comp.get("team") if isinstance(comp.get("team"), dict) else {}
    return team.get("displayName") or team.get("shortDisplayName") or team.get("name")


def espn_event_to_primary_shape(event: dict[str, Any], sport_key: str) -> dict[str, Any] | None:
    event_id = event.get("id")
    if not event_id:
        return None
    home, away = _competitors(event)
    return {
        "id": f"espn-{event_id}",
        "sport_key": sport_key,
        "commence_time": event.get("date"),
        "home_team": _team_name(home),
        "away_team": _team_name(away),
        "_wow_secondary_event_id": str(event_id),
        "_wow_secondary_source": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
    }


def _event_matches(event: dict[str, Any], context: dict[str, Any] | None, event_id: str) -> bool:
    if str(event.get("id")) == event_id.removeprefix("espn-"):
        return True
    if not context:
        return False
    home, away = _competitors(event)
    return (
        _norm(_team_name(home)) == _norm(context.get("home_team"))
        and _norm(_team_name(away)) == _norm(context.get("away_team"))
    )


def _moneyline(value: Any) -> int | float | None:
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value)) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def espn_h2h_payload(event: dict[str, Any], *, primary_failure: str | None = None) -> dict[str, Any] | None:
    competitions = event.get("competitions") or []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    odds_rows = competition.get("odds") or []
    odds = next((row for row in odds_rows if isinstance(row, dict)), None)
    if not odds:
        return None

    home, away = _competitors(event)
    home_name = _team_name(home)
    away_name = _team_name(away)
    home_ml = _moneyline((odds.get("homeTeamOdds") or {}).get("moneyLine"))
    away_ml = _moneyline((odds.get("awayTeamOdds") or {}).get("moneyLine"))
    if not home_name or not away_name or home_ml is None or away_ml is None:
        return None

    provider = odds.get("provider") if isinstance(odds.get("provider"), dict) else {}
    provider_name = str(provider.get("name") or provider.get("title") or "ESPN_SCOREBOARD")
    marker = {
        "provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
        "provider_detail": provider_name,
        "source_tier": "SECONDARY_LIVE_RESEARCH",
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "primary_source_failure": primary_failure,
        "can_execute": False,
    }
    return {
        "id": f"espn-{event.get('id')}",
        "commence_time": event.get("date"),
        "home_team": home_name,
        "away_team": away_name,
        "_wow_secondary_source": marker,
        "bookmakers": [{
            "key": f"espn_backup_{_norm(provider_name) or 'scoreboard'}",
            "title": f"{provider_name} via ESPN backup",
            "last_update": odds.get("lastUpdated") or event.get("date"),
            "markets": [{
                "key": "h2h",
                "last_update": odds.get("lastUpdated") or event.get("date"),
                "outcomes": [
                    {"name": home_name, "price": home_ml},
                    {"name": away_name, "price": away_ml},
                ],
            }],
        }],
    }


def _find_event(
    sport_key: str,
    event_id: str,
    context: dict[str, Any] | None,
) -> SecondaryResult:
    query_params: dict[str, Any] = {}
    if context and context.get("commence_time"):
        date = _date_key(str(context.get("commence_time")))
        query_params = {"commenceTimeFrom": date, "commenceTimeTo": date}
    board = _scoreboard(sport_key, query_params)
    if not board.ok:
        return board
    events = board.data.get("events") if isinstance(board.data, dict) else []
    for event in events or []:
        if isinstance(event, dict) and _event_matches(event, context, event_id):
            return SecondaryResult(True, event, 200)
    return SecondaryResult(False, status=404, code="SECONDARY_SOURCE_EVENT_NOT_FOUND")


def secondary_for_request(
    path: str,
    params: dict[str, Any] | None,
    event_context: dict[str, dict[str, Any]],
    *,
    primary_failure: str | None = None,
) -> SecondaryResult:
    if not SECONDARY_ENABLED:
        return SecondaryResult(False, code="SECONDARY_SOURCE_DISABLED")

    if _SPORTS_RE.match(path):
        return SecondaryResult(True, [
            {"key": key, "title": title, "active": True, "_wow_secondary_source": True}
            for key, (_sport, _league, title) in ESPN_SPORT_MAP.items()
        ], 200)

    match = _EVENTS_RE.match(path)
    if match:
        sport_key = match.group(1)
        board = _scoreboard(sport_key, params)
        if not board.ok:
            return board
        events = board.data.get("events") if isinstance(board.data, dict) else []
        converted = [espn_event_to_primary_shape(event, sport_key) for event in (events or []) if isinstance(event, dict)]
        return SecondaryResult(True, [event for event in converted if event], 200)

    match = _EVENT_DATA_RE.match(path)
    if match:
        sport_key, event_id, _kind = match.groups()
        found = _find_event(sport_key, event_id, event_context.get(event_id))
        if not found.ok:
            return found
        payload = espn_h2h_payload(found.data, primary_failure=primary_failure)
        if payload is None:
            return SecondaryResult(False, status=404, code="SECONDARY_SOURCE_H2H_UNAVAILABLE")
        requested_markets = {
            item.strip() for item in str((params or {}).get("markets") or "h2h").split(",") if item.strip()
        }
        if requested_markets and not requested_markets.issubset({"h2h"}):
            return SecondaryResult(False, status=403, code="SECONDARY_SOURCE_CORE_H2H_ONLY")
        return SecondaryResult(True, payload, 200)

    return SecondaryResult(False, code="SECONDARY_SOURCE_UNSUPPORTED_REQUEST")


__all__ = [
    "ESPN_SPORT_MAP",
    "SecondaryResult",
    "espn_event_to_primary_shape",
    "espn_h2h_payload",
    "secondary_for_request",
]
