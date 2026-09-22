"""Internal OIDC-protected automation routes for LLP V17.1 shadow learning.

These endpoints are intentionally separate from Custom GPT Actions. They may be
called only through the existing WOW Action-key auth seam or an explicitly
allowlisted GitHub Actions OIDC workflow. They never score sporting events,
change production probabilities/rankings, promote a challenger, or execute a
wager.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Query

from github_actions_oidc import scout_route_auth_dependency
from v17.llp_v17_1_shadow_runtime import (
    CAN_EXECUTE,
    DEFAULT_MAX_OUTCOMES,
    DEFAULT_MAX_PREDICTIONS,
    capture_event_prediction_shadows,
    grade_settled_event_shadows,
)
from v17.llp_v17_1_shadow_scorecard_runtime import (
    DEFAULT_MAX_GRADES,
    shadow_scorecard,
)

SERVING_MODE = "INTERNAL_SHADOW_AUTOMATION"
AUTOMATIC_PROMOTION_ALLOWED = False
PRODUCTION_MUTATION_ALLOWED = False


def install_llp_v17_1_shadow_internal_routes(
    app: FastAPI,
    *,
    get_client_fn: Callable[[], Any],
    existing_auth_dependency: Any,
) -> bool:
    """Mount isolated internal capture/grade/evaluation endpoints."""
    if getattr(app.state, "v17_llp_shadow_internal_routes_installed", False):
        return True

    combined_auth = scout_route_auth_dependency(existing_auth_dependency)
    dependencies = [combined_auth]

    @app.post(
        "/internal/v17/llp/shadow/capture",
        operation_id="captureWowV17LLPSharpnessShadowInternal",
        dependencies=dependencies,
    )
    def capture(
        max_predictions: int = Query(default=DEFAULT_MAX_PREDICTIONS, ge=1, le=5000),
    ):
        result = capture_event_prediction_shadows(
            get_client_fn(),
            max_predictions=max_predictions,
        )
        return {
            **result,
            "serving_mode": SERVING_MODE,
            "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
            "production_mutation_allowed": PRODUCTION_MUTATION_ALLOWED,
            "can_execute": CAN_EXECUTE,
        }

    @app.post(
        "/internal/v17/llp/shadow/grade",
        operation_id="gradeWowV17LLPSharpnessShadowInternal",
        dependencies=dependencies,
    )
    def grade(
        max_outcomes: int = Query(default=DEFAULT_MAX_OUTCOMES, ge=1, le=5000),
    ):
        result = grade_settled_event_shadows(
            get_client_fn(),
            max_outcomes=max_outcomes,
        )
        return {
            **result,
            "serving_mode": SERVING_MODE,
            "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
            "production_mutation_allowed": PRODUCTION_MUTATION_ALLOWED,
            "can_execute": CAN_EXECUTE,
        }

    @app.get(
        "/internal/v17/llp/shadow/scorecard",
        operation_id="getWowV17LLPSharpnessShadowScorecardInternal",
        dependencies=dependencies,
    )
    def scorecard(
        max_grades: int = Query(default=DEFAULT_MAX_GRADES, ge=1, le=20000),
        min_cohort_events: int = Query(default=30, ge=1, le=10000),
    ):
        result = shadow_scorecard(
            get_client_fn(),
            max_grades=max_grades,
            min_cohort_events=min_cohort_events,
        )
        return {
            **result,
            "serving_mode": SERVING_MODE,
            "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
            "production_mutation_allowed": PRODUCTION_MUTATION_ALLOWED,
            "can_execute": CAN_EXECUTE,
        }

    app.state.v17_llp_shadow_internal_routes_installed = True
    return True


__all__ = [
    "AUTOMATIC_PROMOTION_ALLOWED",
    "CAN_EXECUTE",
    "PRODUCTION_MUTATION_ALLOWED",
    "SERVING_MODE",
    "install_llp_v17_1_shadow_internal_routes",
]
