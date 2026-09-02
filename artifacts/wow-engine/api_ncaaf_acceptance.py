"""NCAAF research wrapper around the governed production API.

Adds authenticated, non-executable NCAAF maintenance boundaries for closing-line
capture, readiness inspection, historical read-only source hydration, and raw
reviewed official-conference availability ingestion. The final production
entrypoint can enable calibration/publication lane separation without mutating
lower-layer test/runtime contracts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from fastapi import Depends, Header, HTTPException

import api_prod_market_acceptance as base
import calibration_publication_api as lane_patch
from live_probability_runtime import install_live_probability_routes
from mlb_1ip_refresh_scheduler import run_refresh_loop as run_mlb_1ip_refresh_loop
from ncaaf_cfbd_client import CFBDClient, CFBDUnavailable
from ncaaf_cfbd_hydrator import hydrate_cfbd_season, persist_source_snapshots
from ncaaf_closing_capture import run_from_environment
from ncaaf_raw_availability_runtime import install_raw_availability_routes
from ncaaf_training_materializer import materialize_training_games
from pick_request_runtime import install_pick_request_routes
from team_event_request_runtime import install_team_event_request_routes
from prop_live_model_acceptance import run_prop_model_live_self_acceptance

app = base.app
_auth = Depends(base.market_api.prod._require_action_api_key)
_logger = logging.getLogger("wow.ncaaf.readiness")
_mlb_1ip_refresh_logger = logging.getLogger("wow.mlb.1ip.final_refresh")
_background_tasks: set[asyncio.Task] = set()
_original_market_score_prop = base.market_api.score_prop

# This mutation is intentionally production-gated. The lower api_prod_market app
# is a shared FastAPI object imported by several contract tests. Unconditionally
# replacing its route here would leak the final-entrypoint policy into lower-layer
# unit tests and obscure which boundary owns the behavior.
if os.getenv("WOW_CALIBRATION_PUBLICATION_LANE_SEPARATION", "0") == "1":
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == "/score-prop"
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]

    @app.post(
        "/score-prop",
        dependencies=[_auth],
        operation_id="scoreWowProp",
    )
    def score_prop_lane_separated(
        req: base.market_api.ScorePropRequest,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        model_identity = base.market_api.prod._reject_llp_prop_identity(x_wow_model_identity)
        lane = base.market_api.prod._runtime_capability(base.market_api.prod.PROP_CAPABILITY_KEY)
        preflight = lane_patch._governed_preflight(base.market_api)
        blockers = list(dict.fromkeys([
            *lane_patch._collect_blockers(lane.get("evidence") or {}),
            *lane_patch._collect_blockers(preflight),
        ]))

        if preflight.get("governed_publishable") is True or preflight.get("probability_publishable") is True:
            return _original_market_score_prop(req, x_wow_model_identity)

        if lane_patch._publication_only(blockers):
            return lane_patch._raw_specialist_research(
                base.market_api,
                req,
                model_identity=model_identity,
                lane=lane,
                preflight=preflight,
                blockers=blockers,
            )

        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROP_PROBABILITY_UNAVAILABLE",
                "governed_probability_capability": lane.get("capability_status") or "UNAVAILABLE",
                "governed_publication_capability": preflight.get("governed_publication_capability") or "UNAVAILABLE",
                "specialist_model_capability": preflight.get("specialist_model_capability") or "NOT_EVALUATED",
                "failed_contract_scope": preflight.get("failed_contract_scope") or ["GLOBAL"],
                "probability_claim_status": preflight.get("probability_claim_status") or "MODEL_UNAVAILABLE",
                "capability_evidence": lane.get("evidence") or {},
                "preflight": preflight,
                "blockers": blockers or ["UNCLASSIFIED_CAPABILITY_FAILURE"],
                "probability_publishable": False,
                "governed_publishable": False,
                "can_execute": False,
            },
        )

    # Pick Request and any other in-process caller must traverse the exact same
    # publication gate as the HTTP /score-prop route. Keep the original scorer
    # captured above so the healthy governed-publication branch cannot recurse.
    base.market_api.score_prop = score_prop_lane_separated


def _db_client():
    return base.market_api.prod.get_client()


install_raw_availability_routes(app, auth_dependency=_auth, db_client_fn=_db_client)
install_pick_request_routes(app, market_api=base.market_api, auth_dependency=_auth)
install_team_event_request_routes(
    app,
    auth_dependency=_auth,
    db_client_fn=_db_client,
    event_api=base.market_api.prod.event_api,
)
install_live_probability_routes(app, auth_dependency=_auth, db_client_fn=_db_client)


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
    evidence_provider_n = _safe_count("wow_ncaaf_evidence_sources")
    pregame_evidence_n = _safe_count("wow_ncaaf_pregame_evidence")
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
    if evidence_provider_n in (None, 0):
        blockers.append("NCAAF_EVIDENCE_PROVIDER_REGISTRY_EMPTY")
    if pregame_evidence_n in (None, 0):
        blockers.append("NCAAF_PREGAME_EVIDENCE_EMPTY")
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
        "evidence_provider_n": evidence_provider_n,
        "pregame_evidence_n": pregame_evidence_n,
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
            "WOW_NCAAF_READINESS cfbd_configured=%s source_n=%s game_n=%s feature_n=%s evidence_provider_n=%s pregame_evidence_n=%s forward_shadow_n=%s artifact_status=%s calibrator_status=%s controlling_model=%s trust_state=%s blockers=%s probability_publishable=false can_execute=false",
            state["cfbd_configured"],
            state["historical_source_snapshot_n"],
            state["training_game_n"],
            state["training_feature_n"],
            state["evidence_provider_n"],
            state["pregame_evidence_n"],
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


@app.on_event("startup")
async def schedule_prop_model_live_self_acceptance():
    """Optionally prove the real fitted prop path using one governed snapshot."""
    if not os.getenv("WOW_PROP_MODEL_SELF_ACCEPTANCE_SNAPSHOT_ID"):
        return
    task = asyncio.create_task(
        run_prop_model_live_self_acceptance(base.market_api, _logger)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.on_event("startup")
async def schedule_mlb_1ip_final_refresh():
    """Run final-refresh passes in-process when explicitly enabled."""
    if os.getenv("WOW_MLB_1IP_FINAL_REFRESH_ENABLED", "0") != "1":
        return
    try:
        interval_seconds = int(os.getenv("WOW_MLB_1IP_FINAL_REFRESH_INTERVAL_SECONDS", "300"))
    except ValueError:
        interval_seconds = 300
    task = asyncio.create_task(
        run_mlb_1ip_refresh_loop(
            db_client_fn=_db_client,
            logger=_mlb_1ip_refresh_logger,
            interval_seconds=interval_seconds,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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


# api.py's startup validation can materialize FastAPI's OpenAPI cache before
# late production wrappers install their routes. Invalidate only the cached
# document after this final entrypoint has registered every route.
app.openapi_schema = None
