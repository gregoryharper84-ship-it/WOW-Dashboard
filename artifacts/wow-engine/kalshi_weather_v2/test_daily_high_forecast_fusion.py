from __future__ import annotations

import pytest

from .daily_high_forecast_fusion import DailyHighForecastFusionError, build_daily_high_weather_evidence
from .models import ContractSnapshot
from .source_adapters import OpenMeteoAdapter, ProviderSnapshot


def contract() -> ContractSnapshot:
    return ContractSnapshot(
        market_title="NYC daily high",
        contract_title="90F or above",
        ticker="KXHIGHNY-TEST-T90",
        lane="DAILY_HIGH_TEMPERATURE",
        yes_condition="temperature is greater than 89°F",
        no_condition="NOT (temperature is greater than 89°F)",
        location="New York City",
        metric="daily_max_temperature",
        units="F",
        observation_window="2026-09-10 local day",
        timezone="America/New_York",
        settlement_source="The Weather Company",
        settlement_station_id="KNYC",
        settlement_station_name="Central Park",
        rounding_convention="whole degree per contract",
        trace_measurement_rules="not applicable",
        market_close_time="2026-09-10T23:00:00Z",
        rule_snapshot_id="rule-1",
        threshold_lower=89.0,
        threshold_upper=None,
        lower_inclusive=False,
        upper_inclusive=True,
        settlement_location_type="STATION",
        settlement_location_code="KNYC",
    )


def snapshot(provider, role, source_id, retrieved_at, payload, issued_at=None):
    return ProviderSnapshot(
        provider=provider,
        role=role,
        source_id=source_id,
        retrieved_at=retrieved_at,
        issued_at=issued_at,
        valid_times=(),
        payload=payload,
    )


def test_daily_high_is_market_blind_and_reconstructs_observed_max():
    nws_hourly = snapshot(
        "NWS",
        "PRIMARY_FORECAST",
        "nws-hourly",
        "2026-09-10T16:00:00Z",
        {"properties": {"periods": [
            {"startTime": "2026-09-10T11:00:00-04:00", "temperature": 86, "temperatureUnit": "F"},
            {"startTime": "2026-09-10T14:00:00-04:00", "temperature": 91, "temperatureUnit": "F"},
            {"startTime": "2026-09-11T13:00:00-04:00", "temperature": 95, "temperatureUnit": "F"},
        ]}},
        issued_at="2026-09-10T15:55:00Z",
    )
    observations = snapshot(
        "NWS",
        "OFFICIAL_OBSERVATION",
        "nws-obs",
        "2026-09-10T16:00:00Z",
        {"features": [
            {"id": "o1", "properties": {"timestamp": "2026-09-10T14:00:00Z", "temperature": {"value": 28.0, "unitCode": "wmoUnit:degC"}}},
            {"id": "o2", "properties": {"timestamp": "2026-09-10T16:00:00Z", "temperature": {"value": 30.0, "unitCode": "wmoUnit:degC"}}},
        ]},
    )
    open_meteo = snapshot(
        "OPEN_METEO",
        "SECONDARY_FORECAST",
        "om",
        "2026-09-10T16:00:00Z",
        {"daily": {
            "time": ["2026-09-10"],
            "temperature_2m_max_gfs_seamless": [90.0],
            "temperature_2m_max_ecmwf_ifs025": [92.0],
        }, "daily_units": {"temperature_2m_max": "°F"}},
    )

    evidence = build_daily_high_weather_evidence(
        contract=contract(),
        analysis_time="2026-09-10T16:01:00Z",
        local_date="2026-09-10",
        nws_hourly_snapshot=nws_hourly,
        nws_observation_snapshot=observations,
        open_meteo_snapshot=open_meteo,
        settlement_source_verified=True,
        settlement_location_verified=True,
    )

    assert evidence.central_estimate == 91.0
    assert evidence.observed_extreme_so_far == pytest.approx(86.0)
    assert evidence.evidence_complete is True
    assert evidence.station_identity_verified is True
    assert evidence.notes["market_price_used_as_input"] is False
    assert evidence.notes["central_estimate_method"] == "NWS_HOURLY_TARGET_LOCAL_DATE_MAX"
    assert evidence.disagreement_magnitude > 0


def test_future_source_timestamp_fails_closed():
    nws_hourly = snapshot(
        "NWS", "PRIMARY_FORECAST", "nws-hourly", "2026-09-10T16:02:00Z",
        {"properties": {"periods": [{"startTime": "2026-09-10T14:00:00-04:00", "temperature": 91, "temperatureUnit": "F"}]}}
    )
    observations = snapshot(
        "NWS", "OFFICIAL_OBSERVATION", "nws-obs", "2026-09-10T16:00:00Z",
        {"features": [{"id": "o1", "properties": {"timestamp": "2026-09-10T15:00:00Z", "temperature": {"value": 29.0, "unitCode": "wmoUnit:degC"}}}]}
    )
    with pytest.raises(DailyHighForecastFusionError) as exc:
        build_daily_high_weather_evidence(
            contract=contract(), analysis_time="2026-09-10T16:01:00Z", local_date="2026-09-10",
            nws_hourly_snapshot=nws_hourly, nws_observation_snapshot=observations,
            open_meteo_snapshot=None, settlement_source_verified=True, settlement_location_verified=True,
        )
    assert "RETRIEVED_AFTER_ANALYSIS" in str(exc.value)


def test_empty_observation_series_fails_closed():
    nws_hourly = snapshot(
        "NWS", "PRIMARY_FORECAST", "nws-hourly", "2026-09-10T16:00:00Z",
        {"properties": {"periods": [{"startTime": "2026-09-10T14:00:00-04:00", "temperature": 91, "temperatureUnit": "F"}]}}
    )
    observations = snapshot("NWS", "OFFICIAL_OBSERVATION", "nws-obs", "2026-09-10T16:00:00Z", {"features": []})
    with pytest.raises(DailyHighForecastFusionError) as exc:
        build_daily_high_weather_evidence(
            contract=contract(), analysis_time="2026-09-10T16:01:00Z", local_date="2026-09-10",
            nws_hourly_snapshot=nws_hourly, nws_observation_snapshot=observations,
            open_meteo_snapshot=None, settlement_source_verified=True, settlement_location_verified=True,
        )
    assert "OFFICIAL_OBSERVATION_SERIES_EMPTY" in str(exc.value)


def test_open_meteo_daily_high_supports_contract_timezone():
    urls = []

    def get_json(url, _headers=None):
        urls.append(url)
        return {"daily": {"time": ["2026-09-10"], "temperature_2m_max": [91.0]}}

    OpenMeteoAdapter(get_json).multi_model_daily_highs(
        40.78, -73.97, "2026-09-10", ["gfs_seamless"],
        retrieved_at="2026-09-10T16:00:00Z", timezone_name="America/New_York",
    )
    assert "timezone=America/New_York" in urls[0]
