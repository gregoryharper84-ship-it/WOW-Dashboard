"""NCAAF research wrapper around the governed production API.

Adds authenticated, non-executable NCAAF maintenance boundaries for closing-line
capture, readiness inspection, and historical read-only source hydration.
Existing prop/event scoring behavior is inherited unchanged.
"""
from __future__ import annotations

import logging
import os
from fastapi import Depends, HTTPException

import api_prod_market_acceptance as base
from ncaaf_cfbd_client import CFBDClient, CFBDUnavailable
from ncaaf_cfbd_hydrator import hydrate_cfbd_season, persist_source_snapshots
from ncaaf_closing_capture import run_from_environment
from ncaaf_training_materializer import materialize_training_games

app = base.app
_auth = Depends(base.market_api.prod._require_action_api_key)
_logger = logging.getLogger("wow.ncaaf.readiness")


def _db_client():
    return base.market_api.prod.get_client()


def _safe_count(table: str) -> int | None:
    try:
        result = _db_client().table(table).select("*", count="exact").limit(1).execute()
        return int(result.count or 0)
    except Exception:
        return None


def _artifact_state() -> dict:
    try:
        result = _db_client().rpc(
            "wow_ncaaf_certified_model_artifact",
            {"p_feature_schema_version": "NCAAF_FEATURES_V1"},
        ).execute()
        return result.data if isinstance(result.data, dict) else {"ok": False, "code": "NCAAF_MODEL_REGISTRY_INVALID_RESPONSE"}
    except Exception:
        return {"ok": False, "code": "NCAAF_MODEL_REGISTRY_UNAVAILABLE"}


def _calibrator_state(model_artifact_version: str | None) -> dict:
    if not model_artifact_version:
        return {"ok": False, "code": "NCAAF_MODEL_ARTIFACT_UNAVAILABLE"}
    try:
        result = _db_client().rpc(
            "wow_ncaaf_active_calibrator",
            {"p_model_artifact_version": model_artifact_version},
        ).execute()
        return result.data if isinstance(result.data, dict) else {"ok": False, "code": "NCAAF_CALIBRATOR_REGISTRY_INVALID_RESPONSE"}
    except Exception:
        return {"ok": False, "code": "NCAAF_CALIBRATOR_REGISTRY_UNAVAILABLE"}


def ncaaf_readiness():
    artifact = _artifact_state()
    calibrator = _calibrator_state(artifact.get("model_artifact_version") if artifact.get("ok") is True else None)
    source_n = _safe_count("wow_ncaaf_source_snapshots")
    game_n = _safe_count("wow_ncaaf_training_games")
    feature_n = _safe_count("wow_ncaaf_training_features")
    prediction_n = _safe_count("wow_ncaaf_predictions")

    blockers: list[str] = []
    if not bool(os.getenv("CFBD_API_KEY")):
        blockers.append("CFBD_API_KEY_MISSING")
    if source_n in (None, 0):
        blockers.append("NCAAF_HISTORICAL_SOURCE_SNAPSHOTS_EMPTY")
    if game_n in (None, 0):
        blockers.append("NCAAF_TRAINING_GAMES_EMPTY")
    if feature_n in (None, 0):
        blockers.append("NCAAF_TRAINING_FEATURES_EMPTY")
    if artifact.get("ok") is not True:
        blockers.append(str(artifact.get("code") or "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"))
    if calibrator.get("ok") is not True:
        blockers.append(str(calibrator.get("code") or "NCAAF_CERTIFIED_CALIBRATOR_NOT_FOUND"))
    if prediction_n in (None, 0):
        blockers.append("NCAAF_FORWARD_SHADOW_EMPTY")

    return {
        "ok": True,
        "provider_identity": "WOW_NCAAF_FITTED_MODEL_V1",
        "cfbd_configured": bool(os.getenv("CFBD_API_KEY")),
        "historical_source_snapshot_n": source_n,
        "training_game_n": game_n,
        "training_feature_n": feature_n,
        "forward_shadow_n": prediction_n,
        "artifact_status": artifact.get("code"),
        "calibrator_status": calibrator.get("code"),
        "ncaaf_controlling_model": "AVAILABLE" if not blockers else "MODEL_UNAVAILABLE",
        "ncaaf_trust_state": "NCAAF_TEST_ONLY",
        "blockers": sorted(set(blockers)),
        "probability_publishable": False,
        "can_execute": False,
    }


