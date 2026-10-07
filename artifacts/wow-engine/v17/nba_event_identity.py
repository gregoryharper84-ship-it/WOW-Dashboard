"""Canonical NBA current-event reconciliation for V17 Free-Core.

Free ESPN scoreboard discovery supplies provider aliases only. The canonical
identity is assigned after a unique team/date match against the open-licensed
SportsDataverse ESPN schedule corpus already used by WOW's NBA training lane.

The matched schedule id is therefore a governed schedule identity, not a direct
promotion of the discovery alias. No market or probability authority exists here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import threading
import time
from typing import Any, Mapping

from basketball_event_hydration_runtime import (
    BasketballHydrationError,
    fetch_sportsdataverse_games,
)

CAN_EXECUTE = False
SOURCE_PROVIDER = "SPORTSDATAVERSE_ESPN"
_CACHE_TTL_SECONDS = 300.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[int, tuple[float, list[Mapping[str, Any]]]] = {}


class NBAEventIdentityError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NBAEventIdentityError("NBA_EVENT_TIME_INVALID", str(value)) from exc
    if parsed.utcoffset() is None:
        raise NBAEventIdentityError("NBA_EVENT_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def _strip_espn(value: Any) -> str:
    token = str(value or "").strip()
    return token.removeprefix("espn-").strip()


def schedule_season_year(event_time: datetime) -> int:
    """SportsDataverse NBA schedule assets use the season-ending year."""
    return event_time.year + 1 if event_time.month >= 7 else event_time.year


def _schedule_rows(
    year: int,
    *,
    fetcher: Any = fetch_sportsdataverse_games,
    now_monotonic: float | None = None,
) -> list[Mapping[str, Any]]:
    # Tests/custom callers supply their own fetcher and bypass global caching.
    if fetcher is not fetch_sportsdataverse_games:
        return list(fetcher("NBA", int(year)))

    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    with _CACHE_LOCK:
        cached = _CACHE.get(int(year))
        if cached is not None and (now_value - cached[0]) <= _CACHE_TTL_SECONDS:
            return list(cached[1])
    try:
        rows = list(fetch_sportsdataverse_games("NBA", int(year)))
    except BasketballHydrationError as exc:
        raise NBAEventIdentityError(
            str(exc).split(":", 1)[0] or "NBA_SCHEDULE_SOURCE_UNAVAILABLE",
            str(exc),
        ) from exc
    with _CACHE_LOCK:
        _CACHE[int(year)] = (now_value, list(rows))
    return rows


def resolve_nba_current_event_identity(
    *,
    event_start_time: str,
    home_team_alias: str,
    away_team_alias: str,
    fetcher: Any = fetch_sportsdataverse_games,
) -> dict[str, Any]:
    event_time = _aware(event_start_time)
    home_id = _strip_espn(home_team_alias)
    away_id = _strip_espn(away_team_alias)
    if not home_id or not away_id or home_id == away_id:
        raise NBAEventIdentityError("NBA_PROVIDER_TEAM_ALIAS_INVALID")

    # SportsDataverse game_date is calendar-date schedule identity. Allow ±1 day
    # around UTC to handle evening North-American games crossing UTC midnight.
    candidate_dates = {
        (event_time.date() + timedelta(days=offset)).isoformat()
        for offset in (-1, 0, 1)
    }
    rows = _schedule_rows(schedule_season_year(event_time), fetcher=fetcher)
    matches = [
        row for row in rows
        if isinstance(row, Mapping)
        and str(row.get("game_date") or row.get("date") or "")[:10] in candidate_dates
        and _strip_espn(row.get("home_id")) == home_id
        and _strip_espn(row.get("away_id")) == away_id
        and str(row.get("game_id") or row.get("id") or "").strip()
    ]

    ids = {
        str(row.get("game_id") or row.get("id") or "").strip()
        for row in matches
    }
    if not ids:
        raise NBAEventIdentityError("NBA_CANONICAL_EVENT_NOT_FOUND")
    if len(ids) != 1:
        raise NBAEventIdentityError("NBA_CANONICAL_EVENT_AMBIGUOUS")

    event_id = next(iter(ids))
    selected = next(
        row for row in matches
        if str(row.get("game_id") or row.get("id") or "").strip() == event_id
    )
    return {
        "event_id": f"espn-{event_id.removeprefix('espn-')}",
        "game_date": str(selected.get("game_date") or selected.get("date") or "")[:10],
        "home_team_id": f"espn-{home_id}",
        "away_team_id": f"espn-{away_id}",
        "identity_provider": SOURCE_PROVIDER,
        "identity_resolution": "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH",
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = [
    "CAN_EXECUTE",
    "NBAEventIdentityError",
    "SOURCE_PROVIDER",
    "resolve_nba_current_event_identity",
    "reset_cache",
    "schedule_season_year",
]
