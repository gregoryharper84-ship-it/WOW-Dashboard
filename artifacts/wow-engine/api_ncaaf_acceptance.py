"""Production governed WOW API with NCAAF maintenance and v17 host routing.

The accepted production entrypoint preserves all v16-compatible routes while
optionally mounting the v17 host/team-event contract on the same governed
Render/Supabase core. V17 activation never authorizes wager execution.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header, HTTPException

from github_actions_oidc import scout_route_auth_dependency

import api_prod_market_acceptance as base
import calibration_publication_api as lane_patch
from kalshi_weather_v2.routes import install_kalshi_weather_v2_routes
from live_probability_runtime import install_live_probability_routes
from live_state_acquisition_runtime import install_live_state_acquisition_routes
from mlb_1ip_refresh_scheduler import run_refresh_loop as run_mlb_1ip_refresh_loop
from ncaaf_cfbd_client import CFBDClient, CFBDUnavailable
from ncaaf_cfbd_hydrator import hydrate_cfbd_season, persist_source_snapshots
from ncaaf_closing_capture import run_from_environment
from ncaaf_raw_availability_runtime import install_raw_availability_routes
from ncaaf_training_materializer import materialize_training_games
from pick_request_runtime import install_pick_request_routes
from prop_live_model_acceptance import run_prop_model_live_self_acceptance
from v17_synthetic_self_acceptance import run_v17_synthetic_self_acceptance
from recommendation_ledger_api import install_recommendation_ledger_routes
from team_event_request_runtime import install_team_event_request_routes
from v17.core_intelligence_compounding_routes import install_compounding_intelligence_routes_read_only
from v17.core_intelligence_event_runtime import install_core_intelligence_event_routes
from v17.core_intelligence_runtime import install_core_intelligence_routes
from v17.spread_forward_shadow import warm_ncaaf_forward_context
from v17.scout_handoff_queue_installer import schedule_scout_handoff_queue
from v17.reliability_http import install_reliability_headers
from v17.reliability_receipt_runtime import install_reliability_receipt_routes
from v17.core_intelligence_shadow_runtime import install_shadow_lab_routes
from v17.team_event_probability_preservation import (
    install_team_event_routes as install_v17_team_event_routes,
    score_team_event_request as score_v17_team_event_request,
)
import v17.daily_snapshot_runtime as v17_daily_snapshot_runtime
from v17.daily_snapshot_runtime import install_daily_snapshot_route
from v17_observability import initialize_observability

OBSERVABILITY = initialize_observability()
app = base.app
install_reliability_headers(app)
_auth = Depends(base.market_api.prod._require_action_api_key)
_spread_forward_auth = scout_route_auth_dependency(_auth)
_logger = logging.getLogger("wow.ncaaf.readiness")
_v17_logger = logging.getLogger("wow.v17.activation")
_core_intelligence_logger = logging.getLogger("wow.v17.core_intelligence.activation")
_kalshi_weather_logger = logging.getLogger("wow.kalshi_weather_v2.activation")
_mlb_1ip_refresh_logger = logging.getLogger("wow.mlb.1ip.final_refresh")
_spread_forward_logger = logging.getLogger("wow.v17.spread.forward")
_background_tasks: set[asyncio.Task] = set()
_DB_CLIENT_LOCAL = threading.local()
_NCAAF_STARTUP_READINESS_TIMEOUT_SECONDS = 12.0


def _spread_forward_warm_receipt(status: str, code: str, **extra) -> dict:
    return {
        "status": status,
        "code": code,
        "sport": "NCAAF",
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
        **extra,
    }


def _set_spread_forward_warm_state(status: str, code: str, **extra) -> dict:
    state = _spread_forward_warm_receipt(status, code, **extra)
    app.state.wow_ncaaf_spread_forward_warm = state
    return state


def _spread_forward_warm_error_code(exc: Exception) -> str:
    if type(exc).__name__ == "ReadTimeout":
        return "READ_TIMEOUT"
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    if getattr(exc, "args", None):
        first = exc.args[0]
        if isinstance(first, dict) and first.get("code"):
            return str(first["code"])
    return type(exc).__name__


def _spread_forward_warm_failure_is_transient(exc: Exception) -> bool:
    return (
        type(exc).__name__ == "ReadTimeout"
        or _spread_forward_warm_error_code(exc) == "PGRST002"
    )


_set_spread_forward_warm_state(
    "PENDING",
    "SPREAD_FORWARD_CONTEXT_WARM_PENDING",
    attempt=0,
    max_attempts=3,
)
_original_market_score_prop = base.market_api.score_prop
V17_ACTIVE = os.getenv("WOW_V17_ACTIVE", "0") == "1"
V17_CORE_INTELLIGENCE_ACTIVE = os.getenv("WOW_V17_CORE_INTELLIGENCE_ACTIVE", "0") == "1"
KALSHI_WEATHER_V2_ACTIVE = os.getenv("WOW_KALSHI_WEATHER_V2_ACTIVE", "0") == "1"

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
    """Reuse one Supabase client per worker thread.

    FastAPI executes synchronous route handlers in a worker thread pool. Some
    governed maintenance callers can issue many short sequential requests; a
    fresh supabase/http client per request retains enough transport state to
    push the small Render instance toward its RSS ceiling. Thread-local reuse
    bounds client allocation without sharing one client across worker threads
    or changing database/query semantics.
    """
    client = getattr(_DB_CLIENT_LOCAL, "client", None)
    if client is None:
        client = base.market_api.prod.get_client()
        _DB_CLIENT_LOCAL.client = client
    return client


install_raw_availability_routes(app, auth_dependency=_auth, db_client_fn=_db_client)
install_pick_request_routes(app, market_api=base.market_api, auth_dependency=_auth)
install_team_event_request_routes(
    app,
    auth_dependency=_auth,
    db_client_fn=_db_client,
    event_api=base.market_api.prod.event_api,
)
install_live_state_acquisition_routes(app, auth_dependency=_auth, db_client_fn=_db_client)
install_live_probability_routes(app, auth_dependency=_auth, db_client_fn=_db_client)
install_reliability_receipt_routes(app, auth_dependency=_auth, db_client_fn=_db_client)
if KALSHI_WEATHER_V2_ACTIVE:
    install_kalshi_weather_v2_routes(
        app,
        auth_dependency=_auth,
        db_client_fn=_db_client,
    )

# V17 is an additive compatibility cutover on the accepted production app: old
# governed operations remain available while the new host-aware route and ledger
# operations become authoritative for v17 Action schemas. This keeps rollback to
# WOW_V17_ACTIVE=0 atomic and does not duplicate scoring authority.
if V17_ACTIVE:
    if not any(getattr(route, "path", None) == "/score-team-event" for route in app.router.routes):
        install_v17_team_event_routes(
            app,
            event_api=base.market_api.prod.event_api,
            auth_dependency=_auth,
        )
    if not any(getattr(route, "path", None) == "/record-recommendations" for route in app.router.routes):
        install_recommendation_ledger_routes(
            app,
            auth_dependency=_auth,
            get_client_fn=_db_client,
        )

    # Core Intelligence is separately gated so migrations can land before route
    # exposure and rollback remains one environment-variable change. Its route
    # installers require the underlying callable (not an already-created Depends
    # object) so the Action-key dependency is actually attached.
    if V17_CORE_INTELLIGENCE_ACTIVE:
        core_intelligence_auth = base.market_api.prod._require_action_api_key
        install_core_intelligence_routes(
            app,
            auth_dependency=core_intelligence_auth,
            get_client_fn=_db_client,
        )
        install_core_intelligence_event_routes(
            app,
            auth_dependency=core_intelligence_auth,
            get_client_fn=_db_client,
        )
        install_compounding_intelligence_routes_read_only(
            app,
            auth_dependency=core_intelligence_auth,
            get_client_fn=_db_client,
        )
        install_shadow_lab_routes(
            app,
            auth_dependency=core_intelligence_auth,
            get_client_fn=_db_client,
        )

    # Daily imported the base scorer for deterministic lower-layer tests. Bind
    # only the active production V17 Daily module to the scoped repaired scorer;
    # this does not mutate the base team-event runtime or weaken any gate.
    v17_daily_snapshot_runtime.score_team_event_request = score_v17_team_event_request
    install_daily_snapshot_route(
        app,
        auth_dependency=_auth,
        db_client_fn=_db_client,
        market_api=base.market_api,
        event_api=base.market_api.prod.event_api,
    )
    # Feature-gated durable Scout -> specialist handoff. The scheduler itself
    # installs authenticated routes/workers only when the production flag is on.
    schedule_scout_handoff_queue(app, db_client_fn=_db_client)

    @app.get("/v17/host-contract", dependencies=[_auth], operation_id="getWowV17HostContract")
    def get_v17_host_contract():
        contract_path = Path(__file__).with_name("v17") / "custom_engine_alignment_contract.json"
        payload = json.loads(contract_path.read_text())
        return {
            "schema_version": payload["schema_version"],
            "status": payload["status"],
            "activation": payload["activation"],
            "hosts": payload["hosts"],
            "shared_core": payload["shared_core"],
            "team_event_contract": payload["team_event_contract"],
            "prop_contract": payload["prop_contract"],
            "backend_contract": payload["backend_contract"],
            "v17_active_implementation": payload["v17_active_implementation"],
            "can_execute": False,
        }


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


def _emit_ncaaf_readiness_state(state: dict) -> None:
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


async def _run_ncaaf_startup_readiness_audit() -> None:
    """Run read-only readiness outside the startup critical path with a bound."""
    try:
        state = await asyncio.wait_for(
            asyncio.to_thread(ncaaf_readiness),
            timeout=_NCAAF_STARTUP_READINESS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        _logger.error(
            "WOW_NCAAF_READINESS assessment=DEGRADED code=NCAAF_READINESS_TIMEOUT timeout_seconds=%s probability_publishable=false can_execute=false",
            _NCAAF_STARTUP_READINESS_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        _logger.error(
            "WOW_NCAAF_READINESS assessment=UNAVAILABLE error_type=%s probability_publishable=false can_execute=false",
            type(exc).__name__,
        )
    else:
        _emit_ncaaf_readiness_state(state)



@app.get(
    "/internal/v17/spread-forward-warm-status",
    dependencies=[_spread_forward_auth],
    operation_id="getWowV17SpreadForwardWarmStatus",
)
def get_ncaaf_spread_forward_warm_status():
    """Return process-local warm state without touching Supabase."""
    return dict(
        getattr(
            app.state,
            "wow_ncaaf_spread_forward_warm",
            _spread_forward_warm_receipt(
                "PENDING",
                "SPREAD_FORWARD_CONTEXT_WARM_PENDING",
                attempt=0,
                max_attempts=3,
            ),
        )
    )


async def _warm_ncaaf_spread_forward_context_after_startup() -> None:
    """Warm immutable research context after the shared startup DB consumers settle."""
    try:
        startup_delay_seconds = int(os.getenv("WOW_NCAAF_SPREAD_WARM_STARTUP_DELAY_SECONDS", "60"))
    except ValueError:
        startup_delay_seconds = 60
    startup_delay_seconds = max(0, min(startup_delay_seconds, 300))
    retry_delays = (0.0, 15.0, 30.0)
    max_attempts = len(retry_delays)

    _set_spread_forward_warm_state(
        "DELAYED" if startup_delay_seconds else "PENDING",
        "SPREAD_FORWARD_CONTEXT_WARM_DELAYED" if startup_delay_seconds else "SPREAD_FORWARD_CONTEXT_WARM_PENDING",
        attempt=0,
        max_attempts=max_attempts,
        delay_seconds=startup_delay_seconds,
    )
    if startup_delay_seconds:
        _spread_forward_logger.warning(
            "WOW_NCAAF_SPREAD_FORWARD_WARM status=DELAYED seconds=%s can_execute=false",
            startup_delay_seconds,
        )
        await asyncio.sleep(float(startup_delay_seconds))

    for attempt, retry_delay in enumerate(retry_delays, start=1):
        if retry_delay:
            _set_spread_forward_warm_state(
                "RETRYING",
                "SPREAD_FORWARD_CONTEXT_WARM_RETRYING",
                attempt=attempt,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay,
            )
            await asyncio.sleep(retry_delay)

        _set_spread_forward_warm_state(
            "WARMING",
            "SPREAD_FORWARD_CONTEXT_WARMING",
            attempt=attempt,
            max_attempts=max_attempts,
        )
        try:
            receipt = await asyncio.to_thread(warm_ncaaf_forward_context, _db_client())
        except Exception as exc:  # noqa: BLE001
            error_code = _spread_forward_warm_error_code(exc)
            transient = _spread_forward_warm_failure_is_transient(exc)
            has_retry = transient and attempt < max_attempts
            _set_spread_forward_warm_state(
                "RETRYING" if has_retry else "FAILED",
                "SPREAD_FORWARD_CONTEXT_WARM_RETRYING" if has_retry else "SPREAD_FORWARD_CONTEXT_WARM_FAILED",
                attempt=attempt,
                max_attempts=max_attempts,
                error_type=type(exc).__name__,
                error_code=error_code,
                transient=transient,
            )
            _spread_forward_logger.error(
                "WOW_NCAAF_SPREAD_FORWARD_WARM status=%s attempt=%s max_attempts=%s error_type=%s error_code=%s can_execute=false",
                "RETRYING" if has_retry else "FAILED",
                attempt,
                max_attempts,
                type(exc).__name__,
                error_code,
            )
            if has_retry:
                continue
            return

        ready = _set_spread_forward_warm_state(
            "READY",
            "SPREAD_FORWARD_CONTEXT_READY",
            attempt=attempt,
            max_attempts=max_attempts,
            cache_status=receipt.get("cache_status"),
            cache_age_seconds=receipt.get("cache_age_seconds"),
            training_cutoff_event_time=receipt.get("training_cutoff_event_time"),
        )
        _spread_forward_logger.warning(
            "WOW_NCAAF_SPREAD_FORWARD_WARM status=%s code=%s cache_status=%s attempt=%s can_execute=false",
            ready.get("status"),
            ready.get("code"),
            ready.get("cache_status"),
            attempt,
        )
        return


@app.on_event("startup")
async def schedule_ncaaf_spread_forward_context_warm() -> None:
    """Prime the cold fitted context without delaying port binding."""
    if not V17_ACTIVE:
        return
    task = asyncio.create_task(_warm_ncaaf_spread_forward_context_after_startup())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.on_event("startup")
async def log_ncaaf_startup_readiness():
    """Schedule non-secret readiness evidence after critical startup work."""
    async def _delayed_readiness() -> None:
        try:
            delay_seconds = int(os.getenv("WOW_NCAAF_READINESS_STARTUP_DELAY_SECONDS", "180"))
        except ValueError:
            delay_seconds = 180
        delay_seconds = max(0, min(delay_seconds, 600))
        if delay_seconds:
            _logger.warning(
                "WOW_NCAAF_READINESS assessment=DELAYED seconds=%s probability_publishable=false can_execute=false",
                delay_seconds,
            )
            await asyncio.sleep(float(delay_seconds))
        await _run_ncaaf_startup_readiness_audit()

    task = asyncio.create_task(_delayed_readiness())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.on_event("startup")
async def log_v17_activation():
    _v17_logger.warning(
        "WOW_V17_RUNTIME status=%s global_terminal_authority=V17_TERMINAL_REDUCER can_execute=false",
        "ACTIVE" if V17_ACTIVE else "INACTIVE",
    )


@app.on_event("startup")
async def log_v17_core_intelligence_activation():
    _core_intelligence_logger.warning(
        "WOW_V17_CORE_INTELLIGENCE status=%s configured=%s authority=ADVISORY_ONLY automatic_promotion=false probability_publishable=false can_execute=false",
        "ACTIVE" if (V17_ACTIVE and V17_CORE_INTELLIGENCE_ACTIVE) else "INACTIVE",
        "1" if V17_CORE_INTELLIGENCE_ACTIVE else "0",
    )


@app.on_event("startup")
async def log_kalshi_weather_v2_activation():
    _kalshi_weather_logger.warning(
        "WOW_KALSHI_WEATHER_V2_RUNTIME status=%s mode=SHADOW probability_publishable=false can_execute=false",
        "ACTIVE" if KALSHI_WEATHER_V2_ACTIVE else "INACTIVE",
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
async def schedule_v17_synthetic_self_acceptance():
    """Optionally prove the deployed HTTP boundary fails closed (no certified
    model, no fabricated probability, can_execute=false) for an unsupported
    sport, without requiring any external caller to reach this service."""
    if os.getenv("WOW_V17_SYNTHETIC_ACCEPTANCE", "0") != "1":
        return

    async def _run_after_startup():
        try:
            delay_seconds = int(os.getenv("WOW_V17_SYNTHETIC_ACCEPTANCE_DELAY_SECONDS", "240"))
        except ValueError:
            delay_seconds = 240
        delay_seconds = max(0, min(delay_seconds, 600))
        if delay_seconds:
            _v17_logger.warning(
                "WOW_V17_SYNTHETIC_ACCEPTANCE status=DELAYED seconds=%s can_execute=false",
                delay_seconds,
            )
            await asyncio.sleep(float(delay_seconds))
        await run_v17_synthetic_self_acceptance(_v17_logger)

    task = asyncio.create_task(_run_after_startup())
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
    try:
        initial_delay_seconds = int(
            os.getenv("WOW_MLB_1IP_FINAL_REFRESH_INITIAL_DELAY_SECONDS", "30")
        )
    except ValueError:
        initial_delay_seconds = 30
    task = asyncio.create_task(
        run_mlb_1ip_refresh_loop(
            db_client_fn=_db_client,
            logger=_mlb_1ip_refresh_logger,
            interval_seconds=interval_seconds,
            initial_delay_seconds=initial_delay_seconds,
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
