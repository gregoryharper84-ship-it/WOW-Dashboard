import pytest

from kalshi_weather_v2.hourly_forecast_fusion import (
    HourlyForecastFusionError,
    build_hourly_weather_evidence,
)
from kalshi_weather_v2.models import ContractSnapshot
from kalshi_weather_v2.source_adapters import ProviderSnapshot


def contract():
    return ContractSnapshot(
        market_title="Temperature in New York City today at 1pm EDT?",
        contract_title="82° or above",
        ticker="KXTEMPNYCHS-TEST",
        lane="HOURLY_TEMPERATURE",
        yes_condition="index > 81.99°F",
        no_condition="index <= 81.99°F",
        location="New York City",
        metric="kalshi_weather_index_point_temperature",
        units="F",
        observation_window="POINT_IN_TIME:2026-09-10T17:00:00Z",
        timezone="EDT(UTC-04:00)",
        settlement_source="Synoptic Data",
        settlement_station_id=None,
        settlement_station_name=None,
        rounding_convention="USE_OFFICIAL_KALSHI_WEATHER_INDEX_AS_PUBLISHED;NO_ENGINE_ROUNDING",
        trace_measurement_rules="POINT_INDEX_VALUE;PRESERVE_INCOMPLETE_MINUTES;NO_STATION_SUBSTITUTION",
        market_close_time="2026-09-10T18:00:00Z",
        rule_snapshot_id="rule-1",
        threshold_lower=81.99,
        lower_inclusive=False,
        settlement_location_type="SOURCE_LOCATION_CODE",
        settlement_location_code="KALSHI_WEATHER_INDEX:NYC",
    )


def nws(temp=82):
    return ProviderSnapshot(
        provider="NWS",
        role="PRIMARY_FORECAST",
        source_id="https://api.weather.gov/gridpoints/OKX/33,37/forecast/hourly",
        retrieved_at="2026-09-10T16:05:00Z",
        issued_at="2026-09-10T15:55:00Z",
        valid_times=("2026-09-10T13:00:00-04:00",),
        payload={
            "properties": {
                "generatedAt": "2026-09-10T15:55:00Z",
                "periods": [
                    {
                        "startTime": "2026-09-10T13:00:00-04:00",
                        "temperature": temp,
                        "temperatureUnit": "F",
                    }
                ],
            }
        },
    )


def om(gfs=81.5, ecmwf=82.5):
    return ProviderSnapshot(
        provider="OPEN_METEO",
        role="SECONDARY_FORECAST",
        source_id="https://api.open-meteo.com/v1/forecast?...",
        retrieved_at="2026-09-10T16:06:00Z",
        issued_at=None,
        valid_times=("2026-09-10T17:00",),
        payload={
            "timezone": "GMT",
            "utc_offset_seconds": 0,
            "hourly_units": {
                "temperature_2m_gfs_seamless": "°F",
                "temperature_2m_ecmwf_ifs025": "°F",
            },
            "hourly": {
                "time": ["2026-09-10T17:00"],
                "temperature_2m_gfs_seamless": [gfs],
                "temperature_2m_ecmwf_ifs025": [ecmwf],
            },
        },
    )


def test_two_provider_exact_target_fusion_is_complete_and_market_blind():
    evidence = build_hourly_weather_evidence(
        contract=contract(),
        analysis_time="2026-09-10T16:10:00Z",
        nws_snapshot=nws(82),
        open_meteo_snapshot=om(81.5, 82.5),
        settlement_source_verified=True,
        settlement_location_verified=True,
    )
    assert evidence.evidence_complete is True
    assert evidence.providers == ("NWS", "OPEN_METEO")
    assert evidence.central_estimate == 82.0
    assert evidence.disagreement_magnitude == 1.0
    assert evidence.temporal_provenance_verified is True
    assert evidence.settlement_source_verified is True
    assert evidence.settlement_location_verified is True
    assert evidence.notes["market_price_used_as_input"] is False
    assert evidence.notes["fusion_method"] == "DETERMINISTIC_MEDIAN_EXACT_TARGET"
    assert len(evidence.source_snapshot_ids) == 2


def test_one_provider_can_be_represented_but_is_not_complete():
    evidence = build_hourly_weather_evidence(
        contract=contract(),
        analysis_time="2026-09-10T16:10:00Z",
        nws_snapshot=nws(82),
        open_meteo_snapshot=None,
        settlement_source_verified=True,
        settlement_location_verified=True,
    )
    assert evidence.central_estimate == 82.0
    assert evidence.evidence_complete is False
    assert evidence.providers == ("NWS",)


def test_open_meteo_models_do_not_fake_two_provider_completeness():
    evidence = build_hourly_weather_evidence(
        contract=contract(),
        analysis_time="2026-09-10T16:10:00Z",
        nws_snapshot=None,
        open_meteo_snapshot=om(81.5, 82.5),
        settlement_source_verified=True,
        settlement_location_verified=True,
    )
    assert evidence.evidence_complete is False
    assert evidence.notes["provider_count"] == 1


def test_missing_exact_target_fails_closed():
    bad = ProviderSnapshot(
        provider="NWS",
        role="PRIMARY_FORECAST",
        source_id="nws",
        retrieved_at="2026-09-10T16:00:00Z",
        issued_at="2026-09-10T15:00:00Z",
        valid_times=("2026-09-10T12:00:00-04:00",),
        payload={
            "properties": {
                "periods": [
                    {
                        "startTime": "2026-09-10T12:00:00-04:00",
                        "temperature": 81,
                        "temperatureUnit": "F",
                    }
                ]
            }
        },
    )
    with pytest.raises(HourlyForecastFusionError) as exc:
        build_hourly_weather_evidence(
            contract=contract(),
            analysis_time="2026-09-10T16:10:00Z",
            nws_snapshot=bad,
            open_meteo_snapshot=None,
            settlement_source_verified=True,
            settlement_location_verified=True,
        )
    assert "NWS_EXACT_TARGET_MISSING" in exc.value.blockers


def test_analysis_after_target_fails_closed():
    with pytest.raises(HourlyForecastFusionError) as exc:
        build_hourly_weather_evidence(
            contract=contract(),
            analysis_time="2026-09-10T17:01:00Z",
            nws_snapshot=nws(),
            open_meteo_snapshot=om(),
            settlement_source_verified=True,
            settlement_location_verified=True,
        )
    assert "ANALYSIS_AFTER_TARGET" in exc.value.blockers


def test_forecast_fusion_never_claims_station_identity_for_index_contract():
    evidence = build_hourly_weather_evidence(
        contract=contract(),
        analysis_time="2026-09-10T16:10:00Z",
        nws_snapshot=nws(),
        open_meteo_snapshot=om(),
        settlement_source_verified=True,
        settlement_location_verified=True,
    )
    assert evidence.station_identity_verified is False
