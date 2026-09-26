"""OIDC-protected current-slate NCAAF spread shadow scoring route."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from v17.spread_forward_shadow import run_ncaaf_forward_shadow
from v17.spread_margin_challenger import SpreadChallengerUnavailable

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True


class SpreadForwardShadowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: Literal["NCAAF"] = "NCAAF"
    event_id: str = Field(min_length=1, max_length=128)
    event_start_time: str = Field(min_length=10, max_length=64)
    home_team: str = Field(min_length=1, max_length=160)
    away_team: str = Field(min_length=1, max_length=160)
    home_spread: float = Field(gt=-100.0, lt=100.0)
    season: int | None = Field(default=None, ge=2000, le=2100)


def _governance() -> dict[str, Any]:
    return {
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "database_mutated": False,
        "production_registry_mutated": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "can_execute": CAN_EXECUTE,
    }


def execute_spread_forward_shadow(db: Any, request: SpreadForwardShadowRequest) -> dict[str, Any]:
    try:
        result = run_ncaaf_forward_shadow(
            db,
            event_id=request.event_id,
            event_start_time=request.event_start_time,
            home_team=request.home_team,
            away_team=request.away_team,
            home_spread=request.home_spread,
            season=request.season,
        )
    except SpreadChallengerUnavailable as exc:
        return {
            "status": "BLOCKED",
            "code": exc.code,
            "sport": request.sport,
            "event_id": request.event_id,
            "detail": str(exc),
            **_governance(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "BLOCKED",
            "code": "SPREAD_FORWARD_SHADOW_RUNTIME_FAILED",
            "sport": request.sport,
            "event_id": request.event_id,
            "error_type": type(exc).__name__,
            **_governance(),
        }
    return {**result, **_governance()}


def install_spread_forward_shadow_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any) -> None:
    path = "/internal/v17/spread-forward-shadow"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="scoreWowV17SpreadForwardShadow",
    )
    def score_spread_forward_shadow(request: SpreadForwardShadowRequest) -> dict[str, Any]:
        return execute_spread_forward_shadow(db_client_fn(), request)


__all__ = [
    "SpreadForwardShadowRequest",
    "execute_spread_forward_shadow",
    "install_spread_forward_shadow_route",
]
