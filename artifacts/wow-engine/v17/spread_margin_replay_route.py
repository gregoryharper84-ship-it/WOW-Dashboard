"""OIDC-protected, read-only historical replay surface for spread challengers.

This route is research-only Class C evidence infrastructure. It does not mutate
training data, register a serving specialist, publish a probability, or execute
any wager/market action.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from v17.spread_margin_challenger import SpreadChallengerUnavailable
from v17.spread_margin_replay import run_historical_replay

CAN_EXECUTE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PROBABILITY_PUBLISHABLE = False
DATABASE_MUTATED = False
PRODUCTION_REGISTRY_MUTATED = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"

ReplaySport = Literal["NFL", "NBA", "WNBA", "NCAAF", "NCAAB"]


class SpreadMarginReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: ReplaySport
    min_rows: int = Field(default=300, ge=100, le=25000)
    ridge_alpha: float = Field(default=4.0, gt=0.0, le=100.0)


def _governance_fields() -> dict[str, Any]:
    return {
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "database_mutated": DATABASE_MUTATED,
        "production_registry_mutated": PRODUCTION_REGISTRY_MUTATED,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": CAN_EXECUTE,
    }


def execute_spread_margin_replay(db: Any, request: SpreadMarginReplayRequest) -> dict[str, Any]:
    """Execute one bounded read-only replay and return a compact receipt."""
    try:
        result = run_historical_replay(
            sport=request.sport,
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
            **_governance_fields(),
        }
    except Exception as exc:  # noqa: BLE001
        # Preserve infrastructure/runtime failure separately from model/data
        # availability. Never relabel this as MODEL_UNAVAILABLE.
        return {
            "status": "BLOCKED",
            "code": "SPREAD_REPLAY_RUNTIME_FAILED",
            "sport": request.sport,
            "error_type": type(exc).__name__,
            **_governance_fields(),
        }

    artifact = dict(result.get("artifact") or {})
    receipt = dict(result.get("receipt") or {})
    metrics = dict(receipt.get("metrics") or {})
    return {
        "status": "EXPERIMENT_CREATED",
        "code": "SPREAD_HISTORICAL_REPLAY_COMPLETE",
        "sport": request.sport,
        "candidate": {
            "program": artifact.get("program"),
            "model_family": artifact.get("model_family"),
            "feature_schema_version": artifact.get("feature_schema_version"),
            "training_dataset_hash": artifact.get("training_dataset_hash"),
            "train_rows": artifact.get("train_rows"),
            "calibration_rows": artifact.get("calibration_rows"),
            "test_rows": artifact.get("test_rows"),
        },
        "artifact_checksum": receipt.get("artifact_checksum"),
        "historical_row_count": receipt.get("historical_row_count"),
        "metrics": metrics,
        "next_required_stage": receipt.get("next_required_stage"),
        "market_features_used": metrics.get("market_features_used", False),
        "moneyline_probability_used": metrics.get("moneyline_probability_used", False),
        "spread_line_used_as_feature": metrics.get("spread_line_used_as_feature", False),
        **_governance_fields(),
    }


def install_spread_margin_replay_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any) -> None:
    path = "/internal/v17/spread-margin-replay"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17SpreadMarginReplay",
    )
    def run_replay(request: SpreadMarginReplayRequest) -> dict[str, Any]:
        return execute_spread_margin_replay(db_client_fn(), request)


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "DATABASE_MUTATED",
    "GLOBAL_TERMINAL_REDUCER",
    "PROBABILITY_PUBLISHABLE",
    "PRODUCTION_REGISTRY_MUTATED",
    "SpreadMarginReplayRequest",
    "execute_spread_margin_replay",
    "install_spread_margin_replay_route",
]
