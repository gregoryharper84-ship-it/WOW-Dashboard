from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable

from fastapi import FastAPI

from .shadow_cohort import run_shadow_cohort_loop


_logger = logging.getLogger("wow.kalshi_weather_v2.empirical_cohort")
_tasks: set[asyncio.Task] = set()


def install_empirical_cohort_scheduler(
    app: FastAPI,
    *,
    db_client_fn: Callable[[], object],
) -> None:
    """Install one fail-closed in-process empirical shadow collector.

    This scheduler only acquires/persists research evidence and settlement
    outcomes. It cannot promote capability state, publish a probability, rank a
    contract, or execute a trade.
    """
    if getattr(app.state, "kalshi_weather_empirical_scheduler_installed", False):
        return
    app.state.kalshi_weather_empirical_scheduler_installed = True

    @app.on_event("startup")
    async def _schedule_empirical_cohort() -> None:
        if os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED", "0") != "1":
            _logger.warning(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=DISABLED probability_publishable=false can_execute=false"
            )
            return
        try:
            interval_seconds = int(
                os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_INTERVAL_SECONDS", "900")
            )
        except ValueError:
            interval_seconds = 900
        task = asyncio.create_task(
            run_shadow_cohort_loop(
                db_client_fn=db_client_fn,
                logger=_logger,
                interval_seconds=interval_seconds,
            )
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
