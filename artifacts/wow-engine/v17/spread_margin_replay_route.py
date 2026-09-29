"""OIDC-protected, research-only spread and run-line challenger surfaces."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from v17.mlb_run_line_shadow import (
    MLBRunLineShadowUnavailable,
    run_mlb_run_line_forward_shadow,
)
from v17.spread_market_evidence import DEFAULT_BOOKS, SpreadMarketEvidenceError, collect_spread_snapshot

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
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


class SpreadMarketEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: ReplaySport
    slate_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    books: list[str] = Field(default_factory=lambda: list(DEFAULT_BOOKS), min_length=2, max_length=5)


class SpreadForwardShadowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: Literal["NCAAF"] = "NCAAF"
    event_id: str = Field(min_length=1, max_length=128)
    event_start_time: str = Field(min_length=10, max_length=64)
    home_team: str = Field(min_length=1, max_length=160)
    away_team: str = Field(min_length=1, max_length=160)
    home_spread: float = Field(gt=-100.0, lt=100.0)
    season: int | None = Field(default=None, ge=2000, le=2100)


class NFLSpreadForwardShadowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: Literal["NFL"] = "NFL"
    event_id: str = Field(min_length=1, max_length=128)
    event_start_time: str = Field(min_length=10, max_length=64)
    home_team: str = Field(min_length=1, max_length=160)
    away_team: str = Field(min_length=1, max_length=160)
    home_spread: float = Field(gt=-30.0, lt=30.0)


class WNBASpreadForwardShadowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: Literal["WNBA"] = "WNBA"
    event_id: str = Field(pattern=r"^espn-.+$", max_length=128)
    event_start_time: str = Field(min_length=10, max_length=64)
    home_team_id: str = Field(pattern=r"^espn-.+$", max_length=128)
    away_team_id: str = Field(pattern=r"^espn-.+$", max_length=128)
    home_spread: float = Field(gt=-20.0, lt=20.0)


class MLBRunLineForwardShadowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: Literal["MLB"] = "MLB"
    score_snapshot_id: str = Field(min_length=1, max_length=128)
    home_run_line: float = Field(gt=-10.0, lt=10.0)


def _governance_fields() -> dict[str, Any]:
    return {
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "database_mutated": DATABASE_MUTATED,
        "production_registry_mutated": PRODUCTION_REGISTRY_MUTATED,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "can_execute": CAN_EXECUTE,
    }


def _evidence_governance_fields(*, rows_written: int = 0) -> dict[str, Any]:
    return {
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "evidence_database_mutated": rows_written > 0,
        "production_probability_database_mutated": False,
        "production_registry_mutated": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "can_execute": False,
    }


def execute_spread_margin_replay(db: Any, request: SpreadMarginReplayRequest) -> dict[str, Any]:
    # Historical spread model fitting is a research dependency. Defer its
    # scientific stack until this internal endpoint is actually invoked.
    from v17.spread_margin_challenger import SpreadChallengerUnavailable
    from v17.spread_margin_replay import run_historical_replay

    try:
        result = run_historical_replay(sport=request.sport, client=db, min_rows=request.min_rows, ridge_alpha=request.ridge_alpha)
    except SpreadChallengerUnavailable as exc:
        return {"status": "BLOCKED", "code": exc.code, "sport": request.sport, "detail": str(exc), **_governance_fields()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "code": "SPREAD_REPLAY_RUNTIME_FAILED", "sport": request.sport, "error_type": type(exc).__name__, **_governance_fields()}
    artifact = dict(result.get("artifact") or {})
    receipt = dict(result.get("receipt") or {})
    metrics = dict(receipt.get("metrics") or {})
    return {
        "status": "EXPERIMENT_CREATED", "code": "SPREAD_HISTORICAL_REPLAY_COMPLETE", "sport": request.sport,
        "candidate": {"program": artifact.get("program"), "model_family": artifact.get("model_family"), "feature_schema_version": artifact.get("feature_schema_version"), "training_dataset_hash": artifact.get("training_dataset_hash"), "train_rows": artifact.get("train_rows"), "calibration_rows": artifact.get("calibration_rows"), "test_rows": artifact.get("test_rows")},
        "artifact_checksum": receipt.get("artifact_checksum"), "historical_row_count": receipt.get("historical_row_count"),
        "metrics": metrics, "next_required_stage": receipt.get("next_required_stage"),
        "market_features_used": metrics.get("market_features_used", False), "moneyline_probability_used": metrics.get("moneyline_probability_used", False),
        "spread_line_used_as_feature": metrics.get("spread_line_used_as_feature", False), **_governance_fields(),
    }


def execute_spread_exact_line_replay(db: Any, request: SpreadMarginReplayRequest) -> dict[str, Any]:
    from v17.spread_exact_line_replay import run_exact_line_replay
    from v17.spread_margin_challenger import SpreadChallengerUnavailable

    try:
        result = run_exact_line_replay(sport=request.sport, client=db, min_rows=request.min_rows, ridge_alpha=request.ridge_alpha)
    except SpreadChallengerUnavailable as exc:
        return {"status": "BLOCKED", "code": exc.code, "sport": request.sport, "detail": str(exc), "evaluation_mode": "PROVIDER_BOUND_EXACT_SPREAD", **_governance_fields()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "code": "SPREAD_EXACT_LINE_REPLAY_RUNTIME_FAILED", "sport": request.sport, "error_type": type(exc).__name__, "evaluation_mode": "PROVIDER_BOUND_EXACT_SPREAD", **_governance_fields()}
    return {
        "status": "EXPERIMENT_CREATED", "code": "SPREAD_EXACT_LINE_REPLAY_COMPLETE", "sport": request.sport,
        "model_family": result.get("model_family"), "training_dataset_hash": result.get("training_dataset_hash"),
        "artifact_train_rows": result.get("artifact_train_rows"), "artifact_calibration_rows": result.get("artifact_calibration_rows"), "artifact_test_rows": result.get("artifact_test_rows"),
        "exact_line_metrics": result.get("exact_line_metrics"), "binding_audit": result.get("binding_audit"), "synthetic_grid_diagnostic": result.get("synthetic_grid_diagnostic"),
        "evaluation_mode": "PROVIDER_BOUND_EXACT_SPREAD", "market_features_used": False, "moneyline_probability_used": False, "spread_line_used_as_feature": False,
        **_governance_fields(),
    }


def execute_spread_market_evidence_collection(db: Any, request: SpreadMarketEvidenceRequest) -> dict[str, Any]:
    try:
        result = collect_spread_snapshot(db, sport=request.sport, slate_date=request.slate_date, books=request.books)
    except SpreadMarketEvidenceError as exc:
        return {"status": "BLOCKED", "code": exc.code, "sport": request.sport, "slate_date": request.slate_date, "detail": str(exc), **_evidence_governance_fields()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "code": "SPREAD_MARKET_EVIDENCE_RUNTIME_FAILED", "sport": request.sport, "slate_date": request.slate_date, "error_type": type(exc).__name__, **_evidence_governance_fields()}
    rows_written = int(result.get("rows_written") or 0)
    return {**result, "provider_collection_status": result.get("status"), "status": "EVIDENCE_CAPTURED" if rows_written > 0 else "EVIDENCE_EMPTY", "code": "SPREAD_MARKET_EVIDENCE_CAPTURED" if rows_written > 0 else "SPREAD_MARKET_EVIDENCE_EMPTY", **_evidence_governance_fields(rows_written=rows_written)}


def _execute_forward(sport: str, event_id: str, fn: Any) -> dict[str, Any]:
    from v17.spread_margin_challenger import SpreadChallengerUnavailable

    try:
        result = fn()
    except SpreadChallengerUnavailable as exc:
        return {"status": "BLOCKED", "code": exc.code, "sport": sport, "event_id": event_id, "detail": str(exc), **_governance_fields()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "code": f"{sport}_SPREAD_FORWARD_SHADOW_RUNTIME_FAILED", "sport": sport, "event_id": event_id, "error_type": type(exc).__name__, **_governance_fields()}
    return {**result, **_governance_fields()}


def _execute_auto_canary(sport: str, fn: Any) -> dict[str, Any]:
    from v17.spread_margin_challenger import SpreadChallengerUnavailable

    try:
        result = fn()
    except SpreadChallengerUnavailable as exc:
        return {"status": "BLOCKED", "code": exc.code, "sport": sport, "detail": str(exc), "identity_acquisition_location": "BACKEND_RUNTIME", **_governance_fields()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "code": f"{sport}_SPREAD_AUTO_CANARY_RUNTIME_FAILED", "sport": sport, "error_type": type(exc).__name__, "identity_acquisition_location": "BACKEND_RUNTIME", **_governance_fields()}
    return {**result, **_governance_fields()}


def execute_spread_forward_shadow(db: Any, request: SpreadForwardShadowRequest) -> dict[str, Any]:
    from v17.spread_forward_shadow import run_ncaaf_forward_shadow

    return _execute_forward("NCAAF", request.event_id, lambda: run_ncaaf_forward_shadow(db, event_id=request.event_id, event_start_time=request.event_start_time, home_team=request.home_team, away_team=request.away_team, home_spread=request.home_spread, season=request.season))


def execute_nfl_spread_forward_shadow(db: Any, request: NFLSpreadForwardShadowRequest) -> dict[str, Any]:
    from v17.spread_forward_shadow_leagues import run_nfl_forward_shadow

    return _execute_forward("NFL", request.event_id, lambda: run_nfl_forward_shadow(db, event_id=request.event_id, event_start_time=request.event_start_time, home_team=request.home_team, away_team=request.away_team, home_spread=request.home_spread))


def execute_wnba_spread_forward_shadow(db: Any, request: WNBASpreadForwardShadowRequest) -> dict[str, Any]:
    from v17.spread_forward_shadow_leagues import run_wnba_forward_shadow

    return _execute_forward("WNBA", request.event_id, lambda: run_wnba_forward_shadow(db, event_id=request.event_id, event_start_time=request.event_start_time, home_team_id=request.home_team_id, away_team_id=request.away_team_id, home_spread=request.home_spread))


def execute_nfl_spread_auto_canary(db: Any) -> dict[str, Any]:
    from v17.spread_forward_auto_canary import run_nfl_spread_auto_canary

    return _execute_auto_canary("NFL", lambda: run_nfl_spread_auto_canary(db))


def execute_wnba_spread_auto_canary(db: Any) -> dict[str, Any]:
    from v17.spread_forward_auto_canary import run_wnba_spread_auto_canary

    return _execute_auto_canary("WNBA", lambda: run_wnba_spread_auto_canary(db))


def execute_mlb_run_line_forward_shadow(db: Any, request: MLBRunLineForwardShadowRequest) -> dict[str, Any]:
    try:
        result = run_mlb_run_line_forward_shadow(
            db,
            score_snapshot_id=request.score_snapshot_id,
            home_run_line=request.home_run_line,
        )
    except MLBRunLineShadowUnavailable as exc:
        return {"status": "BLOCKED", "code": exc.code, "sport": "MLB", "score_snapshot_id": request.score_snapshot_id, "detail": str(exc), **_governance_fields()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "code": "MLB_RUN_LINE_FORWARD_SHADOW_RUNTIME_FAILED", "sport": "MLB", "score_snapshot_id": request.score_snapshot_id, "error_type": type(exc).__name__, **_governance_fields()}
    return {**result, **_governance_fields()}


def install_spread_margin_replay_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any) -> None:
    paths = {getattr(route, "path", None) for route in app.router.routes}
    auth = [scout_route_auth_dependency(auth_dependency)]
    if "/internal/v17/spread-margin-replay" not in paths:
        @app.post("/internal/v17/spread-margin-replay", dependencies=auth, operation_id="runWowV17SpreadMarginReplay")
        def run_replay(request: SpreadMarginReplayRequest) -> dict[str, Any]:
            return execute_spread_margin_replay(db_client_fn(), request)
    if "/internal/v17/spread-exact-line-replay" not in paths:
        @app.post("/internal/v17/spread-exact-line-replay", dependencies=auth, operation_id="runWowV17SpreadExactLineReplay")
        def run_exact_replay(request: SpreadMarginReplayRequest) -> dict[str, Any]:
            return execute_spread_exact_line_replay(db_client_fn(), request)
    if "/internal/v17/spread-market-evidence" not in paths:
        @app.post("/internal/v17/spread-market-evidence", dependencies=auth, operation_id="collectWowV17SpreadMarketEvidence")
        def collect_evidence(request: SpreadMarketEvidenceRequest) -> dict[str, Any]:
            return execute_spread_market_evidence_collection(db_client_fn(), request)
    if "/internal/v17/spread-forward-shadow" not in paths:
        @app.post("/internal/v17/spread-forward-shadow", dependencies=auth, operation_id="scoreWowV17SpreadForwardShadow")
        def score_forward_shadow(request: SpreadForwardShadowRequest) -> dict[str, Any]:
            return execute_spread_forward_shadow(db_client_fn(), request)
    if "/internal/v17/nfl-spread-forward-shadow" not in paths:
        @app.post("/internal/v17/nfl-spread-forward-shadow", dependencies=auth, operation_id="scoreWowV17NFLSpreadForwardShadow")
        def score_nfl_forward_shadow(request: NFLSpreadForwardShadowRequest) -> dict[str, Any]:
            return execute_nfl_spread_forward_shadow(db_client_fn(), request)
    if "/internal/v17/wnba-spread-forward-shadow" not in paths:
        @app.post("/internal/v17/wnba-spread-forward-shadow", dependencies=auth, operation_id="scoreWowV17WNBASpreadForwardShadow")
        def score_wnba_forward_shadow(request: WNBASpreadForwardShadowRequest) -> dict[str, Any]:
            return execute_wnba_spread_forward_shadow(db_client_fn(), request)
    if "/internal/v17/nfl-spread-forward-auto-canary" not in paths:
        @app.post("/internal/v17/nfl-spread-forward-auto-canary", dependencies=auth, operation_id="runWowV17NFLSpreadForwardAutoCanary")
        def run_nfl_auto_canary() -> dict[str, Any]:
            return execute_nfl_spread_auto_canary(db_client_fn())
    if "/internal/v17/wnba-spread-forward-auto-canary" not in paths:
        @app.post("/internal/v17/wnba-spread-forward-auto-canary", dependencies=auth, operation_id="runWowV17WNBASpreadForwardAutoCanary")
        def run_wnba_auto_canary() -> dict[str, Any]:
            return execute_wnba_spread_auto_canary(db_client_fn())
    if "/internal/v17/mlb-run-line-forward-shadow" not in paths:
        @app.post("/internal/v17/mlb-run-line-forward-shadow", dependencies=auth, operation_id="scoreWowV17MLBRunLineForwardShadow")
        def score_mlb_run_line_forward_shadow(request: MLBRunLineForwardShadowRequest) -> dict[str, Any]:
            return execute_mlb_run_line_forward_shadow(db_client_fn(), request)


__all__ = [
    "AUTOMATIC_CERTIFICATION", "AUTOMATIC_PROMOTION", "CAN_EXECUTE", "DATABASE_MUTATED",
    "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS", "GLOBAL_TERMINAL_REDUCER", "PROBABILITY_PUBLISHABLE",
    "PRODUCTION_REGISTRY_MUTATED", "SpreadForwardShadowRequest", "NFLSpreadForwardShadowRequest",
    "WNBASpreadForwardShadowRequest", "MLBRunLineForwardShadowRequest", "SpreadMarginReplayRequest", "SpreadMarketEvidenceRequest",
    "execute_spread_exact_line_replay", "execute_spread_forward_shadow", "execute_nfl_spread_forward_shadow",
    "execute_wnba_spread_forward_shadow", "execute_nfl_spread_auto_canary", "execute_wnba_spread_auto_canary",
    "execute_mlb_run_line_forward_shadow", "execute_spread_margin_replay", "execute_spread_market_evidence_collection",
    "install_spread_margin_replay_route",
]