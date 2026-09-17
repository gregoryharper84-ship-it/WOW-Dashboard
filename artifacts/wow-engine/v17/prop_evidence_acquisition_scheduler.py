"""Scheduled V17 prop evidence acquisition for forward calibration cohorts.

The production MLB prop producer and the Fantasy Score candidate producer are
kept authority-separated.  The production loop keeps its existing behavior.
When ``WOW_FANTASY_SCORE_EVIDENCE_ACQUISITION_ENABLED=1`` is explicitly set,
a second pass freezes MLB pitcher Fantasy Score candidate snapshots from the
same official probable-pitcher slate.  Those rows remain candidate-only,
non-publishable, non-rankable, and non-executable.

WOW-PATCH-2026-09-12-V17-PROP-EVIDENCE-ACQUISITION-SCHEDULE
WOW-PATCH-2026-09-16-V17-FANTASY-SCORE-FORWARD-EVIDENCE
can_execute=false unconditionally.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from v17.daily_prop_acquisition import acquire_daily_prop_snapshots
from v17.mlb_pitcher_fantasy_score_evidence_acquisition import (
    acquire_mlb_pitcher_fantasy_score_snapshots,
)

CAN_EXECUTE = False


def slate_dates(now: datetime, timezone_name: str, forward_days: int) -> list[str]:
    """Slate dates to acquire, starting with the current local date."""
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
    return acquire_daily_prop_snapshots(
        db=db_client_fn(),
        requested_date=requested_date,
        requested_timezone=timezone_name,
        max_candidates=max_candidates,
    )


def _acquire_fantasy_once(
    *,
    db_client_fn: Callable[[], Any],
    requested_date: str,
    timezone_name: str,
    max_candidates: int,
) -> dict[str, Any]:
    return acquire_mlb_pitcher_fantasy_score_snapshots(
        db=db_client_fn(),
        requested_date=requested_date,
        requested_timezone=timezone_name,
        max_candidates=max_candidates,
    )


def _fantasy_acquisition_enabled() -> bool:
    return os.getenv("WOW_FANTASY_SCORE_EVIDENCE_ACQUISITION_ENABLED", "0") == "1"


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
    """Continuously seed canonical pregame prop evidence for forward cohorts.

    Candidate Fantasy Score acquisition is explicitly opt-in and cannot change
    the authority or output of the certified production acquisition pass.
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

            if _fantasy_acquisition_enabled():
                try:
                    fantasy = await asyncio.to_thread(
                        _acquire_fantasy_once,
                        db_client_fn=db_client_fn,
                        requested_date=requested_date,
                        timezone_name=timezone_name,
                        max_candidates=max_candidates,
                    )
                    logger.warning(
                        "WOW_FANTASY_SCORE_EVIDENCE_ACQUISITION status=%s lane=MLB_PITCHER slate_date=%s attempted=%s hydrated=%s persisted=%s held=%s write_failed=%s blockers=%s probability_publishable=false rank_eligible=false can_execute=false",
                        fantasy.get("status"),
                        requested_date,
                        fantasy.get("attempted"),
                        fantasy.get("hydrated"),
                        fantasy.get("persisted"),
                        fantasy.get("held"),
                        fantasy.get("snapshot_write_failed"),
                        len(fantasy.get("blockers") or []),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "WOW_FANTASY_SCORE_EVIDENCE_ACQUISITION status=FAILED lane=MLB_PITCHER slate_date=%s error_type=%s probability_publishable=false rank_eligible=false can_execute=false",
                        requested_date,
                        type(exc).__name__,
                    )
        await asyncio.sleep(interval_seconds)
