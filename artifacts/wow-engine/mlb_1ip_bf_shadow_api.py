"""Internal HTTP surface for MLB 1IP BF forward-shadow automation.

The run endpoint uses a dedicated non-user-facing scheduler token. The health
endpoint reuses the normal WOW Action authentication dependency. Neither route
publishes sporting probabilities for betting use and neither can execute.
"""
from __future__ import annotations

import hmac
import os
from typing import Any, Callable, Optional

from fastapi import Header, HTTPException

from mlb_1ip_bf_forward_shadow import EXPECTED_ARTIFACT_CHECKSUM, run_once

CAN_EXECUTE = False
RUN_PATH = "/internal/mlb/1ip-bf-forward-shadow/run"
HEALTH_PATH = "/internal/mlb/1ip-bf-forward-shadow/health"


def _scheduler_authorized(provided: Optional[str]) -> bool:
    expected = os.getenv("WOW_MLB_1IP_BF_SHADOW_TOKEN", "")
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


def install_mlb_1ip_bf_shadow_routes(
    app: Any,
    *,
    auth_dependency: Any,
    db_client_fn: Callable[[], Any],
) -> None:
    existing = {getattr(route, "path", None) for route in app.router.routes}

    if RUN_PATH not in existing:
        @app.post(RUN_PATH, include_in_schema=False)
        def run_bf_forward_shadow(
            x_wow_bf_shadow_token: Optional[str] = Header(default=None, alias="X-WOW-BF-Shadow-Token"),
        ) -> dict[str, Any]:
            if not _scheduler_authorized(x_wow_bf_shadow_token):
                raise HTTPException(
                    status_code=401,
                    detail={
                        "code": "MLB_1IP_BF_SHADOW_SCHEDULER_UNAUTHORIZED",
                        "probability_publishable": False,
                        "can_execute": False,
                    },
                )
            try:
                return run_once(client=db_client_fn())
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "MLB_1IP_BF_FORWARD_SHADOW_RUN_FAILED",
                        "error_type": type(exc).__name__,
                        "probability_publishable": False,
                        "can_execute": False,
                    },
                ) from exc

    if HEALTH_PATH not in existing:
        @app.get(
            HEALTH_PATH,
            dependencies=[auth_dependency],
            operation_id="getMlb1ipBfShadowHealth",
        )
        def get_bf_forward_shadow_health() -> dict[str, Any]:
            try:
                response = db_client_fn().rpc(
                    "wow_mlb_1ip_bf_shadow_health",
                    {"p_artifact_checksum": EXPECTED_ARTIFACT_CHECKSUM},
                ).execute()
                health = response.data if isinstance(response.data, dict) else {}
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "MLB_1IP_BF_SHADOW_HEALTH_UNAVAILABLE",
                        "error_type": type(exc).__name__,
                        "probability_publishable": False,
                        "can_execute": False,
                    },
                ) from exc
            return {
                "status": "FORWARD_SHADOW_ACTIVE",
                "health": health,
                "certification_ready": bool(health.get("forward_sample_complete")) and False,
                "promotion_requires_independent_review": True,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
