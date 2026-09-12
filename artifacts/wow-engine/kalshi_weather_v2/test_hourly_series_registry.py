from __future__ import annotations

from kalshi_weather_v2.market_discovery import KalshiWeatherMarketDiscovery


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
                    "tags": ["Hourly temperature", "Miami"],
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


def test_known_registry_also_tracks_current_chicago_and_coastal_la_hourly_series():
    expected = {
        "Chicago Metro Area": "KXTEMPCHIHS",
        "Coastal Los Angeles": "KXTEMPLAXHS",
    }

    for location, ticker in expected.items():
        def get_json(url, _headers, *, _location=location, _ticker=ticker):
            assert url.endswith(f"/series/{_ticker}")
            return {
                "series": {
                    "ticker": _ticker,
                    "title": f"Hourly temperature in {_location}",
                    "category": "Climate and Weather",
                    "tags": ["Hourly temperature", _location],
                    "product_metadata": {},
                }
            }

        rows = KalshiWeatherMarketDiscovery(get_json).candidate_series(expected_location=location)
        assert [row.ticker for row in rows] == [ticker]
