"""OIDC-protected spread certification evidence replay surface.

This route executes historical close-proxy evaluation only. It cannot certify,
promote, register, publish, rank, or execute a spread/run-line selection.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from v17.spread_certification_replay import run_nflverse_close_proxy_replay
from v17.spread_espn_close_proxy_source import run_wnba_espn_close_proxy_replay
from v17.spread_margin_challenger import SpreadChallengerUnavailable

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PRODUCTION_REGISTRY_MUTATED = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"


class SpreadCertificationReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: Literal["NFL", "WNBA"]
    min_rows: int = Field(default=300, ge=100, le=25000)
    ridge_alpha: float = Field(default=4.0, gt=0.0, le=100.0)


def _governance() -> dict[str, Any]:
    return {
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "production_registry_mutated": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


def execute_spread_certification_replay(
    db: Any,
    request: SpreadCertificationReplayRequest,
) -> dict[str, Any]:
    try:
        if request.sport == "NFL":
            result = run_nflverse_close_proxy_replay(
                client=db,
                min_rows=request.min_rows,
                ridge_alpha=request.ridge_alpha,
            )
        else:
            result = run_wnba_espn_close_proxy_replay(
                client=db,
                min_rows=request.min_rows,
                ridge_alpha=request.ridge_alpha,
            )
    except SpreadChallengerUnavailable as exc:
        return {
            "status": "BLOCKED",
            "code": exc.code,
            "sport": request.sport,
            "detail": str(exc),
            "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
            **_governance(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "BLOCKED",
            "code": "SPREAD_CERTIFICATION_REPLAY_RUNTIME_FAILED",
            "sport": request.sport,
            "error_type": type(exc).__name__,
            "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
            **_governance(),
        }
    return {
        **result,
        "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
        **_governance(),
    }


def install_spread_certification_replay_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/spread-certification-replay"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17SpreadCertificationReplay",
    )
    def run_replay(request: SpreadCertificationReplayRequest) -> dict[str, Any]:
        return execute_spread_certification_replay(db_client_fn(), request)


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "GLOBAL_TERMINAL_REDUCER",
    "PROBABILITY_PUBLISHABLE",
    "SpreadCertificationReplayRequest",
    "execute_spread_certification_replay",
    "install_spread_certification_replay_route",
]
