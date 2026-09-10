from __future__ import annotations

import pytest

from .contract_resolver import ContractResolutionError
from .kalshi_contract_acquisition import (
    KalshiContractAcquisitionError,
    KalshiWeatherContractSource,
    WeatherSeriesRulePolicy,
    resolve_acquired_weather_contract,
)


MARKET = {
    "market": {
        "ticker": "KXHIGHNY-TEST-T90",
        "event_ticker": "KXHIGHNY-TEST",
        "title": "NYC high temperature",
        "rules_primary": "YES settles if the official high is at least 90 F.",
        "rules_secondary": "NO otherwise.",
        "close_time": "2026-09-10T23:00:00Z",
        "floor_strike": 90,
        "cap_strike": None,
    }
}
EVENT = {
    "event": {
        "event_ticker": "KXHIGHNY-TEST",
        "series_ticker": "KXHIGHNY",
        "title": "Highest temperature in NYC today",
    }
}
SERIES = {
    "series": {
        "ticker": "KXHIGHNY",
        "settlement_sources": [
            {"name": "The Weather Company", "url": "https://example.test/settlement"}
        ],
    }
}


def getter(url, _headers=None):
    if "/markets/" in url:
        return MARKET
    if "/events/" in url:
        return EVENT
    if "/series/" in url:
        return SERIES
    raise AssertionError(url)


def policy(**overrides):
    values = dict(
        series_ticker="KXHIGHNY",
        lane="DAILY_HIGH_TEMPERATURE",
        location="New York City",
        metric="daily maximum temperature",
        units="F",
        observation_window="2026-09-10 local day",
        timezone="America/New_York",
        settlement_source_name="The Weather Company",
        settlement_location_type="STATION",
        rounding_convention="whole degree per contract",
        trace_measurement_rules="not applicable",
        settlement_station_id="KNYC",
        settlement_station_name="Central Park",
        threshold_lower=90.0,
        threshold_upper=None,
        lower_inclusive=True,
        upper_inclusive=True,
    )
    values.update(overrides)
    return WeatherSeriesRulePolicy(**values)


def test_live_rule_acquisition_walks_market_event_series_and_hashes_snapshot():
    source = KalshiWeatherContractSource(getter)
    acquired = source.acquire("KXHIGHNY-TEST-T90", retrieved_at="2026-09-10T20:00:00Z")
    assert acquired.event_ticker == "KXHIGHNY-TEST"
    assert acquired.series_ticker == "KXHIGHNY"
    assert acquired.rule_snapshot_id.startswith("kalshi-rule-")
    assert len(acquired.rule_snapshot_id) > 30


def test_live_rule_acquisition_rejects_identity_mismatch():
    def bad_getter(url, _headers=None):
        if "/markets/" in url:
            return {"market": {**MARKET["market"], "ticker": "WRONG"}}
        return getter(url, _headers)

    with pytest.raises(KalshiContractAcquisitionError, match="TICKER_MISMATCH"):
        KalshiWeatherContractSource(bad_getter).acquire("KXHIGHNY-TEST-T90", retrieved_at="2026-09-10T20:00:00Z")


def test_policy_must_match_live_settlement_source():
    acquired = KalshiWeatherContractSource(getter).acquire("KXHIGHNY-TEST-T90", retrieved_at="2026-09-10T20:00:00Z")
    with pytest.raises(ContractResolutionError) as exc:
        resolve_acquired_weather_contract(acquired, policy(settlement_source_name="NWS"))
    assert "SETTLEMENT_SOURCE_POLICY_NOT_IN_LIVE_SERIES" in exc.value.blockers


def test_policy_threshold_is_cross_checked_against_live_market_strike():
    acquired = KalshiWeatherContractSource(getter).acquire("KXHIGHNY-TEST-T90", retrieved_at="2026-09-10T20:00:00Z")
    with pytest.raises(ContractResolutionError) as exc:
        resolve_acquired_weather_contract(acquired, policy(threshold_lower=91.0))
    assert "THRESHOLD_LOWER_LIVE_MARKET_MISMATCH" in exc.value.blockers


def test_valid_live_metadata_plus_governed_policy_resolves_exact_contract():
    acquired = KalshiWeatherContractSource(getter).acquire("KXHIGHNY-TEST-T90", retrieved_at="2026-09-10T20:00:00Z")
    contract = resolve_acquired_weather_contract(acquired, policy())
    assert contract.ticker == "KXHIGHNY-TEST-T90"
    assert contract.settlement_source == "The Weather Company"
    assert contract.settlement_station_id == "KNYC"
    assert contract.threshold_lower == 90.0
    assert contract.rule_snapshot_id == acquired.rule_snapshot_id
