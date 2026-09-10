from __future__ import annotations

from .evidence_assembler import WeatherEvidenceAssemblyError, assemble_daily_high_evidence
from .source_adapters import ProviderSnapshot


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


def test_daily_high_evidence_uses_nws_central_and_reconstructed_official_max():
    nws_hourly = snapshot(
        "NWS", "PRIMARY_FORECAST", "nws-hourly", "2026-09-10T16:00:00Z",
        {
            "properties": {
                "generatedAt": "2026-09-10T15:55:00Z",
                "periods": [
                    {"startTime": "2026-09-10T11:00:00-04:00", "temperature": 86, "temperatureUnit": "F"},
                    {"startTime": "2026-09-10T14:00:00-04:00", "temperature": 91, "temperatureUnit": "F"},
                    {"startTime": "2026-09-10T16:00:00-04:00", "temperature": 89, "temperatureUnit": "F"},
                ],
            }
        },
        issued_at="2026-09-10T15:55:00Z",
    )
    observations = snapshot(
        "NWS", "OFFICIAL_OBSERVATION", "nws-obs", "2026-09-10T16:01:00Z",
        {
            "features": [
                {"id": "o1", "properties": {"timestamp": "2026-09-10T14:00:00Z", "temperature": {"value": 28.0, "unitCode": "wmoUnit:degC"}}},
                {"id": "o2", "properties": {"timestamp": "2026-09-10T16:00:00Z", "temperature": {"value": 30.0, "unitCode": "wmoUnit:degC"}}},
            ]
        },
    )
    open_meteo = snapshot(
        "OPEN_METEO", "SECONDARY_FORECAST", "om", "2026-09-10T16:01:30Z",
        {"daily": {"time": ["2026-09-10"], "temperature_2m_max_ecmwf": [92.0], "temperature_2m_max_gfs": [90.0]}},
    )

    evidence = assemble_daily_high_evidence(
        analysis_time="2026-09-10T16:02:00Z",
        local_date="2026-09-10",
        timezone_name="America/New_York",
        nws_hourly=nws_hourly,
        nws_observations=observations,
        open_meteo=open_meteo,
        station_identity_verified=True,
        settlement_source_verified=True,
    )

    assert evidence.central_estimate == 91.0
    assert round(evidence.observed_extreme_so_far, 4) == 86.0
    assert evidence.evidence_complete is True
    assert evidence.temporal_provenance_verified is True
    assert evidence.notes["central_estimate_source"] == "NWS_HOURLY_TARGET_DATE_MAX"
    assert evidence.notes["market_price_used"] is False
    assert evidence.disagreement_magnitude > 0


def test_optional_xweather_is_not_required():
    nws_hourly = snapshot(
        "NWS", "PRIMARY_FORECAST", "nws-hourly", "2026-09-10T16:00:00Z",
        {"properties": {"periods": [{"startTime": "2026-09-10T12:00:00-04:00", "temperature": 90, "temperatureUnit": "F"}]}},
    )
    observations = snapshot(
        "NWS", "OFFICIAL_OBSERVATION", "nws-obs", "2026-09-10T16:00:00Z",
        {"features": [{"id": "o1", "properties": {"timestamp": "2026-09-10T15:00:00Z", "temperature": {"value": 29.0, "unitCode": "wmoUnit:degC"}}}]},
    )
    evidence = assemble_daily_high_evidence(
        analysis_time="2026-09-10T16:01:00Z",
        local_date="2026-09-10",
        timezone_name="America/New_York",
        nws_hourly=nws_hourly,
        nws_observations=observations,
        station_identity_verified=True,
        settlement_source_verified=True,
    )
    assert evidence.evidence_complete is True
    assert "XWEATHER" not in evidence.providers


def test_future_retrieval_time_fails_temporal_provenance():
    nws_hourly = snapshot(
        "NWS", "PRIMARY_FORECAST", "nws-hourly", "2026-09-10T16:05:00Z",
        {"properties": {"periods": [{"startTime": "2026-09-10T12:00:00-04:00", "temperature": 90, "temperatureUnit": "F"}]}},
    )
    observations = snapshot(
        "NWS", "OFFICIAL_OBSERVATION", "nws-obs", "2026-09-10T16:00:00Z",
        {"features": [{"id": "o1", "properties": {"timestamp": "2026-09-10T15:00:00Z", "temperature": {"value": 29.0, "unitCode": "wmoUnit:degC"}}}]},
    )
    evidence = assemble_daily_high_evidence(
        analysis_time="2026-09-10T16:01:00Z",
        local_date="2026-09-10",
        timezone_name="America/New_York",
        nws_hourly=nws_hourly,
        nws_observations=observations,
        station_identity_verified=True,
        settlement_source_verified=True,
    )
    assert evidence.temporal_provenance_verified is False


def test_missing_target_date_hourly_forecast_fails_closed():
    nws_hourly = snapshot(
        "NWS", "PRIMARY_FORECAST", "nws-hourly", "2026-09-10T16:00:00Z",
        {"properties": {"periods": [{"startTime": "2026-09-11T12:00:00-04:00", "temperature": 90, "temperatureUnit": "F"}]}},
    )
    observations = snapshot("NWS", "OFFICIAL_OBSERVATION", "nws-obs", "2026-09-10T16:00:00Z", {"features": []})
    try:
        assemble_daily_high_evidence(
            analysis_time="2026-09-10T16:01:00Z",
            local_date="2026-09-10",
            timezone_name="America/New_York",
            nws_hourly=nws_hourly,
            nws_observations=observations,
            station_identity_verified=True,
            settlement_source_verified=True,
        )
    except WeatherEvidenceAssemblyError as exc:
        assert "NWS_HOURLY_TARGET_DATE_EMPTY" in str(exc)
    else:
        raise AssertionError("expected WeatherEvidenceAssemblyError")
