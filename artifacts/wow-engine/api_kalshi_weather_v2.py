from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from kalshi_weather_v2.persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError
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
    capability_status = "UNAVAILABLE"
    database_status = "UNAVAILABLE"
    try:
        capability = _persistence().load_runtime_capability()
        capability_status = str(capability.get("capability_status") or "UNAVAILABLE")
        database_status = "AVAILABLE"
    except Exception:
        pass
    return {
        "status": "ok",
        "service": "KALSHI_WEATHER_V2",
        "mode": "SHADOW",
        "database_status": database_status,
        "governed_probability_capability": capability_status,
        "probability_publishable": capability_status == "AVAILABLE",
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
