"""Authenticated V17 route for Fantasy Score forward-evidence capture.

The route is intentionally manual/on-demand in this change. Scheduling is not
enabled until the production candidate scorer/artifact bridge is registered and
verified. That prevents an always-on loop from generating repeated held rows while
runtime scoring is still candidate-only.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from v17.fantasy_score_forward_cohort_runtime import (
    FantasyScoreForwardCohortRequest,
    run_fantasy_score_forward_cohort,
)


def install_fantasy_score_forward_cohort_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
) -> None:
    if any(
        getattr(route, "path", None) == "/v17/fantasy-score-forward-cohort-run"
        for route in app.router.routes
    ):
        return

    @app.post(
        "/v17/fantasy-score-forward-cohort-run",
        dependencies=[auth_dependency],
        operation_id="runWowV17FantasyScoreForwardCohort",
    )
    def fantasy_score_forward_cohort_run(req: FantasyScoreForwardCohortRequest):
        return run_fantasy_score_forward_cohort(
            req,
            db=db_client_fn(),
            market_api=market_api,
        )


__all__ = ["install_fantasy_score_forward_cohort_route"]
