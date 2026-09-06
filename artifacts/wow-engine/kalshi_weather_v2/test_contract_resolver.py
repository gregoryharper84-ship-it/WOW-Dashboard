import pytest

from kalshi_weather_v2.contract_resolver import ContractResolutionError, resolve_weather_contract
from kalshi_weather_v2.agents import ContractSettlementAgent
from kalshi_weather_v2.models import WeatherEvidenceSnapshot


def raw_contract(**overrides):
    base = {
        "market_title": "NYC daily high",
        "contract_title": "Will NYC high be 90 to 91F?",
        "ticker": "KXHIGHNY-TEST",
        "lane": "DAILY_HIGH_TEMPERATURE",
        "yes_condition": "Official daily high is 90F or 91F",
        "no_condition": "Otherwise",
        "location": "New York City",
        "metric": "daily_high_temperature",
        "units": "F",
        "observation_window": "2026-09-06 local day",
        "timezone": "America/New_York",
        "settlement_source": "CONTRACT_NAMED_SOURCE",
        "rounding_convention": "nearest whole F",
        "trace_measurement_rules": "not_applicable",
        "market_close_time": "2026-09-07T00:00:00-04:00",
        "rule_snapshot_id": "rule-1",
        "threshold_lower": 90,
        "threshold_upper": 91,
        "settlement_station_id": "KNYC",
        "settlement_station_name": "Central Park",
    }
    base.update(overrides)
    return base


def verified_evidence():
    return WeatherEvidenceSnapshot(
        analysis_time="2026-09-06T18:00:00-04:00",
        latest_official_observation_time=None,
        forecast_issue_time=None,
        source_snapshot_ids=("rules-1",),
        evidence_complete=True,
        station_identity_verified=True,
        settlement_source_verified=True,
        temporal_provenance_verified=True,
    )


def test_station_contract_resolves_without_city_to_station_guessing():
    contract = resolve_weather_contract(raw_contract())
    assert contract.settlement_location_type == "STATION"
    assert contract.settlement_station_id == "KNYC"
    assert contract.threshold_lower == 90.0
    assert contract.threshold_upper == 91.0


def test_coordinate_contract_resolves_without_requiring_station():
    raw = raw_contract(
        settlement_station_id=None,
        settlement_station_name=None,
        settlement_location_type="COORDINATE",
        settlement_latitude=40.7829,
        settlement_longitude=-73.9654,
    )
    contract = resolve_weather_contract(raw)
    assert contract.settlement_location_type == "COORDINATE"
    assert contract.settlement_station_id is None
    result = ContractSettlementAgent().evaluate(contract, verified_evidence())
    assert result.ok is True
    assert result.payload["settlement_location_type"] == "COORDINATE"


def test_both_station_and_coordinate_require_explicit_identity_type():
    raw = raw_contract(settlement_latitude=40.78, settlement_longitude=-73.97)
    with pytest.raises(ContractResolutionError) as exc:
        resolve_weather_contract(raw)
    assert "SETTLEMENT_LOCATION_TYPE_REQUIRED_WHEN_MULTIPLE_IDENTITIES_PRESENT" in exc.value.blockers


def test_missing_settlement_source_fails_closed():
    with pytest.raises(ContractResolutionError) as exc:
        resolve_weather_contract(raw_contract(settlement_source=""))
    assert exc.value.code == "NO_PLAY_SETTLEMENT_AMBIGUITY"


def test_invalid_threshold_order_fails_closed():
    with pytest.raises(ContractResolutionError) as exc:
        resolve_weather_contract(raw_contract(threshold_lower=92, threshold_upper=90))
    assert "CONTRACT_THRESHOLD_ORDER_INVALID" in exc.value.blockers


def test_unsupported_weather_lane_does_not_get_generic_probability():
    with pytest.raises(ContractResolutionError) as exc:
        resolve_weather_contract(raw_contract(lane="DAILY_RAINFALL"))
    assert "WEATHER_LANE_UNSUPPORTED:DAILY_RAINFALL" in exc.value.blockers


def test_celsius_is_not_silently_converted_in_fahrenheit_native_v2_core():
    with pytest.raises(ContractResolutionError) as exc:
        resolve_weather_contract(raw_contract(units="C"))
    assert "WEATHER_UNITS_UNSUPPORTED:C" in exc.value.blockers


def test_no_settlement_location_does_not_substitute_nearby_station():
    with pytest.raises(ContractResolutionError) as exc:
        resolve_weather_contract(raw_contract(settlement_station_id=None, settlement_station_name=None))
    assert "SETTLEMENT_LOCATION_UNRESOLVED" in exc.value.blockers
