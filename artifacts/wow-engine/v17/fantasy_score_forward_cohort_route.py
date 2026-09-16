"""Authenticated V17 route for Fantasy Score forward-evidence capture.

The forward collector remains manual/on-demand until real frozen Fantasy Score
candidate artifacts and hydrated snapshots exist.  This installer also places a
narrow evidence-only scorer in front of the already-composed production prop
boundary.  Non-Fantasy requests delegate byte-for-byte to the scorer that was
present before this installer ran.  If a Fantasy route later gains an exact
certified production artifact, that production scorer also takes precedence over
the research bridge.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, Header

from v17.fantasy_score_candidate_bridge import (
    is_fantasy_score_request,
    score_fantasy_candidate_research,
)
from v17.fantasy_score_forward_cohort_runtime import (
    FantasyScoreForwardCohortRequest,
    run_fantasy_score_forward_cohort,
)
from v17.fantasy_score_forward_cohort_schema_repair import (
    install_fantasy_score_forward_schema_repair,
)

_BRIDGE_STATE_KEY = "wow_fantasy_score_candidate_runtime_bridge_installed"


def install_fantasy_score_candidate_runtime_bridge(
    app: FastAPI,
    *,
    auth_dependency: Any,
    market_api: Any,
) -> bool:
    """Install the evidence-only Fantasy branch after the final prop wrapper.

    The captured scorer remains authoritative for every non-Fantasy request and
    for any Fantasy request whose exact route has since become certified. That
    keeps this research bridge incapable of shadowing future production promotion.
    """
    if getattr(app.state, _BRIDGE_STATE_KEY, False):
        return True

    captured_score_prop = market_api.score_prop

    def score_prop_with_fantasy_candidate(
        req: market_api.ScorePropRequest,
        x_wow_model_identity: Optional[str] = None,
    ) -> dict[str, Any]:
        model_identity = market_api.prod._reject_llp_prop_identity(x_wow_model_identity)
        if is_fantasy_score_request(req):
            certified_resolver = getattr(market_api, "_prop_route_artifact", None)
            if callable(certified_resolver):
                certified = certified_resolver(req.sport, req.stat_type)
                if isinstance(certified, dict) and certified.get("ok") is True:
                    return captured_score_prop(req, x_wow_model_identity)
            return score_fantasy_candidate_research(
                market_api,
                req,
                model_identity=model_identity,
            )
        return captured_score_prop(req, x_wow_model_identity)

    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == "/score-prop"
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]

    @app.post(
        "/score-prop",
        dependencies=[auth_dependency],
        operation_id="scoreWowProp",
    )
    def score_prop_with_fantasy_candidate_route(
        req: market_api.ScorePropRequest,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        return score_prop_with_fantasy_candidate(req, x_wow_model_identity)

    market_api.score_prop = score_prop_with_fantasy_candidate
    setattr(app.state, _BRIDGE_STATE_KEY, True)
    return True


def install_fantasy_score_forward_cohort_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
) -> None:
    # PR #458 selected two columns that are not part of the live evidence table.
    # Repair that exact selector before any request can enter the collector.
    install_fantasy_score_forward_schema_repair()

    # api_ncaaf_acceptance installs the final calibration/publication wrapper
    # before daily/forward-cohort routes.  Install the Fantasy candidate branch
    # here so it composes outside that wrapper instead of bypassing it.
    install_fantasy_score_candidate_runtime_bridge(
        app,
        auth_dependency=auth_dependency,
        market_api=market_api,
    )

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


__all__ = [
    "install_fantasy_score_candidate_runtime_bridge",
    "install_fantasy_score_forward_cohort_route",
]
