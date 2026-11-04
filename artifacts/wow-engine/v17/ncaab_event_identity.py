"""NCAAB schedule identity reconciliation; no scoring or execution authority.

ESPN discovery event IDs and team IDs are provider aliases. A canonical ID
is accepted only after a unique participants + verified start-time match in
the SportsDataverse MBB schedule corpus. Missing evidence stays unresolved.
"""
from __future__ import annotations

import csv
import io
import threading
import time
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

CAN_EXECUTE = False
SOURCE_PROVIDER = "SPORTSDATAVERSE_ESPN_MBB_SCHEDULE"
SCHEDULE_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "espn_mens_college_basketball_schedules/mbb_schedule_{season}.csv"
)
START_TOLERANCE_MINUTES = 30
_CACHE_TTL_SECONDS = 300.0
# Cache typed acquisition failures briefly: an unavailable season must not
# consume one 20-second request for every row in a shared cross-sport scan.
_FAILURE_TTL_SECONDS = 30.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[int, tuple[float, list[Mapping[str, Any]]]] = {}
_FAILURE_CACHE: dict[int, tuple[float, str]] = {}
_REQUIRED_COLUMNS = frozenset({"game_id", "home_id", "away_id", "game_date_time", "time_valid"})


class NCAABEventIdentityError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NCAABEventIdentityError("NCAAB_EVENT_TIME_INVALID") from exc
    if parsed.utcoffset() is None:
        raise NCAABEventIdentityError("NCAAB_EVENT_TIME_INVALID")
    return parsed.astimezone(timezone.utc)


def _espn_id(value: Any) -> str:
    token = str(value or "").strip().removeprefix("espn-")
    return token if 1 <= len(token) <= 20 and token.isascii() and token.isdecimal() and int(token) > 0 else ""


def season_end_year(event_time: datetime) -> int:
    """Men's college basketball schedules are keyed by the season-ending year."""
    return event_time.year + 1 if event_time.month >= 7 else event_time.year


def fetch_ncaab_schedule(
    year: int, *, session: Any = requests, timeout: int = 20,
) -> list[Mapping[str, Any]]:
    """Read the published SportsDataverse MBB schedule, never model features."""
    url = SCHEDULE_URL.format(season=int(year))
    try:
        response = session.get(
            url,
            timeout=timeout,
            headers={"Accept": "text/csv", "User-Agent": "WOW-V17-Free-Core/1.0"},
        )
    except Exception as exc:  # noqa: BLE001 - typed upstream failure
        raise NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_UNAVAILABLE") from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        raise NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_RATE_LIMITED")
    if status != 200:
        raise NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_HTTP_FAILED")
    reader = csv.DictReader(io.StringIO(str(getattr(response, "text", "") or "").lstrip("\ufeff")))
    if not _REQUIRED_COLUMNS.issubset(set(reader.fieldnames or [])):
        raise NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_SCHEMA_DRIFT")
    return [row for row in reader if isinstance(row, Mapping)]


def _schedule_rows(year: int, fetcher: Any = None) -> list[Mapping[str, Any]]:
    if fetcher is not None:
        return list(fetcher(int(year)))
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(year)
        if cached is not None and now - cached[0] <= _CACHE_TTL_SECONDS:
            return list(cached[1])
        failure = _FAILURE_CACHE.get(year)
        if failure is not None and now - failure[0] <= _FAILURE_TTL_SECONDS:
            raise NCAABEventIdentityError(failure[1])
    try:
        rows = fetch_ncaab_schedule(year)
    except NCAABEventIdentityError as exc:
        with _CACHE_LOCK:
            _FAILURE_CACHE[year] = (time.monotonic(), exc.code)
        raise
    # Timestamp completion, not request admission: slow downloads must not
    # return an already-expired success cache to the next row.
    with _CACHE_LOCK:
        _CACHE[year] = (time.monotonic(), list(rows))
        _FAILURE_CACHE.pop(year, None)
    return rows


def resolve_ncaab_current_event_identity(
    *, event_start_time: str, home_team_alias: str,
    away_team_alias: str, fetcher: Any = None,
) -> dict[str, Any]:
    event_time = _aware(event_start_time)
    home_id, away_id = _espn_id(home_team_alias), _espn_id(away_team_alias)
    if not home_id or not away_id or home_id == away_id:
        raise NCAABEventIdentityError("NCAAB_PROVIDER_TEAM_ALIAS_INVALID")
    year = season_end_year(event_time)
    try:
        rows = _schedule_rows(year, fetcher)
    except NCAABEventIdentityError:
        raise
    except Exception as exc:  # noqa: BLE001 - source failure, never model failure
        raise NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_UNAVAILABLE") from exc

    matches: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("time_valid") or "").strip().lower() not in {"true", "1", "yes"}:
            continue
        if _espn_id(row.get("home_id")) != home_id or _espn_id(row.get("away_id")) != away_id:
            continue
        game_id = _espn_id(row.get("game_id"))
        secondary_id = _espn_id(row.get("id")) if row.get("id") else game_id
        if not game_id or secondary_id != game_id:
            continue
        try:
            actual_time = _aware(row.get("game_date_time"))
        except NCAABEventIdentityError:
            continue
        if abs((actual_time - event_time).total_seconds()) > 60 * START_TOLERANCE_MINUTES:
            continue
        matches[game_id] = row
    if not matches:
        raise NCAABEventIdentityError("NCAAB_CANONICAL_EVENT_NOT_FOUND")
    if len(matches) != 1:
        raise NCAABEventIdentityError("NCAAB_CANONICAL_EVENT_AMBIGUOUS")
    event_id = next(iter(matches))
    return {
        "event_id": f"espn-{event_id}",
        "home_team_id": f"espn-{home_id}",
        "away_team_id": f"espn-{away_id}",
        "identity_provider": SOURCE_PROVIDER,
        "identity_resolution": "SPORTSDATAVERSE_EXACT_PARTICIPANTS_START_MATCH",
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _FAILURE_CACHE.clear()


__all__ = [
    "CAN_EXECUTE", "NCAABEventIdentityError", "SOURCE_PROVIDER",
    "fetch_ncaab_schedule", "resolve_ncaab_current_event_identity",
    "reset_cache", "season_end_year",
]
