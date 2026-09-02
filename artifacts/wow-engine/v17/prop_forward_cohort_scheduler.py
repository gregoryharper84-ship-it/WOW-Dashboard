"""In-process scheduler for V17 prop forward calibration cohort capture.

The accepted production web service already owns the governed Supabase and
scorer environment. Running this loop there avoids duplicating credentials into
a second service. Capture remains idempotent, non-executable, and does not fit
or promote a calibrator.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from v17.prop_forward_cohort_runtime import PropForwardCohortRequest, run_prop_forward_cohort


async def run_prop_forward_cohort_loop(
    *,
    db_client_fn: Callable[[], Any],
    market_api: Any,
    logger: logging.Logger,
    interval_seconds: int = 900,
    max_snapshots: int = 100,
    initial_delay_seconds: int = 5,
) -> None:
    """Continuously capture governed pregame forecasts for calibration.

    One failed pass never promotes a model or terminates the production API.
    Cancellation is propagated so application shutdown remains clean.
    """
    interval_seconds = max(60, int(interval_seconds))
    max_snapshots = max(1, min(int(max_snapshots), 200))
    initial_delay_seconds = max(0, int(initial_delay_seconds))

    if initial_delay_seconds:
        await asyncio.sleep(initial_delay_seconds)

    while True:
        try:
            result = run_prop_forward_cohort(
                PropForwardCohortRequest(max_snapshots=max_snapshots),
                db=db_client_fn(),
                market_api=market_api,
            )
            readiness = result.get("calibration_readiness") or {}
            logger.warning(
                "WOW_PROP_FORWARD_COHORT run_status=%s snapshots=%s captured=%s forward_prediction_n=%s forward_settled_n=%s readiness=%s calibrator_fit_performed=false can_execute=false",
                result.get("run_status"),
                result.get("snapshots_considered"),
                result.get("captured_forward_predictions"),
                readiness.get("forward_prediction_n"),
                readiness.get("forward_settled_n"),
                readiness.get("status"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "WOW_PROP_FORWARD_COHORT run_status=FAILED error_type=%s calibrator_fit_performed=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds)
