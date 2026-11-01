from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Optional

import httpx

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from kalshi_weather_v2.research_release_a import install_release_a_research
from kalshi_weather_v2.research_backfill import BackfillDataError, reconcile_miami_settlements
from kalshi_weather_v2.persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError
from kalshi_weather_v2.empirical_runtime import install_empirical_cohort_scheduler
from kalshi_weather_v2.isolated_capture_wiring import install_isolated_capture_if_owned
from kalshi_weather_v2.runtime import (
    DEFAULT_OPEN_METEO_MODELS,
    KalshiWeatherRuntimeError,
    capture_hourly_shadow,
    settle_hourly_prediction,
)
from ledger import get_client


app = FastAPI(
    title="WOW Kalshi Weather V2 Analytical Runtime",
    version="2.0.0-shadow",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# Attach the existing bounded cohort controller only when this isolated runtime
# explicitly owns acquisition. Default is OFF, including when a global
# empirical-cohort flag is accidentally inherited from the shared V17 service.
# Deployment must separately verify that no other live collector is writing.
ISOLATED_CAPTURE = install_isolated_capture_if_owned(
    app,
    db_client_fn=get_client,
    installer=install_empirical_cohort_scheduler,
)


@app.get("/collector/readiness", operation_id="kalshiWeatherCollectorReadiness")
def collector_readiness():
    """Configuration status, not proof of fresh evidence or writer fencing."""
    return {
        **ISOLATED_CAPTURE,
        "mode": "SHADOW",
        "production_single_writer_evidence": "NOT_ATTESTED_BY_ENDPOINT",
        "settlement_station_verified": False,
        "independent_model_probability_available": False,
        "probability_publishable": False,
        "can_execute": False,
    }


class HourlyShadowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, max_length=200)
    index_city: str = Field(min_length=1, max_length=80)
    expected_location: str = Field(min_length=1, max_length=160)
    forecast_latitude: float = Field(ge=-90.0, le=90.0)
    forecast_longitude: float = Field(ge=-180.0, le=180.0)
    open_meteo_models: list[str] = Field(default_factory=lambda: list(DEFAULT_OPEN_METEO_MODELS), min_length=1, max_length=4)


class HourlySettleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prediction_id: str = Field(min_length=1, max_length=200)
    index_city: str = Field(min_length=1, max_length=80)


def _require_api_key(authorization: Optional[str] = Header(default=None, alias="Authorization")) -> None:
    configured = os.getenv("WOW_KALSHI_WEATHER_API_KEY")
    if not configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "KALSHI_WEATHER_API_AUTH_NOT_CONFIGURED",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    prefix = "Bearer "
    supplied = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "probability_publishable": False,
                "can_execute": False,
            },
        )


def _persistence() -> KalshiWeatherPersistence:
    return KalshiWeatherPersistence(get_client())


@app.get("/health", operation_id="kalshiWeatherHealth")
def health():
    """Typed, secret-free database-readiness signal for isolated operations.

    A public 200 means the API process is available, NOT that the ledger is
    accessible. Never echo URL, service key, query data or exception text.
    """
    capability_status = "UNAVAILABLE"
    database_status = "UNAVAILABLE"
    missing = tuple(
        name for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
        if not os.environ.get(name)
    )
    if missing:
        database_diagnostic = "DATABASE_CONFIGURATION_INCOMPLETE"
    else:
        try:
            capability = _persistence().load_runtime_capability()
            capability_status = str(capability.get("capability_status") or "UNAVAILABLE")
            database_status = "AVAILABLE"
            database_diagnostic = "DATABASE_READ_VERIFIED"
        except KalshiWeatherPersistenceError:
            database_diagnostic = "DATABASE_CAPABILITY_READ_FAILED"
        except Exception:
            database_diagnostic = "DATABASE_CLIENT_OR_READ_FAILED"
    return {
        "status": "ok",
        "service": "KALSHI_WEATHER_V2",
        "mode": "SHADOW",
        "database_status": database_status,
        "database_diagnostic": database_diagnostic,
        "governed_probability_capability": capability_status,
        "probability_publishable": False,  # health/readiness is never publication authority
        "can_execute": False,
    }


@app.get("/governance", operation_id="kalshiWeatherGovernance")
def governance():
    try:
        capability = _persistence().load_runtime_capability()
    except Exception:
        capability = {
            "capability_key": "KALSHI_WEATHER_PROBABILITY",
            "capability_status": "UNAVAILABLE",
            "evidence": {"reason": "CAPABILITY_LEDGER_UNAVAILABLE"},
            "can_execute": False,
        }
    return {
        "service": "KALSHI_WEATHER_V2",
        "mode": "SHADOW",
        "capability": capability,
        "invariants": {
            "market_price_used_as_weather_probability": False,
            "caller_supplied_decision_time_allowed": False,
            "order_placement_available": False,
            "probability_requires_certified_calibration": True,
            "probability_requires_capability_available": True,
            "can_execute": False,
        },
        "can_execute": False,
    }


