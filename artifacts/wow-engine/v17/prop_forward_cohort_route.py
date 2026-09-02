"""Authenticated V17 route installer for server-owned prop forward cohort capture."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from v17.prop_forward_cohort_runtime import PropForwardCohortRequest, run_prop_forward_cohort


def install_prop_forward_cohort_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
) -> None:
    if any(getattr(route, "path", None) == "/v17/prop-forward-cohort-run" for route in app.router.routes):
        return

    @app.post(
        "/v17/prop-forward-cohort-run",
        dependencies=[auth_dependency],
        operation_id="runWowV17PropForwardCohort",
    )
    def prop_forward_cohort_run(req: PropForwardCohortRequest):
        return run_prop_forward_cohort(req, db=db_client_fn(), market_api=market_api)
