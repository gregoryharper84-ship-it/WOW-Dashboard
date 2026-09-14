from __future__ import annotations

from kalshi_weather_v2.empirical_runtime import _automated_shadow_targets
from kalshi_weather_v2.market_discovery import KalshiWeatherMarketDiscovery
from kalshi_weather_v2.operational_cycle import VERIFIED_HOURLY_TARGETS


def test_miami_hourly_series_uses_explicit_registered_identity():
    calls: list[str] = []

    def get_json(url, _headers):
        calls.append(url)
        if url.endswith("/series/KXTEMPMIAH"):
            return {
                "series": {
                    "ticker": "KXTEMPMIAH",
                    "title": "Hourly Directional Miami Temperature",
                    "category": "Climate and Weather",
                    "tags": ["Hourly temperature"],
                    "product_metadata": {},
                }
            }
        raise AssertionError(f"unexpected fallback request: {url}")

    rows = KalshiWeatherMarketDiscovery(get_json).candidate_series(expected_location="Miami")

    assert [row.ticker for row in rows] == ["KXTEMPMIAH"]
    assert len(calls) == 1
    assert calls[0].endswith("/series/KXTEMPMIAH")


def test_registered_series_that_fails_identity_validation_falls_back_to_broad_list():
    calls: list[str] = []

    def get_json(url, _headers):
        calls.append(url)
        if url.endswith("/series/KXTEMPMIAH"):
            return {
                "series": {
                    "ticker": "KXTEMPMIAH",
                    "title": "Unrelated product",
                    "category": "Climate and Weather",
                    "tags": [],
                    "product_metadata": {},
                }
            }
        if "/series?" in url:
            return {
                "series": [
                    {
                        "ticker": "KXTEMPMIAH",
                        "title": "Hourly temperature in Miami",
                        "category": "Climate and Weather",
                        "tags": ["weather"],
                        "product_metadata": {},
                    }
                ]
            }
        raise AssertionError(url)

    rows = KalshiWeatherMarketDiscovery(get_json).candidate_series(expected_location="Miami")

    assert [row.ticker for row in rows] == ["KXTEMPMIAH"]
    assert any("/series?" in url for url in calls)


def test_registered_chicago_and_coastal_la_accept_current_series_labels():
    expected = {
        "Chicago Metro Area": ("KXTEMPCHIHS", "Hourly Directional Chicago Metro Temperature"),
        "Coastal Los Angeles": ("KXTEMPLAXHS", "Hourly Directional Coastal LA Temperature"),
    }

    for location, (ticker, title) in expected.items():
        def get_json(url, _headers, *, _ticker=ticker, _title=title):
            assert url.endswith(f"/series/{_ticker}")
            return {
                "series": {
                    "ticker": _ticker,
                    "title": _title,
                    "category": "Climate and Weather",
                    "tags": ["Hourly temperature"],
                    "product_metadata": {},
                }
            }

        rows = KalshiWeatherMarketDiscovery(get_json).candidate_series(expected_location=location)
        assert [row.ticker for row in rows] == [ticker]


def test_open_market_discovery_falls_back_to_nested_open_events():
    calls: list[str] = []

    def get_json(url, _headers):
        calls.append(url)
        if "/markets?" in url and "status=open" in url:
            return {"markets": [], "cursor": ""}
        if "/events?" in url:
            return {
                "events": [
                    {
                        "event_ticker": "KXTEMPMIAH-26SEP1312",
                        "markets": [
                            {
                                "ticker": "KXTEMPMIAH-26SEP1312-T89.99",
                                "status": "active",
                            }
                        ],
                    }
                ],
                "cursor": "",
            }
        raise AssertionError(url)

    discovery = KalshiWeatherMarketDiscovery(get_json)
    rows = discovery.open_markets(series_ticker="KXTEMPMIAH")

    assert [row["ticker"] for row in rows] == ["KXTEMPMIAH-26SEP1312-T89.99"]
    assert discovery.last_market_discovery_path == "EVENTS_STATUS_OPEN_NESTED_MARKETS"


def test_bounded_scheduler_mirrors_all_verified_operational_targets():
    bounded = _automated_shadow_targets()

    assert len(bounded) == len(VERIFIED_HOURLY_TARGETS) == 3
    assert [target.index_city for target in bounded] == [
        target.index_city for target in VERIFIED_HOURLY_TARGETS
    ]
    assert [target.expected_location for target in bounded] == [
        target.expected_location for target in VERIFIED_HOURLY_TARGETS
    ]
    assert all(target.enabled for target in bounded)
