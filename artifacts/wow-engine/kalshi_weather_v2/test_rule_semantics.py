from dataclasses import replace

import pytest

from kalshi_weather_v2.contract_rule_acquisition import (
    ContractRuleAcquisitionError,
    FrozenContractRulePackage,
    SettlementSourceEvidence,
)
from kalshi_weather_v2.rule_semantics import parse_temperature_rule
from kalshi_weather_v2.rule_snapshot import freeze_market_rules


def _package(*, rule, strike_type, floor=None, cap=None, source="The Weather Company"):
    market = {
        "ticker": "KXHIGHNY-26SEP10-B91.5",
        "event_ticker": "KXHIGHNY-26SEP10",
        "title": "Will the maximum temperature be 91-92° on Sep 10, 2026?",
        "subtitle": "91° to 92°",
        "rules_primary": rule,
        "rules_secondary": "Preliminary data may differ from the final value.",
        "strike_type": strike_type,
        "floor_strike": floor,
        "cap_strike": cap,
        "close_time": "2026-09-11T05:00:00Z",
    }
    frozen = freeze_market_rules(market, acquired_at="2026-09-10T04:30:00Z")
    return FrozenContractRulePackage(
        package_id="pkg",
        acquired_at="2026-09-10T04:30:00Z",
        market_rules=frozen,
        event_ticker="KXHIGHNY-26SEP10",
        series_ticker="KXHIGHNY",
        settlement_source=SettlementSourceEvidence(source, "https://weather.com/kalshi", "SERIES_SETTLEMENT_SOURCES"),
        contract_url=None,
        contract_terms_url=None,
        series_last_updated_at=None,
        raw_event={},
        raw_series={},
        can_execute=False,
    )


def test_parse_current_daily_high_between_rule_preserves_clinyc_identity():
    package = _package(
        rule="If the maximum temperature recorded at New York City (CLINYC) for Sep 10, 2026, is between 91-92° fahrenheit according to The Weather Company, then the market resolves to Yes.",
        strike_type="between", floor=91, cap=92,
    )
    parsed = parse_temperature_rule(package)
    assert parsed.lane == "DAILY_HIGH_TEMPERATURE"
    assert parsed.metric == "daily_max_temperature"
    assert parsed.location == "New York City"
    assert parsed.settlement_location_code == "CLINYC"
    assert parsed.settlement_source == "The Weather Company"
    assert parsed.threshold_lower == 91
    assert parsed.threshold_upper == 92
    assert parsed.lower_inclusive is True and parsed.upper_inclusive is True
    assert parsed.can_execute is False


def test_parse_current_greater_rule_cross_checks_floor_strike():
    package = _package(
        rule="If the maximum temperature recorded at New York City (CLINYC) for Sep 10, 2026, is greater than 92° fahrenheit according to The Weather Company, then the market resolves to Yes.",
        strike_type="greater", floor=92,
    )
    parsed = parse_temperature_rule(package)
    assert parsed.threshold_lower == 92
    assert parsed.threshold_upper is None
    assert parsed.lower_inclusive is False


def test_parse_current_less_rule_cross_checks_cap_strike():
    package = _package(
        rule="If the maximum temperature recorded at New York City (CLINYC) for Sep 10, 2026, is less than 85° fahrenheit according to The Weather Company, then the market resolves to Yes.",
        strike_type="less", cap=85,
    )
    parsed = parse_temperature_rule(package)
    assert parsed.threshold_lower is None
    assert parsed.threshold_upper == 85
    assert parsed.upper_inclusive is False


def test_rule_and_structured_strike_disagreement_fails_closed():
    package = _package(
        rule="If the maximum temperature recorded at New York City (CLINYC) for Sep 10, 2026, is between 91-92° fahrenheit according to The Weather Company, then the market resolves to Yes.",
        strike_type="between", floor=89, cap=90,
    )
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_temperature_rule(package)
    assert "STRIKE_BOUNDS_RULE_MISMATCH" in exc.value.blockers


def test_rule_and_series_settlement_source_disagreement_fails_closed():
    package = _package(
        rule="If the maximum temperature recorded at New York City (CLINYC) for Sep 10, 2026, is between 91-92° fahrenheit according to The Weather Company, then the market resolves to Yes.",
        strike_type="between", floor=91, cap=92, source="National Weather Service",
    )
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_temperature_rule(package)
    assert "SETTLEMENT_SOURCE_RULE_SERIES_MISMATCH" in exc.value.blockers


def test_unrecognized_narrative_never_guesses_semantics():
    package = _package(
        rule="Highest temperature in New York resolves from the official source.",
        strike_type="between", floor=91, cap=92,
    )
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        parse_temperature_rule(package)
    assert exc.value.blockers == ("TEMPERATURE_RULE_SYNTAX_UNRECOGNIZED",)
