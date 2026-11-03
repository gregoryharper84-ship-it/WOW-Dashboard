"""Event-timed Rundown market-history captures (data-point cost control).

Replaces fixed-interval polling with captures only when they matter:

- MORNING: once per sport per local day, at or after
  ``WOW_RUNDOWN_TIMED_MORNING_HOUR`` (default 9, local time), and only when that
  sport has pregame games today. Gives early-pick prices.
- CLOSE: once per start window. Starts within ``WOW_RUNDOWN_TIMED_WINDOW_MINUTES``
  (default 30) of a window's first start share one capture, taken when that
  first start is within ``WOW_RUNDOWN_TIMED_CLOSE_LEAD_MINUTES`` (default 30)
  and still in the future. Its last pregame quote becomes the CLOSE reference.

Start times come from ESPN's free public scoreboard (no key, no quota).
Completed windows are recorded durably in ``wow_market_feed_sync_state``
(``RUNDOWN:TIMED:<local date>``) so restarts never repeat a paid capture.
Everything else (budgets, 429 suspension, typed failures) is the existing
history collector. Evidence only: ``can_execute=false``.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from v17 import rundown_market_history as history
from v17 import scout_secondary_source as espn

CAN_EXECUTE = False
SYNC_TABLE = "wow_market_feed_sync_state"
MORNING = "MORNING"
CLOSE = "CLOSE"
LOGGER = logging.getLogger(__name__)


def schedule_mode() -> str:
    mode = os.getenv("WOW_RUNDOWN_MARKET_HISTORY_SCHEDULE", "TIMED").strip().upper()
    return mode if mode in {"TIMED", "INTERVAL"} else "TIMED"


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(value, high))


def morning_hour() -> int:
    return _int("WOW_RUNDOWN_TIMED_MORNING_HOUR", 9, 0, 23)


def close_lead() -> timedelta:
    return timedelta(minutes=_int("WOW_RUNDOWN_TIMED_CLOSE_LEAD_MINUTES", 30, 5, 120))


def window_span() -> timedelta:
    return timedelta(minutes=_int("WOW_RUNDOWN_TIMED_WINDOW_MINUTES", 30, 0, 180))


def tick_seconds() -> int:
    return _int("WOW_RUNDOWN_TIMED_TICK_SECONDS", 600, 120, 1800)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pregame_starts(
    sport_key: str,
    local_day: date,
    *,
    now: datetime,
    fetch: Callable[..., Any] | None = None,
) -> tuple[list[datetime], str | None]:
    """Future start times of today's (local) pregame games, from free ESPN."""
    mapped = espn.ESPN_SPORT_MAP.get(sport_key)
    if not mapped:
        return [], "ESPN_UNSUPPORTED_SPORT"
    sport, league, _title = mapped
    tz = history._timezone()
    starts: set[datetime] = set()
    # ESPN's date buckets are US-Eastern-ish; query the local day and the next
    # so late local starts are never missed, then keep only this local day.
    for day in (local_day, local_day + timedelta(days=1)):
        result = (fetch or espn._http_json)(
            f"{espn.ESPN_BASE}/{sport}/{league}/scoreboard", {"dates": day.strftime("%Y%m%d"), "limit": 1000}
        )
        if not getattr(result, "ok", False):
            return [], str(getattr(result, "code", None) or "ESPN_SCOREBOARD_UNAVAILABLE")
        for event in (result.data or {}).get("events") or []:
            if not isinstance(event, dict):
                continue
            state = str((((event.get("status") or {}).get("type") or {}).get("state")) or "").lower()
            start = _parse(event.get("date"))
            if state == "pre" and start is not None and start > now and start.astimezone(tz).date() == local_day:
                starts.add(start)
    return sorted(starts), None


def windows(starts: list[datetime]) -> list[datetime]:
    """First start of each window; later starts within the span share it."""
    anchors: list[datetime] = []
    for start in sorted(starts):
        if not anchors or start - anchors[-1] > window_span():
            anchors.append(start)
    return anchors


def _state_key(local_day: date) -> str:
    return f"RUNDOWN:TIMED:{local_day.isoformat()}"


def read_done(client: Any, local_day: date) -> set[str]:
    try:
        rows = getattr(
            client.table(SYNC_TABLE).select("metadata").eq("feed_key", _state_key(local_day)).limit(1).execute(),
            "data", None,
        ) or []
        meta = rows[0].get("metadata") if rows and isinstance(rows[0], dict) else {}
        return set((meta or {}).get("done") or [])
    except Exception:  # noqa: BLE001 - unknown state means "not done"; budgets still bound calls
        return set()


def write_done(client: Any, local_day: date, done: set[str]) -> None:
    client.table(SYNC_TABLE).upsert(
        {
            "feed_key": _state_key(local_day),
            "sport_key": "ALL",
            "slate_date": local_day.isoformat(),
            "market_ids": [],
            "affiliate_ids": [],
            "acquisition_mode": "TIMED",
            "metadata": {"done": sorted(done)},
            "can_execute": False,
        },
        on_conflict="feed_key",
    ).execute()


def due_captures(
    client: Any,
    *,
    now: datetime,
    fetch: Callable[..., Any] | None = None,
) -> tuple[dict[str, list[str]], dict[str, str], date]:
    """Sport -> window keys due now; plus typed schedule failures."""
    local_now = now.astimezone(history._timezone())
    local_day = local_now.date()
    done = read_done(client, local_day)
    due: dict[str, list[str]] = {}
    failures: dict[str, str] = {}
    for sport_key in history.configured_sports():
        starts, code = pregame_starts(sport_key, local_day, now=now, fetch=fetch)
        if code:
            failures[sport_key] = code
            continue
        if not starts:
            continue  # no pregame games left today: no paid call
        keys: list[str] = []
        morning_key = f"{sport_key}|{MORNING}"
        if local_now.hour >= morning_hour() and morning_key not in done:
            keys.append(morning_key)
        for anchor in windows(starts):
            key = f"{sport_key}|{CLOSE}|{_iso(anchor)}"
            if key not in done and now < anchor <= now + close_lead():
                keys.append(key)
        if keys:
            due[sport_key] = keys
    return due, failures, local_day


def run_timed_cycle(
    client: Any,
    *,
    now: datetime | None = None,
    fetch: Callable[..., Any] | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    due, failures, local_day = due_captures(client, now=current, fetch=fetch)
    summary: dict[str, Any] = {
        "schedule": "TIMED",
        "due": due,
        "schedule_failures": failures,
        "prediction_authority": False,
        "can_execute": False,
    }
    if not due:
        summary["status"] = "NOTHING_DUE"
        summary["reference_rows_written"] = history.materialize_reference_backlog(client, now=current)
        summary["provider_calls"] = 0
        return summary
    result = history.collect_history_once(client, now=current, opener=opener, sports=list(due))
    summary.update({k: result.get(k) for k in ("status", "reason_code", "provider_calls", "reference_rows_written")})
    completed = {
        str(item.get("sport_key"))
        for item in result.get("results") or []
        if item.get("status") == "COMPLETE"
    }
    done = read_done(client, local_day)
    newly = {key for sport, keys in due.items() if sport in completed for key in keys}
    if newly:
        write_done(client, local_day, done | newly)
    summary["windows_completed"] = sorted(newly)
    return summary


__all__ = [
    "CAN_EXECUTE",
    "due_captures",
    "pregame_starts",
    "run_timed_cycle",
    "schedule_mode",
    "tick_seconds",
    "windows",
]