@app.post(
    "/v2/hourly/shadow",
    dependencies=[Depends(_require_api_key)],
    operation_id="captureKalshiHourlyWeatherShadow",
)
def hourly_shadow(req: HourlyShadowRequest):
    try:
        return capture_hourly_shadow(
            client=get_client(),
            ticker=req.ticker,
            index_city=req.index_city,
            expected_location=req.expected_location,
            forecast_latitude=req.forecast_latitude,
            forecast_longitude=req.forecast_longitude,
            open_meteo_models=req.open_meteo_models,
        )
    except Exception as exc:
        _raise_governed(exc)


@app.post(
    "/v2/hourly/settle",
    dependencies=[Depends(_require_api_key)],
    operation_id="settleKalshiHourlyWeatherShadow",
)
def hourly_settle(req: HourlySettleRequest):
    try:
        return settle_hourly_prediction(
            client=get_client(),
            prediction_id=req.prediction_id,
            index_city=req.index_city,
        )
    except Exception as exc:
        _raise_governed(exc)


# Read-only private research UI. Uses an independent research-only password
# through HTTP Basic; never activates the writer-capable weather API key.
install_release_a_research(app, lambda: get_client())


def _raise_governed(exc: Exception) -> None:
    if isinstance(exc, KalshiWeatherRuntimeError):
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "blockers": list(exc.blockers),
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc
    if isinstance(exc, KalshiWeatherPersistenceError):
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "blockers": [exc.detail],
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    code = str(getattr(exc, "code", "KALSHI_WEATHER_RUNTIME_UNAVAILABLE"))
    blockers = getattr(exc, "blockers", None)
    if blockers is None:
        detail = getattr(exc, "detail", None)
        blockers = [str(detail)] if detail else [type(exc).__name__]
    raise HTTPException(
        status_code=409,
        detail={
            "code": code,
            "blockers": list(blockers),
            "probability_publishable": False,
            "can_execute": False,
        },
    ) from exc


# Research-only Trigger.dev integration. Independent of the browser's read-only
# password and the weather shadow-capture writer credential.
class MiamiBackfillLaunch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(pattern=r"^miami$")


def _require_scoped_bearer(env_name: str, authorization: Optional[str]) -> None:
    configured = os.getenv(env_name, "")
    if not configured:
        raise HTTPException(status_code=503, detail="RESEARCH_JOB_AUTH_NOT_CONFIGURED")
    prefix = "Bearer "
    token = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
    if not token or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=401, detail="RESEARCH_JOB_AUTH_REQUIRED")


def _require_backfill_request_key(authorization: Optional[str] = Header(default=None)) -> None:
    _require_scoped_bearer("WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY", authorization)


def _require_backfill_worker_key(authorization: Optional[str] = Header(default=None)) -> None:
    _require_scoped_bearer("WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY", authorization)


@app.post(
    "/research/backfill",
    status_code=202,
    dependencies=[Depends(_require_backfill_request_key)],
    include_in_schema=False,
)
def launch_miami_research_backfill(req: MiamiBackfillLaunch):
    """Launch one tiny Trigger.dev research task, never a forecast or trade."""
    trigger_key = os.getenv("WOW_KALSHI_WEATHER_TRIGGER_SECRET_KEY", "")
    if not trigger_key:
        raise HTTPException(status_code=503, detail="TRIGGER_RESEARCH_NOT_CONFIGURED")
    # One request per five-minute bucket; source-ledger uniqueness also guards
    # retries, later replays and overlapping launches beyond Trigger's TTL.
    stamp = datetime.now(timezone.utc)
    five_minute = stamp.replace(minute=(stamp.minute // 5) * 5, second=0, microsecond=0)
    try:
        result = httpx.post(
            "https://api.trigger.dev/api/v1/tasks/weather-research-backfill/trigger",
            headers={"Authorization": f"Bearer {trigger_key}", "Content-Type": "application/json"},
            json={
                "payload": {"scope": "miami"},
                "options": {
                    "idempotencyKey": f"kalshi-miami-backfill-{five_minute.isoformat()}",
                    "idempotencyKeyTTL": "5m",
                },
            },
            timeout=12.0,
        )
        if result.status_code not in (200, 201, 202):
            raise ValueError("TRIGGER_UPSTREAM_STATUS")
        run_id = result.json().get("id")
        if not isinstance(run_id, str) or not run_id.startswith("run_"):
            raise ValueError("TRIGGER_RUN_ID_MISSING")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="TRIGGER_RESEARCH_LAUNCH_UNAVAILABLE") from exc
    return {
        "status": "RESEARCH_TASK_ACCEPTED",
        "run_id": run_id,
        "scope": "miami",
        "probability_publishable": False,
        "can_execute": False,
    }


@app.post(
    "/internal/research/miami-settlement-reconcile",
    dependencies=[Depends(_require_backfill_worker_key)],
    include_in_schema=False,
)
def run_miami_research_backfill(req: MiamiBackfillLaunch):
    """Worker-only, bounded reconciliation of previously captured Miami events."""
    try:
        return reconcile_miami_settlements(client=get_client())
    except BackfillDataError as exc:
        raise HTTPException(status_code=503, detail="RESEARCH_BACKFILL_UNAVAILABLE") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="RESEARCH_BACKFILL_UNAVAILABLE") from exc
