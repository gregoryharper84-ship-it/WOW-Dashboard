from __future__ import annotations

import asyncio
import hmac
import logging
import os
from dataclasses import asdict
from typing import Callable

from fastapi import FastAPI, Header, HTTPException

from .operational_cycle import VERIFIED_HOURLY_TARGETS
from .shadow_cohort import HourlyCohortTarget, run_hourly_shadow_cohort_once


_logger = logging.getLogger("wow.kalshi_weather_v2.empirical_cohort")
_tasks: set[asyncio.Task] = set()


def _automated_shadow_targets() -> tuple[HourlyCohortTarget, ...]:
    """Mirror governed operational targets into the bounded empirical collector.

    The automated web-process loop is intentionally calibration-first: one
    threshold sibling per exact weather target/lead bucket is sufficient to
    produce an unbiased temperature residual. Repeating identical NWS and
    Open-Meteo acquisitions for every threshold sibling is deferred to explicit
    on-demand analysis until shared-evidence sibling expansion is implemented.
    """
    return tuple(
        HourlyCohortTarget(
            index_city=target.index_city,
            expected_location=target.expected_location,
            forecast_latitude=target.forecast_latitude,
            forecast_longitude=target.forecast_longitude,
            forecast_reference_note=target.forecast_reference_note,
            enabled=target.enabled,
        )
        for target in VERIFIED_HOURLY_TARGETS
    )


def _run_bounded_shadow_cycle(*, db_client_fn: Callable[[], object]):
    return run_hourly_shadow_cohort_once(
        db_client_fn=db_client_fn,
        targets=_automated_shadow_targets(),
    )


def install_empirical_cohort_scheduler(
    app: FastAPI,
    *,
    db_client_fn: Callable[[], object],
) -> None:
    """Install bounded automated Weather calibration collection plus trigger.

    The automatic web-process workload deliberately captures one representative
    threshold sibling per exact weather target/lead bucket. That preserves
    unbiased calibration sampling while preventing duplicate provider work from
    destabilizing the shared web service. Exact sibling contracts remain
    available through the authenticated on-demand Weather routes.

    Nothing here can promote model capability, publish an uncertified
    probability, or execute a trade.
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
        result = _run_bounded_shadow_cycle(db_client_fn=db_client_fn)
        return {
            **asdict(result),
            "trigger": "EXTERNAL_LEAST_PRIVILEGE_SCHEDULER",
            "collection_mode": "BOUNDED_REPRESENTATIVE_SHADOW",
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
        interval_seconds = max(300, interval_seconds)
        task = asyncio.create_task(
            _run_bounded_shadow_loop(
                db_client_fn=db_client_fn,
                interval_seconds=interval_seconds,
            )
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _run_bounded_shadow_loop(
    *,
    db_client_fn: Callable[[], object],
    interval_seconds: int,
) -> None:
    _logger.warning(
        "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=STARTED collection_mode=BOUNDED_REPRESENTATIVE_SHADOW interval_seconds=%s probability_publishable=false can_execute=false",
        interval_seconds,
    )
    while True:
        try:
            result = await asyncio.to_thread(
                _run_bounded_shadow_cycle,
                db_client_fn=db_client_fn,
            )
            _logger.warning(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=%s collection_mode=BOUNDED_REPRESENTATIVE_SHADOW targets=%s discovered=%s captured=%s skipped=%s supplemental=%s settled=%s capture_failures=%s supplemental_failures=%s settlement_failures=%s failure_samples=%s probability_publishable=false can_execute=false",
                result.status,
                result.targets_checked,
                result.contracts_discovered,
                result.samples_captured,
                result.samples_skipped_existing,
                result.supplemental_snapshots_captured,
                result.predictions_settled,
                len(result.capture_failures),
                len(result.supplemental_failures),
                len(result.settlement_failures),
                list(result.capture_failures[:6]),
            )
        except Exception as exc:
            _logger.exception(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=FAILED collection_mode=BOUNDED_REPRESENTATIVE_SHADOW error_type=%s probability_publishable=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds)
