from dataclasses import replace

import pytest

from kalshi_weather_v2.contract_rule_acquisition import (
    ContractRuleAcquisitionError,
    FrozenContractRulePackage,
    SettlementSourceEvidence,
)
from kalshi_weather_v2.hourly_rule_semantics import parse_hourly_temperature_rule
from kalshi_weather_v2.rule_snapshot import freeze_market_rules


def _package(
    *,
    rule="If the temperature recorded in New York City for Sep 10, 2026 1:00pm EDT as reported by Synoptic Data is above 81.99° fahrenheit, then the market resolves to Yes.",
    strike_type="greater",
    floor=81.99,
    cap=None,
    occurrence="2026-09-10T17:00:00Z",
    source="Synoptic Data",
    title="Temperature in New York City today at 1pm EDT?",
):
    market = {
        "ticker": "KXTEMPNYCHS-26SEP1013-T81.99",
        "event_ticker": "KXTEMPNYCHS-26SEP1013",
        "title": title,
        "subtitle": "82° or above",
        "rules_primary": rule,
        "rules_secondary": "The official settlement source is Synoptic Data.",
        "strike_type": strike_type,
        "floor_strike": floor,
        "cap_strike": cap,
        "close_time": "2026-09-10T18:00:00Z",
        "occurrence_datetime": occurrence,
    }
    frozen = freeze_market_rules(market, acquired_at="2026-09-10T16:00:00Z")
    return FrozenContractRulePackage(
        package_id="pkg-hourly",
        acquired_at="2026-09-10T16:00:00Z",
        market_rules=frozen,
        event_ticker="KXTEMPNYCHS-26SEP1013",
        series_ticker="KXTEMPNYCHS",
        settlement_source=SettlementSourceEvidence(source, "https://synopticdata.com/", "SERIES_SETTLEMENT_SOURCES"),
        contract_url=None,
        contract_terms_url=None,
        series_last_updated_at=None,
        raw_event={
            "event_ticker": "KXTEMPNYCHS-26SEP1013",
            "series_ticker": "KXTEMPNYCHS",
            "title": title,
            "occurrence_datetime": occurrence,
        },
        raw_series={"ticker": "KXTEMPNYCHS"},
        can_execute=False,
    )


def test_hourly_greater_rule_resolves_exact_point_contract():
    parsed = parse_hourly_temperature_rule(
        _package(), index_city="nyc", expected_location="New York City"
    )
    assert parsed.lane == "HOURLY_TEMPERATURE"
    assert parsed.metric == "kalshi_weather_index_point_temperature"
    assert parsed.settlement_source == "Synoptic Data"
    assert parsed.settlement_location_code == "KALSHI_WEATHER_INDEX:nyc"
    assert parsed.observation_time_utc == "2026-09-10T17:00:00Z"
    assert parsed.observation_timestamp_ms == 1789059600000
    assert parsed.timezone == "EDT(UTC-04:00)"
    assert parsed.threshold_lower == 81.99
    assert parsed.threshold_upper is None
    assert parsed.lower_inclusive is False
    assert parsed.can_execute is False

    contract = parsed.to_contract_snapshot(_package())
    assert contract.lane == "HOURLY_TEMPERATURE"
    assert contract.observation_window == "POINT_IN_TIME:2026-09-10T17:00:00Z"
    assert contract.settlement_location_type == "SOURCE_LOCATION_CODE"
    assert contract.settlement_location_code == "KALSHI_WEATHER_INDEX:NYC"
    assert "NO_ENGINE_ROUNDING" in contract.rounding_convention


def test_hourly_rule_source_must_match_series_source():
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_hourly_temperature_rule(
            _package(source="The Weather Company"),
            index_city="nyc",
            expected_location="New York City",
        )
    assert "HOURLY_SETTLEMENT_SOURCE_NOT_NAMED_IN_RULE" in exc.value.blockers


def test_hourly_occurrence_must_match_explicit_edt_clock():
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_hourly_temperature_rule(
            _package(occurrence="2026-09-10T18:00:00Z"),
            index_city="nyc",
            expected_location="New York City",
        )
    assert "HOURLY_OCCURRENCE_TIMEZONE_MISMATCH" in exc.value.blockers


def test_hourly_rule_and_structured_strike_must_match():
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_hourly_temperature_rule(
            _package(floor=82.99),
            index_city="nyc",
            expected_location="New York City",
        )
    assert "HOURLY_RULE_STRIKE_VALUE_MISMATCH" in exc.value.blockers


def test_hourly_location_is_not_inferred_from_unrelated_text():
    package = _package(
        title="Temperature today at 1pm EDT?",
        rule="If the temperature for Sep 10, 2026 1:00pm EDT as reported by Synoptic Data is above 81.99° fahrenheit, then the market resolves to Yes.",
    )
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_hourly_temperature_rule(package, index_city="nyc")
    assert "HOURLY_LOCATION_UNRESOLVED" in exc.value.blockers


def test_hourly_index_city_is_explicit_and_never_station_guessed():
    parsed = parse_hourly_temperature_rule(
        _package(), index_city="nyc", expected_location="New York City"
    )
    assert parsed.settlement_location_code == "KALSHI_WEATHER_INDEX:nyc"
    assert "KNYC" not in parsed.settlement_location_code


def test_hourly_between_is_supported_only_when_both_rule_values_match():
    parsed = parse_hourly_temperature_rule(
        _package(
            rule="If the temperature recorded in New York City for Sep 10, 2026 1:00pm EDT as reported by Synoptic Data is between 81.25 and 82.75° fahrenheit, then the market resolves to Yes.",
            strike_type="between",
            floor=81.25,
            cap=82.75,
        ),
        index_city="nyc",
        expected_location="New York City",
    )
    assert parsed.threshold_lower == 81.25
    assert parsed.threshold_upper == 82.75
    assert parsed.lower_inclusive is True
    assert parsed.upper_inclusive is True


def test_hourly_unimplemented_exact_strike_fails_closed():
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_hourly_temperature_rule(
            _package(
                rule="If the temperature recorded in New York City for Sep 10, 2026 1:00pm EDT as reported by Synoptic Data is exactly 82° fahrenheit, then the market resolves to Yes.",
                strike_type="exactly",
                floor=82,
            ),
            index_city="nyc",
            expected_location="New York City",
        )
    assert "HOURLY_STRIKE_TYPE_UNSUPPORTED:exactly" in exc.value.blockers


def test_hourly_timezone_token_is_required_not_geographically_inferred():
    package = _package(
        rule="If the temperature recorded in New York City for Sep 10, 2026 1:00pm as reported by Synoptic Data is above 81.99° fahrenheit, then the market resolves to Yes.",
        title="Temperature in New York City today at 1pm?",
    )
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_hourly_temperature_rule(package, index_city="nyc", expected_location="New York City")
    assert "HOURLY_TIMEZONE_TOKEN_MISSING" in exc.value.blockers
