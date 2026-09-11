from __future__ import annotations

import asyncio
import hmac
import logging
import os
from typing import Callable

from fastapi import FastAPI, Header, HTTPException

from .shadow_cohort import run_hourly_shadow_cohort_once, run_shadow_cohort_loop


_logger = logging.getLogger("wow.kalshi_weather_v2.empirical_cohort")
_tasks: set[asyncio.Task] = set()


def install_empirical_cohort_scheduler(
    app: FastAPI,
    *,
    db_client_fn: Callable[[], object],
) -> None:
    """Install fail-closed empirical collection plus a least-privilege wake route.

    The in-process loop gives immediate collection while the web process is
    awake. A separately keyed HTTP trigger exists so an external scheduler can
    wake a free web service without receiving the main WOW Action API key.
    Neither path can promote capability state, publish a probability, rank a
    contract, or execute a trade.
    """
    if getattr(app.state, "kalshi_weather_empirical_scheduler_installed", False):
        return
    app.state.kalshi_weather_empirical_scheduler_installed = True

    @app.post(
        "/internal/kalshi-weather/v17/empirical-cohort/cron-run",
        operation_id="runKalshiWeatherV17EmpiricalCohortCron",
        include_in_schema=False,
    )
    def _cron_run(
        x_wow_weather_cohort_key: str | None = Header(
            default=None,
            alias="X-WOW-Weather-Cohort-Key",
        ),
    ):
        expected = os.getenv("WOW_KALSHI_WEATHER_COHORT_KEY", "")
        supplied = str(x_wow_weather_cohort_key or "")
        if not expected:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "KALSHI_WEATHER_COHORT_TRIGGER_UNCONFIGURED",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "KALSHI_WEATHER_COHORT_TRIGGER_UNAUTHORIZED",
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )
        result = run_hourly_shadow_cohort_once(db_client_fn=db_client_fn)
        return {
            **result.__dict__,
            "trigger": "EXTERNAL_LEAST_PRIVILEGE_SCHEDULER",
            "probability_publishable": False,
            "can_execute": False,
        }

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
