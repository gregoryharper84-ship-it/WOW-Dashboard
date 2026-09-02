"""Opt-in in-process scheduler for MLB 1IP final refresh.

This is a deployment/runtime adapter around mlb_1ip_final_refresh_job.run_once.
It reuses the already-configured production Supabase client from the web
process so no service-role credential must be copied into a second service.

The scheduler is disabled unless WOW_MLB_1IP_FINAL_REFRESH_ENABLED=1.
It does not change probability publication or wager execution authority.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

from mlb_1ip_final_refresh_job import run_once
from mlb_1ip_live_self_acceptance import run_live_self_acceptance

CAN_EXECUTE = False
DEFAULT_INTERVAL_SECONDS = 300


async def run_refresh_loop(
    *,
    db_client_fn: Callable[[], Any],
    logger: logging.Logger,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """Run governed MLB 1IP refresh passes until the task is cancelled."""
    interval = max(60, int(interval_seconds))
    logger.warning(
        "WOW_MLB_1IP_FINAL_REFRESH status=STARTED interval_seconds=%s probability_publishable=false can_execute=false",
        interval,
    )
    if os.getenv("WOW_MLB_1IP_LIVE_SELF_ACCEPTANCE", "0") == "1":
        await run_live_self_acceptance(logger)
    while True:
        try:
            result = await asyncio.to_thread(run_once, client=db_client_fn())
            logger.warning(
                "WOW_MLB_1IP_FINAL_REFRESH status=PASS seen=%s waiting=%s rerun_completed=%s purged=%s expired=%s failed=%s probability_publishable=false can_execute=false",
                result.get("seen", 0),
                result.get("waiting", 0),
                result.get("rerun_completed", 0),
                result.get("purged", 0),
                result.get("expired", 0),
                result.get("failed", 0),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "WOW_MLB_1IP_FINAL_REFRESH status=FAILED error_type=%s probability_publishable=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval)
