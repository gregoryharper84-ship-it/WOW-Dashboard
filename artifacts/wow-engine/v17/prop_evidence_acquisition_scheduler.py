"""Scheduled V17 prop evidence acquisition for the forward calibration cohort.

``acquire_daily_prop_snapshots`` only ran inside the authenticated
``/v17/daily-snapshot-run`` route, so ``wow_prop_evidence_snapshots`` was
produced only when someone called that route by hand. The forward cohort loop
consumes those snapshots every 15 minutes but had no autonomous producer, so a
live future cohort could not build on its own: evidence capture last ran at
2026-09-11T17:36Z and the future slate was empty the following day.

This loop gives acquisition its own pass, mirroring the existing forward cohort
scheduler. It seeds the same immutable snapshots the route already produces --
no new candidate source, no invented line, no scoring, no publication, and no
calibrator fit. Off unless explicitly enabled.

WOW-PATCH-2026-09-12-V17-PROP-EVIDENCE-ACQUISITION-SCHEDULE
can_execute=false unconditionally.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from v17.daily_prop_acquisition import acquire_daily_prop_snapshots
from v17.nba_points_snapshot_capture import acquire_daily_nba_points_snapshots

CAN_EXECUTE = False


def slate_dates(now: datetime, timezone_name: str, forward_days: int) -> list[str]:
    """Slate dates to acquire, starting with the current local date.

    The cohort only captures events that have not started yet, so acquiring the
    current date alone leaves the future slate empty once the last game of the
    day begins. Covering the following date keeps a forward slate available
    across the whole day; a date with no posted probable pitchers simply
    acquires nothing.
    """
    try:
        local_now = now.astimezone(ZoneInfo(timezone_name))
    except Exception:
        local_now = now.astimezone(timezone.utc)
    return [
        (local_now + timedelta(days=offset)).date().isoformat()
        for offset in range(max(1, int(forward_days)))
    ]


def _acquire_once(
    *,
    db_client_fn: Callable[[], Any],
    requested_date: str,
    timezone_name: str,
    max_candidates: int,
) -> dict[str, Any]:
    db = db_client_fn()
    # Keep sport lanes independent: a failure in the MLB producer must not
    # suppress NBA POINTS evidence capture (and vice versa).
    try:
        mlb = acquire_daily_prop_snapshots(
            db=db,
            requested_date=requested_date,
            requested_timezone=timezone_name,
            max_candidates=max_candidates,
        )
    except Exception as exc:
        mlb = {
            "status": "FAILED",
            "attempted": 0,
            "hydrated": 0,
            "persisted": 0,
            "held": 0,
            "snapshot_write_failed": 0,
            "blockers": [],
            "error_type": type(exc).__name__,
        }
    try:
        nba = acquire_daily_nba_points_snapshots(
            db=db,
            requested_date=requested_date,
            timezone_name=timezone_name,
            max_candidates=min(max_candidates, 8),
        )
    except Exception as exc:
        nba = {
            "status": "FAILED",
            "attempted": 0,
            "persisted": 0,
            "held": 0,
            "blockers": [],
            "error_type": type(exc).__name__,
        }

    mlb_blockers = list(mlb.get("blockers") or [])
    nba_blockers = list(nba.get("blockers") or [])
    attempted = int(mlb.get("attempted") or 0) + int(nba.get("attempted") or 0)
    hydrated = int(mlb.get("hydrated") or 0) + int(nba.get("persisted") or 0)
    persisted = int(mlb.get("persisted") or 0) + int(nba.get("persisted") or 0)
    held = int(mlb.get("held") or 0) + int(nba.get("held") or 0)
    write_failed = int(mlb.get("snapshot_write_failed") or 0) + sum(
        1 for item in nba_blockers
        if str(item.get("code") or "").startswith("NBA_POINTS_SNAPSHOT_")
        and str(item.get("code") or "").endswith(("_FAILED", "_UNAVAILABLE"))
    )
    statuses = {str(mlb.get("status") or ""), str(nba.get("status") or "")}
    status = "FAILED" if statuses == {"FAILED"} else ("PARTIAL" if "FAILED" in statuses else "OK")
    return {
        "status": status,
        "attempted": attempted,
        "hydrated": hydrated,
        "persisted": persisted,
        "held": held,
        "snapshot_write_failed": write_failed,
        "blockers": mlb_blockers + nba_blockers,
        "by_sport": {"MLB": mlb, "NBA_POINTS": nba},
        "can_execute": False,
    }


async def run_prop_evidence_acquisition_loop(
    *,
    db_client_fn: Callable[[], Any],
    logger: logging.Logger,
    interval_seconds: int = 3600,
    max_candidates: int = 60,
    forward_days: int = 2,
    timezone_name: str = "America/Chicago",
    initial_delay_seconds: int = 15,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:
    """Continuously seed canonical pregame prop evidence for the cohort.

    Each synchronous acquisition pass runs in a worker thread so it cannot block
    the FastAPI event loop. A failed date never aborts the remaining dates and
    never terminates the production API. Cancellation propagates cleanly.
    """
    interval_seconds = max(300, int(interval_seconds))
    max_candidates = max(1, min(int(max_candidates), 200))
    forward_days = max(1, min(int(forward_days), 3))
    initial_delay_seconds = max(0, int(initial_delay_seconds))

    if initial_delay_seconds:
        await asyncio.sleep(initial_delay_seconds)

    while True:
        for requested_date in slate_dates(now_fn(), timezone_name, forward_days):
            try:
                result = await asyncio.to_thread(
                    _acquire_once,
                    db_client_fn=db_client_fn,
                    requested_date=requested_date,
                    timezone_name=timezone_name,
                    max_candidates=max_candidates,
                )
                logger.warning(
                    "WOW_PROP_EVIDENCE_ACQUISITION status=%s slate_date=%s attempted=%s hydrated=%s persisted=%s held=%s write_failed=%s blockers=%s can_execute=false",
                    result.get("status"),
                    requested_date,
                    result.get("attempted"),
                    result.get("hydrated"),
                    result.get("persisted"),
                    result.get("held"),
                    result.get("snapshot_write_failed"),
                    len(result.get("blockers") or []),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "WOW_PROP_EVIDENCE_ACQUISITION status=FAILED slate_date=%s error_type=%s can_execute=false",
                    requested_date,
                    type(exc).__name__,
                )
        await asyncio.sleep(interval_seconds)