@app.get(
    "/internal/ncaaf/readiness",
    dependencies=[_auth],
    operation_id="getNcaafReadiness",
)
def get_ncaaf_readiness():
    return ncaaf_readiness()


@app.on_event("startup")
async def log_ncaaf_startup_readiness():
    """Emit non-secret, non-probability readiness evidence after each deploy."""
    try:
        state = ncaaf_readiness()
        _logger.warning(
            "WOW_NCAAF_READINESS cfbd_configured=%s source_n=%s game_n=%s feature_n=%s forward_shadow_n=%s artifact_status=%s calibrator_status=%s controlling_model=%s trust_state=%s blockers=%s probability_publishable=false can_execute=false",
            state["cfbd_configured"],
            state["historical_source_snapshot_n"],
            state["training_game_n"],
            state["training_feature_n"],
            state["forward_shadow_n"],
            state["artifact_status"],
            state["calibrator_status"],
            state["ncaaf_controlling_model"],
            state["ncaaf_trust_state"],
            ",".join(state["blockers"]),
        )
    except Exception as exc:
        _logger.error(
            "WOW_NCAAF_READINESS assessment=UNAVAILABLE error_type=%s probability_publishable=false can_execute=false",
            type(exc).__name__,
        )


@app.post(
    "/internal/ncaaf/hydrate-history",
    dependencies=[_auth],
    operation_id="hydrateNcaafHistory",
)
def hydrate_ncaaf_history(
    season: int,
    start_week: int = 1,
    end_week: int = 15,
):
    if season < 2018 or season > 2026:
        raise HTTPException(status_code=422, detail={"code": "NCAAF_SEASON_OUT_OF_RANGE", "probability_publishable": False, "can_execute": False})
    if start_week < 0 or end_week > 30 or start_week > end_week:
        raise HTTPException(status_code=422, detail={"code": "NCAAF_WEEK_RANGE_INVALID", "probability_publishable": False, "can_execute": False})
    try:
        client = CFBDClient.from_environment()
    except CFBDUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "probability_publishable": False, "can_execute": False}) from exc
    try:
        snapshots = hydrate_cfbd_season(
            client,
            season=season,
            weeks=range(start_week, end_week + 1),
            rating_families=("elo",),
            classification="fbs",
        )
        db = _db_client()
        persisted_n = persist_source_snapshots(db, snapshots)
        games = materialize_training_games(db, snapshots)
    except CFBDUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "probability_publishable": False, "can_execute": False}) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "NCAAF_HISTORY_HYDRATION_FAILED", "error_type": type(exc).__name__, "probability_publishable": False, "can_execute": False}) from exc

    blocker_codes = sorted({code for snapshot in snapshots for code in snapshot.blocker_codes}.union(games.blocker_codes))
    return {
        "ok": True,
        "season": season,
        "weeks": [start_week, end_week],
        "source_snapshot_n": len(snapshots),
        "source_snapshot_persisted_n": persisted_n,
        "training_game_candidate_n": games.candidate_rows,
        "training_game_persisted_n": games.persisted_rows,
        "training_game_skipped_n": games.skipped_rows,
        "blocker_codes": blocker_codes,
        "feature_build_status": "BLOCKED_MISSING_FULL_PREGAME_FEATURE_EVIDENCE",
        "model_training_status": "NOT_ATTEMPTED",
        "probability_publishable": False,
        "can_execute": False,
    }


@app.post(
    "/internal/ncaaf/capture-closing-lines",
    dependencies=[_auth],
    operation_id="captureNcaafClosingLines",
)
def capture_ncaaf_closing_lines():
    try:
        result = run_from_environment()
    except RuntimeError as exc:
        message = str(exc)
        code = "NCAAF_CLOSING_FEED_UNCONFIGURED" if "WOW_NCAAF_MARKET_FEED_URL" in message else "NCAAF_CLOSING_CAPTURE_CONFIGURATION_UNAVAILABLE"
        raise HTTPException(status_code=503, detail={"code": code, "message": message, "probability_publishable": False, "can_execute": False}) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "NCAAF_CLOSING_CAPTURE_FAILED", "error_type": type(exc).__name__, "probability_publishable": False, "can_execute": False}) from exc

    return {
        "ok": True,
        "status": result.status,
        "candidates_checked": result.candidates_checked,
        "quotes_captured": result.quotes_captured,
        "no_close_marked": result.no_close_marked,
        "provider_failures": result.provider_failures,
        "identity_failures": result.identity_failures,
        "stale_quote_failures": result.stale_quote_failures,
        "probability_publishable": False,
        "can_execute": False,
    }
