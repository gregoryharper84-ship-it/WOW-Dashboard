"""Authenticated V17 route for Fantasy Score and other research candidates.

The forward collector remains available for batch/backfill capture. This installer
also places narrow evidence-only scorers in front of the already-composed production
prop boundary. Non-candidate requests delegate byte-for-byte to the scorer that was
present before this installer ran. If an exact candidate route later gains a certified
production artifact, that production scorer always takes precedence.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, Header

from v17.fantasy_score_candidate_persistence_bridge import (
    is_fantasy_score_request,
    score_fantasy_candidate_research,
)
from v17.fantasy_score_forward_cohort_runtime import (
    FantasyScoreForwardCohortRequest,
    run_fantasy_score_forward_cohort,
)
from v17.fantasy_score_forward_cohort_schema_repair import install_fantasy_score_forward_schema_repair
from v17.mlb_player_doubles_candidate_bridge import (
    is_mlb_player_doubles_candidate_request,
    score_mlb_player_doubles_candidate_research,
)
from v17.nba_scalar_candidate_bridge import is_nba_scalar_candidate_request, score_nba_scalar_candidate_research
from v17.nba_scalar_candidate_registry import install_nba_scalar_candidate_registration_route
from v17.wnba_composite_candidate_bridge import (
    is_wnba_composite_candidate_request,
    score_wnba_composite_candidate_research,
)

_BRIDGE_STATE_KEY = "wow_fantasy_score_candidate_runtime_bridge_installed"


def install_fantasy_score_candidate_runtime_bridge(
    app: FastAPI,
    *,
    auth_dependency: Any,
    market_api: Any,
) -> bool:
    """Install evidence-only fitted-candidate branches after the final prop wrapper.

    The captured scorer remains authoritative for every ordinary request and for any
    candidate request whose exact route has since become certified. That makes these
    research bridges incapable of shadowing a future production promotion.
    """
    if getattr(app.state, _BRIDGE_STATE_KEY, False):
        return True

    captured_score_prop = getattr(market_api, "score_prop", None)
    score_request_model = getattr(market_api, "ScorePropRequest", None)
    if not callable(captured_score_prop) or score_request_model is None:
        return False

    def _certified_route_available(req: Any) -> bool:
        certified_resolver = getattr(market_api, "_prop_route_artifact", None)
        if not callable(certified_resolver):
            return False
        certified = certified_resolver(req.sport, req.stat_type)
        return isinstance(certified, dict) and certified.get("ok") is True

    def score_prop_with_fitted_candidate(
        req: Any,
        x_wow_model_identity: Optional[str] = None,
    ) -> dict[str, Any]:
        model_identity = market_api.prod._reject_llp_prop_identity(x_wow_model_identity)
        if is_mlb_player_doubles_candidate_request(req):
            if _certified_route_available(req):
                return captured_score_prop(req, x_wow_model_identity)
            return score_mlb_player_doubles_candidate_research(market_api, req, model_identity=model_identity)
        if is_fantasy_score_request(req):
            if _certified_route_available(req):
                return captured_score_prop(req, x_wow_model_identity)
            return score_fantasy_candidate_research(market_api, req, model_identity=model_identity)
        if is_wnba_composite_candidate_request(req):
            if _certified_route_available(req):
                return captured_score_prop(req, x_wow_model_identity)
            return score_wnba_composite_candidate_research(market_api, req, model_identity=model_identity)
        if is_nba_scalar_candidate_request(req):
            if _certified_route_available(req):
                return captured_score_prop(req, x_wow_model_identity)
            return score_nba_scalar_candidate_research(market_api, req, model_identity=model_identity)
        return captured_score_prop(req, x_wow_model_identity)

    score_prop_with_fitted_candidate.__annotations__["req"] = score_request_model

    app.router.routes[:] = [
        route for route in app.router.routes
        if not (
            getattr(route, "path", None) == "/score-prop"
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]

    def score_prop_with_fitted_candidate_route(
        req: Any,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        return score_prop_with_fitted_candidate(req, x_wow_model_identity)

    score_prop_with_fitted_candidate_route.__annotations__["req"] = score_request_model
    app.post("/score-prop", dependencies=[auth_dependency], operation_id="scoreWowProp")(
        score_prop_with_fitted_candidate_route
    )

    market_api.score_prop = score_prop_with_fitted_candidate
    setattr(app.state, _BRIDGE_STATE_KEY, True)
    return True


def install_fantasy_score_forward_cohort_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
) -> None:
    install_fantasy_score_forward_schema_repair()

    install_nba_scalar_candidate_registration_route(
        app,
        auth_dependency=auth_dependency,
        db_client_fn=db_client_fn,
    )

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
        return run_fantasy_score_forward_cohort(req, db=db_client_fn(), market_api=market_api)


__all__ = ["install_fantasy_score_candidate_runtime_bridge", "install_fantasy_score_forward_cohort_route"]
