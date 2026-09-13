from __future__ import annotations

import asyncio
import hmac
import logging
import os
from dataclasses import asdict
from typing import Callable

from fastapi import FastAPI, Header, HTTPException

from .operational_cycle import run_weather_operational_cycle_once


_logger = logging.getLogger("wow.kalshi_weather_v2.empirical_cohort")
_tasks: set[asyncio.Task] = set()


def install_empirical_cohort_scheduler(
    app: FastAPI,
    *,
    db_client_fn: Callable[[], object],
) -> None:
    """Install automated Weather collection plus least-privilege wake route.

    The operational cycle performs exact contract discovery, immutable forecast
    capture, official Weather Index trajectory capture, market-only quote/fee
    refresh, and automatic settlement. It cannot promote model capability,
    publish an uncertified probability, or execute a trade.
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
        result = run_weather_operational_cycle_once(db_client_fn=db_client_fn)
        return {
            **asdict(result),
            "trigger": "EXTERNAL_LEAST_PRIVILEGE_SCHEDULER",
            "probability_publishable": False,
            "can_execute": False,
        }

    @app.on_event("startup")
    async def _schedule_empirical_cohort() -> None:
        if os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED", "0") != "1":
            _logger.warning(
                "WOW_KALSHI_WEATHER_OPERATIONAL_CYCLE status=DISABLED probability_publishable=false can_execute=false"
            )
            return
        try:
            interval_seconds = int(
                os.getenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_INTERVAL_SECONDS", "900")
            )
        except ValueError:
            interval_seconds = 900
        interval_seconds = max(300, interval_seconds)
        task = asyncio.create_task(
            _run_operational_loop(
                db_client_fn=db_client_fn,
                interval_seconds=interval_seconds,
            )
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _run_operational_loop(
    *,
    db_client_fn: Callable[[], object],
    interval_seconds: int,
) -> None:
    _logger.warning(
        "WOW_KALSHI_WEATHER_OPERATIONAL_CYCLE status=STARTED interval_seconds=%s probability_publishable=false can_execute=false",
        interval_seconds,
    )
    while True:
        try:
            result = await asyncio.to_thread(
                run_weather_operational_cycle_once,
                db_client_fn=db_client_fn,
            )
            first_rejections = [
                reason
                for diagnostic in result.discovery_diagnostics
                for reason in diagnostic.rejection_reasons[:3]
            ][:8]
            _logger.warning(
                "WOW_KALSHI_WEATHER_OPERATIONAL_CYCLE status=%s targets=%s markets_seen=%s discovered=%s captured=%s skipped=%s supplemental=%s trajectories=%s market_refresh=%s settled=%s capture_failures=%s market_failures=%s rejection_samples=%s probability_publishable=false can_execute=false",
                result.status,
                result.targets_checked,
                result.markets_seen,
                result.contracts_discovered,
                result.predictions_captured,
                result.predictions_skipped_existing,
                result.supplemental_snapshots_captured,
                result.official_trajectory_snapshots_captured,
                result.market_snapshots_refreshed,
                result.predictions_settled,
                len(result.capture_failures),
                len(result.market_monitor_failures),
                first_rejections,
            )
        except Exception as exc:
            _logger.exception(
                "WOW_KALSHI_WEATHER_OPERATIONAL_CYCLE status=FAILED error_type=%s probability_publishable=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds)
