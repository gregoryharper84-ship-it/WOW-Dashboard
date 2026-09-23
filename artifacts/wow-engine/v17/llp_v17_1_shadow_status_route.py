"""Authenticated activation status for the LLP V17.1 shadow observer.

This route exists whenever the V17 production host is active, even when Core
Intelligence or shadow automation is deliberately disabled. It lets scheduled
GitHub automation distinguish a configured-off feature from an active feature
whose internal routes are unexpectedly missing.

The status route is diagnostic only. It cannot mutate production state, promote
models, publish probability, rank rows, or authorize execution.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency

CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
PRODUCTION_MUTATION_ALLOWED = False
_REQUIRED_ROUTES = frozenset({
    "/internal/v17/llp/shadow/capture",
    "/internal/v17/llp/shadow/grade",
    "/internal/v17/llp/shadow/scorecard",
})


def shadow_automation_status(app: FastAPI) -> dict[str, Any]:
    core_configured = os.getenv("WOW_V17_CORE_INTELLIGENCE_ACTIVE", "0") == "1"
    shadow_configured = os.getenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", "0") == "1"
    paths = {getattr(route, "path", "") for route in app.routes}
    missing_routes = sorted(_REQUIRED_ROUTES.difference(paths))
    routes_present = not missing_routes

    if not core_configured:
        status = "INACTIVE_CORE_INTELLIGENCE_DISABLED"
    elif not shadow_configured:
        status = "INACTIVE_SHADOW_AUTOMATION_DISABLED"
    elif routes_present:
        status = "ACTIVE"
    else:
        status = "MISCONFIGURED_ROUTE_MISSING"

    return {
        "status": status,
        "core_intelligence_configured": core_configured,
        "shadow_automation_configured": shadow_configured,
        "routes_expected": bool(core_configured and shadow_configured),
        "routes_present": routes_present,
        "missing_routes": missing_routes,
        "serving_mode": "INTERNAL_SHADOW_AUTOMATION_STATUS",
        "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
        "production_mutation_allowed": PRODUCTION_MUTATION_ALLOWED,
        "probability_publishable": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": CAN_EXECUTE,
    }


def install_llp_shadow_status_route(
    app: FastAPI,
    *,
    existing_auth_dependency: Any,
) -> bool:
    if getattr(app.state, "v17_llp_shadow_status_route_installed", False):
        return True

    combined_auth = scout_route_auth_dependency(existing_auth_dependency)

    @app.get(
        "/internal/v17/llp/shadow/status",
        operation_id="getWowV17LLPShadowAutomationStatusInternal",
        dependencies=[combined_auth],
    )
    def status():
        return shadow_automation_status(app)

    app.state.v17_llp_shadow_status_route_installed = True
    return True


__all__ = [
    "AUTOMATIC_PROMOTION_ALLOWED",
    "CAN_EXECUTE",
    "PRODUCTION_MUTATION_ALLOWED",
    "install_llp_shadow_status_route",
    "shadow_automation_status",
]
