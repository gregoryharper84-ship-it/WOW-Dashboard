from __future__ import annotations

import pytest

from kalshi_weather_v2.contract_rule_acquisition import (
    ContractRuleAcquisitionError,
    FrozenContractRulePackage,
    SettlementSourceEvidence,
    resolve_settlement_source,
)
from kalshi_weather_v2.hourly_rule_semantics import parse_hourly_temperature_rule
from kalshi_weather_v2.rule_snapshot import FrozenRuleSnapshot


def _package(*, occurrence: str = "2026-09-13T16:05:00Z") -> FrozenContractRulePackage:
    rule = (
        "If the temperature recorded at Miami, FL for Sep 13, 2026 at 12 PM EDT "
        "as reported by Synoptic Data, is above 89.99°, then the market resolves to Yes."
    )
    market = FrozenRuleSnapshot(
        rule_snapshot_id="rule-live-miami",
        ticker="KXTEMPMIAH-26SEP1312-T89.99",
        acquired_at="2026-09-13T15:30:00Z",
        title="Will the temp in Miami be above 89.99° on Sep 13, 2026 at 12pm EDT?",
        subtitle="90° or above",
        rules_primary=rule,
        rules_secondary=(
            "The official final value for this market is determined strictly by the temperature "
            "reported by Synoptic Data, calculated in accordance with the Kalshi Weather Index Methodology."
        ),
        strike_type="greater",
        floor_strike=89.99,
        cap_strike=None,
        functional_strike=None,
        close_time="2026-09-13T16:00:00Z",
        expiration_time="2026-09-20T16:00:00Z",
        raw_market={
            "ticker": "KXTEMPMIAH-26SEP1312-T89.99",
            "title": "Will the temp in Miami be above 89.99° on Sep 13, 2026 at 12pm EDT?",
            "subtitle": "90° or above",
            "event_ticker": "KXTEMPMIAH-26SEP1312",
            "occurrence_datetime": occurrence,
            "strike_type": "greater",
            "floor_strike": 89.99,
        },
    )
    return FrozenContractRulePackage(
        package_id="package-live-miami",
        acquired_at="2026-09-13T15:30:00Z",
        market_rules=market,
        event_ticker="KXTEMPMIAH-26SEP1312",
        series_ticker="KXTEMPMIAH",
        settlement_source=SettlementSourceEvidence(
            name="Synoptic Data",
            url="https://synopticdata.com/",
            source="SERIES_SETTLEMENT_SOURCES",
        ),
        contract_url=None,
        contract_terms_url=None,
        series_last_updated_at=None,
        raw_event={
            "event_ticker": "KXTEMPMIAH-26SEP1312",
            "series_ticker": "KXTEMPMIAH",
            "title": "Temperature in Miami on Sep 13, 2026 at 12pm EDT",
            "occurrence_datetime": occurrence,
        },
        raw_series={"ticker": "KXTEMPMIAH"},
    )


def test_live_rule_clock_controls_over_five_minute_occurrence_lag():
    parsed = parse_hourly_temperature_rule(
        _package(),
        index_city="miami",
        expected_location="Miami",
    )
    assert parsed.observation_time_utc == "2026-09-13T16:00:00Z"
    assert parsed.market_close_time == "2026-09-13T16:00:00Z"
    assert parsed.threshold_lower == 89.99
    assert parsed.yes_condition == "index > 89.99°F"


def test_occurrence_too_far_from_rule_clock_still_fails_closed():
    with pytest.raises(ContractRuleAcquisitionError) as exc_info:
        parse_hourly_temperature_rule(
            _package(occurrence="2026-09-13T16:30:00Z"),
            index_city="miami",
            expected_location="Miami",
        )
    assert "HOURLY_OCCURRENCE_RULE_TIME_MISMATCH" in exc_info.value.blockers


def test_generic_kalshi_series_source_can_be_refined_by_exact_market_rule():
    source = resolve_settlement_source(
        [{"name": "Kalshi", "url": "https://kalshi.com"}],
        market_rule_text=(
            "If the temperature recorded at Chicago Metro Area for Sep 13, 2026 at 12 PM EDT "
            "as reported by Synoptic Data, is above 71.99°, then the market resolves to Yes."
        ),
    )
    assert source.name == "Synoptic Data"
    assert source.url is None
    assert source.source == "MARKET_RULE_EXPLICIT_SOURCE_OVER_GENERIC_SERIES_METADATA"


def test_specific_series_source_conflict_does_not_get_overridden():
    with pytest.raises(ContractRuleAcquisitionError) as exc_info:
        resolve_settlement_source(
            [{"name": "Specific Other Source", "url": "https://example.com"}],
            market_rule_text=(
                "If the temperature recorded at Miami, FL for Sep 13, 2026 at 12 PM EDT "
                "as reported by Synoptic Data, is above 89.99°, then the market resolves to Yes."
            ),
        )
    assert "SERIES_MARKET_SETTLEMENT_SOURCE_CONFLICT" in exc_info.value.blockers
