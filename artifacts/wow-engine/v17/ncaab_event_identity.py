"""Canonical NCAAB current-event reconciliation for V17 Free-Core.

Free ESPN scoreboard discovery supplies provider aliases only. The canonical
identity is assigned after a unique team/season/scheduled-tip match against the open-licensed
SportsDataverse men's college basketball schedule already used by WOW's NCAAB
research lane.

The matched schedule id is a governed schedule identity, not a copy of the
discovery alias and not a fitted-model or publication grant. No market price,
sporting probability, calibration, ranking, or execution authority exists here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
_ERROR_TTL_SECONDS = 30.0
_MAX_TIP_DRIFT_SECONDS = 1800
_EASTERN = ZoneInfo("America/New_York")
_YEAR_LOCKS: dict[int, threading.Lock] = {}
_ERROR_CACHE: dict[int, tuple[float, str, str]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE: dict[int, tuple[float, list[Mapping[str, Any]]]] = {}
_REQUIRED_COLUMNS = ("game_id", "game_date", "home_id", "away_id", "game_date_time", "time_valid")


class NCAABEventIdentityError(RuntimeError):
    """Typed fail-closed NCAAB schedule identity error, never a model fallback."""

    def __init__(self, code: str, detail: str = ""):
        """Preserve the canonical failure code separately from source detail."""
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _aware(value: Any) -> datetime:
    """Require a timezone-aware event tip and normalize it to UTC.

    Raises:
        NCAABEventIdentityError: For a missing, invalid or naive timestamp.
    """
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NCAABEventIdentityError("NCAAB_EVENT_TIME_INVALID", str(value)) from exc
    if parsed.utcoffset() is None:
        raise NCAABEventIdentityError("NCAAB_EVENT_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def _strip_espn(value: Any) -> str:
    """Normalize an ESPN ID alias without inventing or coercing an identifier."""
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
    timeout: int = 10,
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
    fetcher: Any = None,
    now_monotonic: float | None = None,
) -> list[Mapping[str, Any]]:
    """Fetch cached season rows or re-raise a short-lived typed source failure.

    The per-season lock ensures a failing upstream read cannot multiply into
    one network timeout per discovered event. Fixture fetchers bypass caches.
    """
    # Frozen fixtures bypass the production source cache.
    if fetcher is not None:
        return list(fetcher(int(year)))
    key = int(year)
    with _CACHE_LOCK:
        year_lock = _YEAR_LOCKS.setdefault(key, threading.Lock())
    # Per-season single-flight avoids N repeated 10-second source timeouts.
    with year_lock:
        now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and now_value - cached[0] <= _CACHE_TTL_SECONDS:
                return list(cached[1])
            failure = _ERROR_CACHE.get(key)
            if failure is not None and now_value - failure[0] <= _ERROR_TTL_SECONDS:
                raise NCAABEventIdentityError(failure[1], failure[2])
        try:
            rows = list(fetch_ncaab_schedule(key))
        except NCAABEventIdentityError as exc:
            with _CACHE_LOCK:
                _ERROR_CACHE[key] = (time.monotonic(), exc.code, exc.detail)
            raise
        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), list(rows))
            _ERROR_CACHE.pop(key, None)
        return rows


def _scheduled_tip_utc(row: Mapping[str, Any]) -> datetime | None:
    """Schedule game_date_time is documented as America/New_York."""
    if str(row.get("time_valid") or "").strip().lower() not in ("true", "1"):
        return None
    value = str(row.get("game_date_time") or "").strip()
    if not value:
        return None
    try:
        tip = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if tip.utcoffset() is None:
        tip = tip.replace(tzinfo=_EASTERN)
    return tip.astimezone(timezone.utc)


def resolve_ncaab_current_event_identity(
    *,
    event_start_time: str,
    home_team_alias: str,
    away_team_alias: str,
    fetcher: Any = None,
) -> dict[str, Any]:
    """Resolve a unique source-valid, ordered-team schedule tip within 30m.

    An event ID is evidence of identity only. This never certifies a fitted
    model, ranks a pick, publishes a probability or grants wager execution.

    Raises:
        NCAABEventIdentityError: On invalid, absent, ambiguous or stale proof.
    """
    event_time = _aware(event_start_time)
    home_id = _strip_espn(home_team_alias)
    away_id = _strip_espn(away_team_alias)
    if not home_id or not away_id or home_id == away_id:
        raise NCAABEventIdentityError("NCAAB_PROVIDER_TEAM_ALIAS_INVALID")

    # Local date is only a prefilter; independent schedule tip within
    # 30 minutes and ordered team IDs must prove the canonical event.
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
        and str(row.get("game_id") or "").strip().isdecimal()
        and (
            not str(row.get("season") or "").strip()
            or str(row.get("season")).strip() == str(schedule_season_year(event_time))
        )
        and (tip := _scheduled_tip_utc(row)) is not None
        and abs((tip - event_time).total_seconds()) <= _MAX_TIP_DRIFT_SECONDS
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
        "matched_schedule_game_id": event_id,
        "matched_schedule_tip_utc": _scheduled_tip_utc(selected).isoformat(),
        "identity_tip_drift_seconds": abs((_scheduled_tip_utc(selected) - event_time).total_seconds()),
        "home_team_id": f"espn-{home_id}",
        "away_team_id": f"espn-{away_id}",
        "identity_provider": SOURCE_PROVIDER,
        "identity_resolution": "SPORTSDATAVERSE_MBB_SCHEDULE_EXACT_TEAM_TIP_MATCH",
        "identity_source_asset": SCHEDULE_URL.format(year=schedule_season_year(event_time)),
        "market_features_used": False,
        "prediction_authority": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def reset_cache() -> None:
    """Clear positive, negative and single-flight state for a fresh read."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _ERROR_CACHE.clear()
        _YEAR_LOCKS.clear()


__all__ = [
    "CAN_EXECUTE",
    "NCAABEventIdentityError",
    "SOURCE_PROVIDER",
    "fetch_ncaab_schedule",
    "reset_cache",
    "resolve_ncaab_current_event_identity",
    "schedule_season_year",
]
