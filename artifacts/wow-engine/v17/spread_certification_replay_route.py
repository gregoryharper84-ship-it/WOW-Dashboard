"""OIDC-protected spread certification evidence replay surface.

This route executes historical close-proxy evaluation only. It cannot certify,
promote, register, publish, rank, or execute a spread/run-line selection.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from v17.mlb_run_line_shadow import RUN_LINE_MODEL_FAMILY
from v17.nfl_spread_context_challenger import run_nfl_context_v2_close_proxy_replay
from v17.nfl_spread_scale_calibrated_challenger import run_nfl_context_v3_scale_calibrated_replay
from v17.spread_certification_replay import run_nflverse_close_proxy_replay
from v17.spread_margin_challenger import SpreadChallengerUnavailable

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PRODUCTION_REGISTRY_MUTATED = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"


class SpreadCertificationReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: Literal["MLB", "NFL", "WNBA"]
    variant: Literal["V1", "NFL_CONTEXT_V2", "NFL_CONTEXT_V3_SCALE_CAL"] = "V1"
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


def _mlb_historical_replay_blocker() -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "code": "MLB_RUN_LINE_HISTORICAL_SAME_FAMILY_STATE_UNAVAILABLE",
        "sport": "MLB",
        "detail": (
            "current MLB run-line certification replay requires frozen historical "
            "pregame state from the active run-line family plus a governed canonical "
            "event crosswalk before ESPN run-line close proxies can be bound"
        ),
        "model_family": RUN_LINE_MODEL_FAMILY,
        "evidence_class": "ESPN_HISTORICAL_RUN_LINE_CLOSE_PROXY",
        "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
        "exact_line_metrics": {
            "evidence_row_n": 0,
            "eligible_historical_model_state_n": 0,
            "bound_exact_line_n": 0,
            "exact_line_coverage": 0.0,
        },
        "blockers": [
            "MLB_RUN_LINE_HISTORICAL_SAME_FAMILY_MODEL_STATE_UNAVAILABLE",
            "MLB_RUN_LINE_CANONICAL_EVENT_CROSSWALK_UNAVAILABLE",
        ],
        "required_next_evidence": [
            "frozen pregame score-distribution state proven compatible with the active run-line model family",
            "governed canonical MLB event to ESPN event crosswalk frozen before historical line binding",
            "exact historical run-line proxy bound only after model state is frozen",
        ],
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "lookahead_reconstruction_allowed": False,
        **_governance(),
    }


def _wnba_historical_replay_blocker() -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "code": "WNBA_SPREAD_HISTORICAL_CLOSE_PROXY_BATCH_ACQUISITION_REQUIRED",
        "sport": "WNBA",
        "detail": (
            "full held-out WNBA ESPN final-summary close-proxy acquisition requires "
            "a bounded resumable batch artifact; synchronous per-event acquisition is "
            "intentionally not treated as certification evidence"
        ),
        "evidence_class": "ESPN_HISTORICAL_CLOSE_PROXY",
        "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
        "exact_line_metrics": {
            "evidence_row_n": 0,
            "bound_exact_line_n": 0,
            "exact_line_coverage": 0.0,
        },
        "binding_audit": {
            "acquisition_mode": "BATCH_ARTIFACT_REQUIRED",
            "bound_event_n": 0,
            "coverage": 0.0,
            "source_provider": "ESPN_FINAL_SUMMARY_PICKCENTER",
            "source_quote_timestamp_state": "HISTORICAL_FINAL_SUMMARY_CLOSE_PROXY_TIMESTAMP_UNAVAILABLE",
            "historical_certification_evidence_only": True,
            "live_card_receipt_eligible": False,
            "clv_evidence": False,
            "probability_publishable": False,
            "can_execute": False,
        },
        "blockers": ["WNBA_SPREAD_HISTORICAL_CLOSE_PROXY_BATCH_ARTIFACT_UNAVAILABLE"],
        "required_next_evidence": [
            "bounded resumable ESPN final-summary acquisition over the untouched WNBA holdout",
            "frozen aggregate payload hash and exact event/team identity audit",
            "exact-line calibration metrics computed only after the batch artifact is complete",
        ],
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        **_governance(),
    }


def execute_spread_certification_replay(db: Any, request: SpreadCertificationReplayRequest) -> dict[str, Any]:
    if request.variant in {"NFL_CONTEXT_V2", "NFL_CONTEXT_V3_SCALE_CAL"} and request.sport != "NFL":
        return {
            "status": "BLOCKED",
            "code": "SPREAD_CERTIFICATION_VARIANT_SPORT_MISMATCH",
            "sport": request.sport,
            "variant": request.variant,
            "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
            **_governance(),
        }
    if request.sport == "MLB":
        return _mlb_historical_replay_blocker()
    if request.sport == "WNBA":
        return _wnba_historical_replay_blocker()
    try:
        if request.variant == "NFL_CONTEXT_V3_SCALE_CAL":
            result = run_nfl_context_v3_scale_calibrated_replay(client=db, ridge_alpha=request.ridge_alpha)
        elif request.variant == "NFL_CONTEXT_V2":
            result = run_nfl_context_v2_close_proxy_replay(client=db, ridge_alpha=request.ridge_alpha)
        else:
            result = run_nflverse_close_proxy_replay(
                client=db,
                min_rows=request.min_rows,
                ridge_alpha=request.ridge_alpha,
            )
    except SpreadChallengerUnavailable as exc:
        return {
            "status": "BLOCKED",
            "code": exc.code,
            "sport": request.sport,
            "variant": request.variant,
            "detail": str(exc),
            "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
            **_governance(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "BLOCKED",
            "code": "SPREAD_CERTIFICATION_REPLAY_RUNTIME_FAILED",
            "sport": request.sport,
            "variant": request.variant,
            "error_type": type(exc).__name__,
            "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
            **_governance(),
        }
    return {
        **result,
        "variant": request.variant,
        "evidence_scope": "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY",
        **_governance(),
    }


def install_spread_certification_replay_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any) -> None:
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
