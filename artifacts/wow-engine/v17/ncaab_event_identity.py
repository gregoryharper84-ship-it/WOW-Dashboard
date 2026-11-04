"""Canonical NCAAB current-event reconciliation for V17 Free-Core.

ESPN discovery event ids and team ids remain provider *aliases*. WOW assigns the
canonical NCAAB event id ``NCAAB:<espn_game_id>`` only after the public ESPN
men's college basketball scoreboard proves exactly one unique participant + date
match. That prefix is the identity the NCAAB training corpus is keyed by, so a
resolved row speaks the same event language as the fitted lane.

Why the scoreboard and not SportsDataverse: there is no NCAAB *schedule* release.
SportsDataverse publishes only ``espn_mens_college_basketball_team_boxscores``
(verified 404 for the schedules release), which contains already-completed games
and therefore cannot prove a pregame identity. The scoreboard is the only open
source in this repository that can.

No market price, sporting probability, calibration, ranking or execution
authority exists in this module. can_execute is false unconditionally.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Any, Callable, Mapping

import requests

CAN_EXECUTE = False
SOURCE_PROVIDER = "ESPN_NCAAB_SCOREBOARD"
BASE_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard"
)
CANONICAL_EVENT_PREFIX = "NCAAB:"
IDENTITY_RESOLUTION = "ESPN_SCOREBOARD_EXACT_PARTICIPANTS_DATE_MATCH"

# A pregame probability must never be reused as a live one, and one unreachable
# source must not be re-requested for every row. Successful reads and typed
# failures are both cached for this window; a failure cache entry keeps an
# outage from multiplying into one provider call per discovered event.
_CACHE_TTL_SECONDS = 300.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, list[Mapping[str, Any]] | None, str | None]] = {}

Fetcher = Callable[[str], list[Mapping[str, Any]]]


class NCAABEventIdentityError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NCAABEventIdentityError("NCAAB_EVENT_TIME_INVALID", str(value)) from exc
    if parsed.utcoffset() is None:
        raise NCAABEventIdentityError("NCAAB_EVENT_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def _strip_espn(value: Any) -> str:
    token = str(value or "").strip()
    return token.removeprefix("espn-").strip()


def _competitors(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return ``(home_id, away_id)`` only when both sides are explicit."""
    competitions = row.get("competitions")
    if not isinstance(competitions, list) or not competitions:
        return None
    competition = competitions[0]
    if not isinstance(competition, Mapping):
        return None
    sides: dict[str, str] = {}
    for competitor in competition.get("competitors") or []:
        if not isinstance(competitor, Mapping):
            continue
        side = str(competitor.get("homeAway") or "").strip().lower()
        if side in {"home", "away"}:
            sides[side] = _strip_espn(competitor.get("id"))
    if not sides.get("home") or not sides.get("away"):
        return None
    return sides["home"], sides["away"]


def _fetch_scoreboard(date_key: str) -> list[Mapping[str, Any]]:
    try:
        response = requests.get(
            BASE_URL,
            params={"dates": date_key, "limit": 500},
            timeout=15,
            headers={"Accept": "application/json", "User-Agent": "WOW-V17-Free-Core/1.0"},
        )
    except Exception as exc:  # noqa: BLE001
        raise NCAABEventIdentityError(
            "NCAAB_OFFICIAL_SOURCE_TRANSPORT_FAILED", type(exc).__name__
        ) from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        raise NCAABEventIdentityError("NCAAB_OFFICIAL_SOURCE_RATE_LIMITED")
    if status != 200:
        raise NCAABEventIdentityError(f"NCAAB_OFFICIAL_SOURCE_HTTP_{status or 'UNKNOWN'}")
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise NCAABEventIdentityError(
            "NCAAB_OFFICIAL_SOURCE_RESPONSE_INVALID", type(exc).__name__
        ) from exc
    events = payload.get("events") if isinstance(payload, Mapping) else None
    if not isinstance(events, list):
        raise NCAABEventIdentityError("NCAAB_OFFICIAL_SOURCE_SCHEMA_DRIFT")
    return [event for event in events if isinstance(event, Mapping)]


def _scoreboard_rows(
    date_key: str,
    *,
    fetcher: Fetcher | None = None,
    now_monotonic: float | None = None,
) -> list[Mapping[str, Any]]:
    # Tests and custom callers supply their own fetcher and bypass global caching.
    if fetcher is not None:
        return list(fetcher(date_key))

    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    with _CACHE_LOCK:
        cached = _CACHE.get(date_key)
        if cached is not None and (now_value - cached[0]) <= _CACHE_TTL_SECONDS:
            if cached[2] is not None:
                raise NCAABEventIdentityError(cached[2])
            return list(cached[1] or ())
    try:
        rows = list(_fetch_scoreboard(date_key))
    except NCAABEventIdentityError as exc:
        with _CACHE_LOCK:
            _CACHE[date_key] = (now_value, None, exc.code)
        raise
    with _CACHE_LOCK:
        _CACHE[date_key] = (now_value, list(rows), None)
    return rows


def date_keys_for(event_time: datetime) -> tuple[str, ...]:
    """Scoreboard date window around the UTC start.

    Evening North-American tip-offs cross UTC midnight, so the calendar date is
    not presumed to equal the UTC date. Exact participant matching still decides
    identity; the window only bounds acquisition.
    """
    return tuple(
        (event_time.date() + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in (-1, 0, 1)
    )


def resolve_ncaab_current_event_identity(
    *,
    event_start_time: str,
    home_team_alias: str,
    away_team_alias: str,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    event_time = _aware(event_start_time)
    home_id = _strip_espn(home_team_alias)
    away_id = _strip_espn(away_team_alias)
    if not home_id or not away_id or home_id == away_id:
        raise NCAABEventIdentityError(
            "NCAAB_PROVIDER_TEAM_ALIAS_INVALID",
            f"{home_team_alias}|{away_team_alias}",
        )

    rows: list[Mapping[str, Any]] = []
    for date_key in date_keys_for(event_time):
        rows.extend(_scoreboard_rows(date_key, fetcher=fetcher))

    matches: list[tuple[str, datetime]] = []
    for row in rows:
        event_id = str(row.get("id") or "").strip()
        if not event_id:
            continue
        sides = _competitors(row)
        if sides is None or sides != (home_id, away_id):
            continue
        try:
            start = _aware(row.get("date"))
        except NCAABEventIdentityError:
            continue
        matches.append((event_id, start))

    ids = {event_id for event_id, _ in matches}
    if not ids:
        raise NCAABEventIdentityError("NCAAB_CANONICAL_EVENT_NOT_FOUND")
    if len(ids) != 1:
        raise NCAABEventIdentityError("NCAAB_CANONICAL_EVENT_AMBIGUOUS")

    event_id, start = matches[0]
    return {
        "event_id": f"{CANONICAL_EVENT_PREFIX}{event_id}",
        "espn_event_id": event_id,
        "event_start_time": start.isoformat(),
        "home_team_id": f"espn-{home_id}",
        "away_team_id": f"espn-{away_id}",
        "identity_provider": SOURCE_PROVIDER,
        "identity_resolution": IDENTITY_RESOLUTION,
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = [
    "BASE_URL",
    "CAN_EXECUTE",
    "CANONICAL_EVENT_PREFIX",
    "IDENTITY_RESOLUTION",
    "NCAABEventIdentityError",
    "SOURCE_PROVIDER",
    "date_keys_for",
    "reset_cache",
    "resolve_ncaab_current_event_identity",
]
