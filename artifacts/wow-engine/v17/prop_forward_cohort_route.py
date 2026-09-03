"""Authenticated V17 route and scheduler installer for prop forward cohort capture."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import FastAPI

from v17.prop_forward_cohort_market_adapter import ForwardCohortMarketAdapter
from v17.prop_forward_cohort_runtime import PropForwardCohortRequest, run_prop_forward_cohort
from v17.prop_forward_cohort_scheduler import run_prop_forward_cohort_loop


_LOGGER = logging.getLogger("wow.v17.prop_forward_cohort")
_STATE_KEY = "wow_prop_forward_cohort_scheduler_installed"


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _install_scheduler(app: FastAPI, *, db_client_fn: Any, market_api: Any) -> None:
    if os.getenv("WOW_PROP_FORWARD_COHORT_ENABLED", "0") != "1":
        return
    if getattr(app.state, _STATE_KEY, False):
        return
    setattr(app.state, _STATE_KEY, True)

    interval_seconds = _int_env(
        "WOW_PROP_FORWARD_COHORT_INTERVAL_SECONDS", 900, minimum=60, maximum=86400
    )
    max_snapshots = _int_env(
        "WOW_PROP_FORWARD_COHORT_MAX_SNAPSHOTS", 100, minimum=1, maximum=200
    )
    initial_delay_seconds = _int_env(
        "WOW_PROP_FORWARD_COHORT_INITIAL_DELAY_SECONDS", 5, minimum=0, maximum=300
    )

    @app.on_event("startup")
    async def schedule_prop_forward_cohort() -> None:
        task = asyncio.create_task(
            run_prop_forward_cohort_loop(
                db_client_fn=db_client_fn,
                market_api=market_api,
                logger=_LOGGER,
                interval_seconds=interval_seconds,
                max_snapshots=max_snapshots,
                initial_delay_seconds=initial_delay_seconds,
            )
        )
        # Keep a strong application-owned reference so the task cannot be
        # garbage-collected while the service is running.
        tasks = getattr(app.state, "wow_prop_forward_cohort_tasks", None)
        if tasks is None:
            tasks = set()
            app.state.wow_prop_forward_cohort_tasks = tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)


def install_prop_forward_cohort_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
) -> None:
    cohort_market_api = ForwardCohortMarketAdapter(market_api)
    _install_scheduler(app, db_client_fn=db_client_fn, market_api=cohort_market_api)

    if any(getattr(route, "path", None) == "/v17/prop-forward-cohort-run" for route in app.router.routes):
        return

    @app.post(
        "/v17/prop-forward-cohort-run",
        dependencies=[auth_dependency],
        operation_id="runWowV17PropForwardCohort",
    )
    def prop_forward_cohort_run(req: PropForwardCohortRequest):
        return run_prop_forward_cohort(req, db=db_client_fn(), market_api=cohort_market_api)
