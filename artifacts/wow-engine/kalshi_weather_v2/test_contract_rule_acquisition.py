import pytest

from kalshi_weather_v2.contract_rule_acquisition import (
    ContractRuleAcquisitionError,
    KalshiContractRuleAcquirer,
    resolve_settlement_source,
)


def _fixture_get_json(url, _headers):
    if url.endswith("/markets/KXHIGHNY-26SEP10-T81"):
        return {
            "market": {
                "ticker": "KXHIGHNY-26SEP10-T81",
                "event_ticker": "KXHIGHNY-26SEP10",
                "title": "Highest temperature in New York City today?",
                "subtitle": "80° to 81°",
                "rules_primary": "The market resolves using the National Weather Service final climate report.",
                "rules_secondary": "Central Park is the listed location.",
                "strike_type": "between",
                "floor_strike": 80,
                "cap_strike": 81,
                "close_time": "2026-09-11T05:00:00Z",
            }
        }
    if url.endswith("/events/KXHIGHNY-26SEP10"):
        return {"event": {"event_ticker": "KXHIGHNY-26SEP10", "series_ticker": "KXHIGHNY"}}
    if url.endswith("/series/KXHIGHNY"):
        return {
            "series": {
                "ticker": "KXHIGHNY",
                "settlement_sources": [
                    {"name": "National Weather Service", "url": "https://www.weather.gov/"}
                ],
            }
        }
    raise AssertionError(url)


def test_acquisition_freezes_market_event_series_and_exact_source():
    package = KalshiContractRuleAcquirer(_fixture_get_json).acquire(
        "KXHIGHNY-26SEP10-T81", acquired_at="2026-09-10T04:20:00Z"
    )
    assert package.market_rules.ticker == "KXHIGHNY-26SEP10-T81"
    assert package.event_ticker == "KXHIGHNY-26SEP10"
    assert package.series_ticker == "KXHIGHNY"
    assert package.settlement_source.name == "National Weather Service"
    assert package.settlement_source.source == "SERIES_SETTLEMENT_SOURCES"
    assert package.package_id.startswith("kalshi-contract-rules-")
    assert package.can_execute is False


def test_multiple_sources_require_exact_market_rule_disambiguation():
    sources = [
        {"name": "National Weather Service", "url": "https://weather.gov"},
        {"name": "The Weather Company", "url": "https://weather.com/kalshi"},
    ]
    source = resolve_settlement_source(
        sources,
        market_rule_text="Settlement is based on The Weather Company reading for the listed coordinates.",
    )
    assert source.name == "The Weather Company"
    assert source.source == "SERIES_PLUS_MARKET_RULE_DISAMBIGUATION"


def test_multiple_sources_without_disambiguation_fail_closed():
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        resolve_settlement_source(
            [
                {"name": "National Weather Service", "url": "https://weather.gov"},
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"},
            ],
            market_rule_text="See the official source for settlement.",
        )
    assert exc.value.code == "NO_PLAY_SETTLEMENT_AMBIGUITY"
    assert "MULTIPLE_SETTLEMENT_SOURCES_UNRESOLVED" in exc.value.blockers


def test_no_settlement_source_never_falls_back_to_market_title():
    with pytest.raises(ContractRuleAcquisitionError) as exc:
        resolve_settlement_source([], market_rule_text="Highest temperature in NYC")
    assert exc.value.blockers == ("SETTLEMENT_SOURCE_MISSING",)


def test_market_event_identity_mismatch_fails_closed():
    def get_json(url, headers):
        if "/markets/" in url:
            return _fixture_get_json(url, headers)
        if "/events/" in url:
            return {"event": {"event_ticker": "WRONG", "series_ticker": "KXHIGHNY"}}
        return _fixture_get_json(url, headers)

    with pytest.raises(ContractRuleAcquisitionError) as exc:
        KalshiContractRuleAcquirer(get_json).acquire(
            "KXHIGHNY-26SEP10-T81", acquired_at="2026-09-10T04:20:00Z"
        )
    assert "EVENT_IDENTITY_MISMATCH" in exc.value.blockers
