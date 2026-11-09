"""Canonical NCAAB current-event reconciliation for V17 Free-Core.

Free ESPN scoreboard discovery supplies provider aliases only. The canonical
identity is assigned after a unique team/date match against the open-licensed
SportsDataverse men's college basketball schedule already used by WOW's NCAAB
research lane.

The matched schedule id is a governed schedule identity, not a copy of the
discovery alias and not a fitted-model or publication grant. No market price,
sporting probability, calibration, ranking, or execution authority exists here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import csv
import io
import threading
import time
from typing import Any, Mapping

import requests

from v17.model_source_entitlements import source_readiness

CAN_EXECUTE = False
SOURCE_PROVIDER = "SPORTSDATAVERSE_ESPN"
SCHEDULE_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "espn_mens_college_basketball_schedules/mbb_schedule_{year}.csv"
)
_CACHE_TTL_SECONDS = 300.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[int, tuple[float, list[Mapping[str, Any]]]] = {}
_REQUIRED_COLUMNS = ("game_id", "game_date", "home_id", "away_id")


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


def schedule_season_year(event_time: datetime) -> int:
    """SportsDataverse MBB schedule assets use the season-ending year.

    The 2026-27 season file is ``mbb_schedule_2027.csv`` and begins in November
    2026. A July-or-later tip belongs to the season that ends the next calendar
    year; spring games stay on the season already in progress.
    """
    return event_time.year + 1 if event_time.month >= 7 else event_time.year


def fetch_ncaab_schedule(
    year: int,
    *,
    session: Any = requests,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Read one open-licensed SportsDataverse MBB schedule release.

    Availability of the file is identity evidence only. It does not authorize
    training, calibration, or publication.
    """
    readiness = source_readiness("SPORTSDATAVERSE_ESPN")
    if not readiness.ready_for_candidate_training:
        blockers = ",".join(readiness.blockers) or "UNKNOWN"
        raise NCAABEventIdentityError(
            "NCAAB_SCHEDULE_SOURCE_NOT_READY",
            f"SPORTSDATAVERSE_ESPN_SOURCE_NOT_READY:{blockers}",
        )
    url = SCHEDULE_URL.format(year=int(year))
    try:
        response = session.get(
            url,
            headers={"Accept": "text/csv", "User-Agent": "WOW-V17-NCAAB-Identity/1.0"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - transport failure is a typed identity hold
        raise NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_UNAVAILABLE", type(exc).__name__) from exc
    status = getattr(response, "status_code", None)
    if status == 429:
        raise NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_RATE_LIMITED", url)
    if status != 200:
        raise NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_UNAVAILABLE", f"HTTP_{status}")
    text = str(getattr(response, "text", "") or "").lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = {str(value or "").strip() for value in (reader.fieldnames or [])}
    missing = [column for column in _REQUIRED_COLUMNS if column not in fieldnames]
    if missing:
        raise NCAABEventIdentityError(
            "NCAAB_SCHEDULE_SCHEMA_DRIFT",
            ",".join(missing),
        )
    return [dict(row) for row in reader]


def _schedule_rows(
    year: int,
    *,
    fetcher: Any = fetch_ncaab_schedule,
    now_monotonic: float | None = None,
) -> list[Mapping[str, Any]]:
    if fetcher is not fetch_ncaab_schedule:
        return list(fetcher(int(year)))

    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    with _CACHE_LOCK:
        cached = _CACHE.get(int(year))
        if cached is not None and (now_value - cached[0]) <= _CACHE_TTL_SECONDS:
            return list(cached[1])
    rows = list(fetch_ncaab_schedule(int(year)))
    with _CACHE_LOCK:
        _CACHE[int(year)] = (now_value, list(rows))
    return rows


def resolve_ncaab_current_event_identity(
    *,
    event_start_time: str,
    home_team_alias: str,
    away_team_alias: str,
    fetcher: Any = fetch_ncaab_schedule,
) -> dict[str, Any]:
    event_time = _aware(event_start_time)
    home_id = _strip_espn(home_team_alias)
    away_id = _strip_espn(away_team_alias)
    if not home_id or not away_id or home_id == away_id:
        raise NCAABEventIdentityError("NCAAB_PROVIDER_TEAM_ALIAS_INVALID")

    # Schedule game_date is the calendar date of the local tip. Allow ±1 UTC
    # day so an evening North-American game that crosses UTC midnight still
    # matches, without accepting a different matchup.
    candidate_dates = {
        (event_time.date() + timedelta(days=offset)).isoformat()
        for offset in (-1, 0, 1)
    }
    rows = _schedule_rows(schedule_season_year(event_time), fetcher=fetcher)
    matches = [
        row for row in rows
        if isinstance(row, Mapping)
        and str(row.get("game_date") or "")[:10] in candidate_dates
        and _strip_espn(row.get("home_id")) == home_id
        and _strip_espn(row.get("away_id")) == away_id
        and str(row.get("game_id") or "").strip()
    ]
    ids = {str(row.get("game_id") or "").strip() for row in matches}
    if not ids:
        raise NCAABEventIdentityError("NCAAB_CANONICAL_EVENT_NOT_FOUND")
    if len(ids) != 1:
        raise NCAABEventIdentityError("NCAAB_CANONICAL_EVENT_AMBIGUOUS")

    event_id = next(iter(ids))
    selected = next(
        row for row in matches
        if str(row.get("game_id") or "").strip() == event_id
    )
    return {
        "event_id": f"espn-{event_id.removeprefix('espn-')}",
        "game_date": str(selected.get("game_date") or "")[:10],
        "home_team_id": f"espn-{home_id}",
        "away_team_id": f"espn-{away_id}",
        "identity_provider": SOURCE_PROVIDER,
        "identity_resolution": "SPORTSDATAVERSE_MBB_SCHEDULE_EXACT_TEAM_DATE_MATCH",
        "market_features_used": False,
        "prediction_authority": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = [
    "CAN_EXECUTE",
    "NCAABEventIdentityError",
    "SOURCE_PROVIDER",
    "fetch_ncaab_schedule",
    "reset_cache",
    "resolve_ncaab_current_event_identity",
    "schedule_season_year",
]
