from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .empirical_runtime import install_empirical_cohort_scheduler
from .free_public_sources import source_registry_snapshot
from .persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError
from .runtime import DEFAULT_OPEN_METEO_MODELS, KalshiWeatherRuntimeError, capture_hourly_shadow, settle_hourly_prediction
from .shadow_cohort import build_calibration_report, run_hourly_shadow_cohort_once
from v17.kalshi_weather_governance import governance_snapshot, reduce_kalshi_weather_prediction_v17


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


class WeatherPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prediction_id: str = Field(min_length=1, max_length=200)


class WeatherAnalyzeRequest(BaseModel):
    """V17 ingress for any declared Weather family; unsupported lanes fail closed."""

    model_config = ConfigDict(extra="forbid")

    lane: str = Field(min_length=1, max_length=80)
    ticker: str = Field(min_length=1, max_length=200)
    index_city: str | None = Field(default=None, max_length=80)
    expected_location: str | None = Field(default=None, max_length=160)
    forecast_latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    forecast_longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    open_meteo_models: list[str] = Field(default_factory=lambda: list(DEFAULT_OPEN_METEO_MODELS), min_length=1, max_length=4)


def install_kalshi_weather_v2_routes(
    app: FastAPI,
    *,
    auth_dependency,
    db_client_fn: Callable[[], object],
) -> None:
    """Mount fail-closed Weather V2 compatibility plus canonical V17 routes."""
    existing = {getattr(route, "path", None) for route in app.router.routes}

    if "/kalshi-weather/v2/governance" not in existing:
        @app.get(
            "/kalshi-weather/v2/governance",
            operation_id="getKalshiWeatherV2Governance",
        )
        def governance():
            try:
                capability = KalshiWeatherPersistence(db_client_fn()).load_runtime_capability()
            except Exception:
                capability = {
                    "capability_key": "KALSHI_WEATHER_PROBABILITY",
                    "capability_status": "UNAVAILABLE",
                    "evidence": {"reason": "CAPABILITY_LEDGER_UNAVAILABLE"},
                    "can_execute": False,
                }
            return {
                "service": "KALSHI_WEATHER_V2",
                "mode": "SHADOW_COMPATIBILITY",
                "capability": capability,
                "invariants": {
                    "market_price_used_as_weather_probability": False,
                    "caller_supplied_decision_time_allowed": False,
                    "order_placement_available": False,
                    "probability_requires_certified_calibration": True,
                    "probability_requires_capability_available": True,
                    "local_terminal_label_audit_only": True,
                    "global_terminal_authority": "V17_TERMINAL_REDUCER",
                    "can_execute": False,
                },
                "can_execute": False,
            }

    if "/kalshi-weather/v17/governance" not in existing:
        @app.get(
            "/kalshi-weather/v17/governance",
            operation_id="getKalshiWeatherV17Governance",
        )
        def governance_v17():
            try:
                return governance_snapshot(client=db_client_fn())
            except Exception as exc:
                _raise_governed(exc)

    if "/kalshi-weather/v17/free-sources" not in existing:
        @app.get(
            "/kalshi-weather/v17/free-sources",
            dependencies=[auth_dependency],
            operation_id="getKalshiWeatherV17FreeSources",
        )
        def free_sources_v17():
            return {
                "status": "FREE_PUBLIC_SOURCE_REGISTRY",
                "sources": source_registry_snapshot(),
                "settlement_source_override_allowed": False,
                "probability_publishable": False,
                "can_execute": False,
            }

    if "/kalshi-weather/v2/hourly/shadow" not in existing:
        @app.post(
            "/kalshi-weather/v2/hourly/shadow",
            dependencies=[auth_dependency],
            operation_id="captureKalshiWeatherV2HourlyShadow",
        )
        def hourly_shadow(req: HourlyShadowRequest):
            return _capture_hourly(req, db_client_fn)

    if "/kalshi-weather/v17/hourly/shadow" not in existing:
        @app.post(
            "/kalshi-weather/v17/hourly/shadow",
            dependencies=[auth_dependency],
            operation_id="captureKalshiWeatherV17HourlyShadow",
        )
        def hourly_shadow_v17(req: HourlyShadowRequest):
            return _capture_hourly(req, db_client_fn)

    if "/kalshi-weather/v17/analyze" not in existing:
        @app.post(
            "/kalshi-weather/v17/analyze",
            dependencies=[auth_dependency],
            operation_id="analyzeKalshiWeatherV17Contract",
        )
        def analyze_v17(req: WeatherAnalyzeRequest):
            lane = req.lane.strip().upper()
            if lane != "HOURLY_TEMPERATURE":
                return {
                    "status": "NO_PLAY_DATA_INSUFFICIENT",
                    "code": "WEATHER_LANE_RUNTIME_NOT_CERTIFIED",
                    "lane": lane,
                    "ticker": req.ticker,
                    "p_yes": None,
                    "p_no": None,
                    "probability_publishable": False,
                    "edge_publishable": False,
                    "rank_eligible": False,
                    "blockers": [f"LANE_RUNTIME_NOT_CERTIFIED:{lane}"],
                    "global_terminal_authority": "V17_TERMINAL_REDUCER",
                    "controlling_specialist": "KALSHI_WEATHER_MARKET_EXPERT",
                    "can_execute": False,
                }
            missing = [
                name
                for name, value in (
                    ("index_city", req.index_city),
                    ("expected_location", req.expected_location),
                    ("forecast_latitude", req.forecast_latitude),
                    ("forecast_longitude", req.forecast_longitude),
                )
                if value is None or value == ""
            ]
            if missing:
                return {
                    "status": "NO_PLAY_DATA_INSUFFICIENT",
                    "code": "HOURLY_WEATHER_INPUTS_INSUFFICIENT",
                    "lane": lane,
                    "ticker": req.ticker,
                    "p_yes": None,
                    "p_no": None,
                    "probability_publishable": False,
                    "edge_publishable": False,
                    "rank_eligible": False,
                    "blockers": [f"MISSING_INPUT:{name}" for name in missing],
                    "global_terminal_authority": "V17_TERMINAL_REDUCER",
                    "controlling_specialist": "KALSHI_WEATHER_MARKET_EXPERT",
                    "can_execute": False,
                }
            capture = _capture_hourly(
                HourlyShadowRequest(
                    ticker=req.ticker,
                    index_city=str(req.index_city),
                    expected_location=str(req.expected_location),
                    forecast_latitude=float(req.forecast_latitude),
                    forecast_longitude=float(req.forecast_longitude),
                    open_meteo_models=req.open_meteo_models,
                ),
                db_client_fn,
            )
            decision = reduce_kalshi_weather_prediction_v17(
                client=db_client_fn(),
                prediction_id=str(capture["prediction_id"]),
            )
            return {
                "capture": capture,
                "decision": decision,
                "immutable_write_precedes_publication": True,
                "global_terminal_authority": "V17_TERMINAL_REDUCER",
                "can_execute": False,
            }

    if "/kalshi-weather/v17/publish" not in existing:
        @app.post(
            "/kalshi-weather/v17/publish",
            dependencies=[auth_dependency],
            operation_id="publishKalshiWeatherV17Prediction",
        )
        def publish_v17(req: WeatherPublishRequest):
            try:
                return reduce_kalshi_weather_prediction_v17(
                    client=db_client_fn(),
                    prediction_id=req.prediction_id,
                )
            except Exception as exc:
                _raise_governed(exc)

    if "/kalshi-weather/v2/hourly/settle" not in existing:
        @app.post(
            "/kalshi-weather/v2/hourly/settle",
            dependencies=[auth_dependency],
            operation_id="settleKalshiWeatherV2HourlyShadow",
        )
        def hourly_settle(req: HourlySettleRequest):
            return _settle_hourly(req, db_client_fn)

    if "/kalshi-weather/v17/hourly/settle" not in existing:
        @app.post(
            "/kalshi-weather/v17/hourly/settle",
            dependencies=[auth_dependency],
            operation_id="settleKalshiWeatherV17HourlyPrediction",
        )
        def hourly_settle_v17(req: HourlySettleRequest):
            return _settle_hourly(req, db_client_fn)

    if "/internal/kalshi-weather/v17/empirical-cohort/run" not in existing:
        @app.post(
            "/internal/kalshi-weather/v17/empirical-cohort/run",
            dependencies=[auth_dependency],
            operation_id="runKalshiWeatherV17EmpiricalCohort",
        )
        def empirical_cohort_run():
            try:
                result = run_hourly_shadow_cohort_once(db_client_fn=db_client_fn)
                return {
                    **result.__dict__,
                    "global_terminal_authority": "V17_TERMINAL_REDUCER",
                }
            except Exception as exc:
                _raise_governed(exc)

    if "/internal/kalshi-weather/v17/calibration-report" not in existing:
        @app.get(
            "/internal/kalshi-weather/v17/calibration-report",
            dependencies=[auth_dependency],
            operation_id="getKalshiWeatherV17CalibrationReport",
        )
        def empirical_calibration_report():
            try:
                return build_calibration_report(client=db_client_fn())
            except Exception as exc:
                _raise_governed(exc)

    install_empirical_cohort_scheduler(app, db_client_fn=db_client_fn)


def _capture_hourly(req: HourlyShadowRequest, db_client_fn: Callable[[], object]):
    try:
        return capture_hourly_shadow(
            client=db_client_fn(),
            ticker=req.ticker,
            index_city=req.index_city,
            expected_location=req.expected_location,
            forecast_latitude=req.forecast_latitude,
            forecast_longitude=req.forecast_longitude,
            open_meteo_models=req.open_meteo_models,
        )
    except Exception as exc:
        _raise_governed(exc)


def _settle_hourly(req: HourlySettleRequest, db_client_fn: Callable[[], object]):
    try:
        return settle_hourly_prediction(
            client=db_client_fn(),
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
