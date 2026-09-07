"""Non-lossy bridge from the existing /wow/kalshi/weather/evaluate evidence envelope.

This module does not trust legacy heuristic lower bounds. It converts only weather
identity/forecast/observation evidence into the V17 probability input contract;
calibration evidence must be supplied separately from a certified calibration cohort.
"""
from __future__ import annotations

from typing import Any

from .core import score_weather_contract
from .registry import REGISTRY_VERSION, validate_station
from .snapshots import freeze_forecast_snapshot, freeze_observation_snapshot


def build_v17_payload_from_legacy_evaluate(
    legacy: dict[str, Any],
    contract: dict[str, Any],
    *,
    calibration: dict[str, Any] | None = None,
    station_error_profiles: list[dict[str, Any]] | None = None,
    regimes: list[dict[str, Any]] | None = None,
    scored_at: str | None = None,
) -> dict[str, Any]:
    city_code = str(legacy.get("city") or "").upper()
    station_id = str(legacy.get("station") or "").upper()
    series = legacy.get("series")
    station = validate_station(city_code, station_id, series)

    forecast_high = legacy.get("forecast_high")
    forecasts = []
    if forecast_high is not None:
        forecast_row = {
            "station_id": station_id,
            "source_family": str(legacy.get("weather_data_source_tier") or "OFFICIAL_GRIDPOINT").upper(),
            "model_name": str(legacy.get("forecast_source") or "LEGACY_EVALUATE_FORECAST"),
            "forecast_high_f": float(forecast_high),
            "forecast_horizon_hours": legacy.get("forecast_horizon_hours"),
            "source_quality": 1.0 if str(legacy.get("weather_data_source_tier") or "").lower().startswith("nws") else .75,
        }
        forecast_ts = legacy.get("forecast_timestamp") or scored_at
        if forecast_ts:
            forecast_row["retrieved_at"] = forecast_ts
        forecasts.append(freeze_forecast_snapshot(forecast_row))

    observations = []
    observed_high = legacy.get("observed_high")
    observation_ts = legacy.get("cli_issuance_time") or scored_at
    if observed_high is not None and observation_ts:
        # Existing evaluate output may represent an official CLI max. Preserve it
        # as evidence and as the hard support floor; never invent a timestamp or
        # infer an unreported current temperature path.
        observations.append(freeze_observation_snapshot({
            "station_id": station_id,
            "observed_at": observation_ts,
            "temperature_f": float(observed_high),
            "maximum_observed_so_far_f": float(observed_high),
            "source": legacy.get("cli_source_url") or "NWS_CLI",
            "quality_flag": legacy.get("report_status"),
            "correction_flag": bool(legacy.get("revision_risk")),
        }))

    return {
        "settlement_identity": {
            "city": station["city"],
            "city_code": city_code,
            "series": station["series"],
            "station_id": station_id,
            "settlement_station_verified": True,
            "settlement_source": "NWS_CLI_OFFICIAL_CLIMATE_PRODUCT",
            "registry_version": REGISTRY_VERSION,
            "timezone": station["timezone"],
        },
        "forecast_snapshots": forecasts,
        "observations": observations,
        "maximum_observed_so_far_f": float(observed_high) if observed_high is not None else None,
        "station_error_profiles": list(station_error_profiles or []),
        "regimes": list(regimes or []),
        "contract": dict(contract),
        "calibration": calibration,
        "scored_at": scored_at,
        "legacy_evidence_reference": {
            "scoring_mode": legacy.get("scoring_mode"),
            "report_status": legacy.get("report_status"),
            "cli_product_id": legacy.get("cli_product_id"),
            "cli_issuance_time": legacy.get("cli_issuance_time"),
            "model_prob_sum": legacy.get("model_prob_sum"),
        },
    }


def score_from_legacy_evaluate(
    legacy: dict[str, Any],
    contract: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    payload = build_v17_payload_from_legacy_evaluate(legacy, contract, **kwargs)
    result = score_weather_contract(payload)
    result["legacy_bridge_used"] = True
    result["legacy_heuristic_lower_bound_consumed"] = False
    return result
