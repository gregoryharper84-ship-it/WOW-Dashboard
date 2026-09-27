"""Canonicalize interactive /score-prop stat aliases before governed routing.

The Pick Request core already canonicalizes host-friendly aliases such as
NFL PASS_YARDS -> PASSING_YARDS and RUSH_YARDS -> RUSHING_YARDS before it asks
for the controlling specialist. The direct /score-prop boundary did not, so an
otherwise registered exact NFL route could incorrectly fail as MODEL_UNAVAILABLE.

This wrapper changes identity/routing labels only. It does not change fitted model
math, calibration, probability values, publication gates, or execution authority.
"""
import inspect
from typing import Any, Optional

from fastapi import Header

import pick_request_runtime_core as pick_core

_STATE_KEY = "wow_v17_prop_stat_alias_boundary_installed"
_STARTUP_KEY = "wow_v17_prop_stat_alias_boundary_scheduled"


def canonical_stat_type(sport: str, stat_type: str) -> str:
    """Reuse the existing Pick Request canonical stat map as single authority."""
    return pick_core._canonical_stat(str(sport or "").upper(), str(stat_type or ""))


def _invoke(endpoint: Any, req: Any, model_identity: Optional[str]) -> Any:
    try:
        parameters = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "x_wow_model_identity" in parameters:
        return endpoint(req, x_wow_model_identity=model_identity)
    return endpoint(req)


def install_score_prop_alias_boundary(app: Any, *, market_api: Any) -> bool:
    if getattr(app.state, _STATE_KEY, False):
        return True

    route = next(
        (
            candidate
            for candidate in app.router.routes
            if getattr(candidate, "path", None) == "/score-prop"
            and "POST" in (getattr(candidate, "methods", set()) or set())
        ),
        None,
    )
    if route is None or not callable(getattr(route, "endpoint", None)):
        return False

    captured = route.endpoint
    dependencies = list(getattr(route, "dependencies", None) or [])
    operation_id = getattr(route, "operation_id", None) or "scoreWowProp"
    app.router.routes[:] = [candidate for candidate in app.router.routes if candidate is not route]

    request_model = market_api.ScorePropRequest

    @app.post(
        "/score-prop",
        dependencies=dependencies,
        operation_id=operation_id,
    )
    def score_prop_alias_bound(
        req: request_model,  # type: ignore[valid-type]
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ) -> Any:
        requested_stat = str(req.stat_type)
        canonical_stat = canonical_stat_type(req.sport, requested_stat)
        prepared = (
            req.model_copy(update={"stat_type": canonical_stat})
            if canonical_stat != requested_stat
            else req
        )
        response = _invoke(captured, prepared, x_wow_model_identity)
        if isinstance(response, dict):
            response["stat_type_resolution"] = {
                "requested_stat_type": requested_stat,
                "canonical_stat_type": canonical_stat,
                "rewritten": canonical_stat != requested_stat,
                "probability_mutated": False,
                "can_execute": False,
            }
            response["can_execute"] = False
        return response

    setattr(app.state, _STATE_KEY, True)
    return True


def schedule_score_prop_alias_boundary(app: Any, *, market_api: Any) -> None:
    if getattr(app.state, _STARTUP_KEY, False):
        return

    @app.on_event("startup")
    async def _install_final_score_prop_alias_boundary() -> None:
        install_score_prop_alias_boundary(app, market_api=market_api)

    setattr(app.state, _STARTUP_KEY, True)


__all__ = [
    "canonical_stat_type",
    "install_score_prop_alias_boundary",
    "schedule_score_prop_alias_boundary",
]
