from kalshi_weather_v2.market_discovery import KalshiWeatherMarketDiscovery


def test_current_climate_and_weather_category_is_discovered_without_category_query():
    calls = []

    def get_json(url, _headers):
        calls.append(url)
        # This fixture intentionally does not emulate the direct GET /series/{ticker}
        # response shape, so discovery must safely fall back to the broad public
        # Series list without adding a brittle category query parameter.
        return {
            "series": [
                {
                    "ticker": "KXHOURLYMIA",
                    "title": "Hourly temperature in Miami",
                    "category": "Climate and Weather",
                    "tags": ["Hourly temperature"],
                    "product_metadata": {},
                },
                {
                    "ticker": "SPORTTEMP-MIA",
                    "title": "Temperature in Miami stadium",
                    "category": "Sports",
                    "tags": ["temperature"],
                    "product_metadata": {},
                },
            ]
        }

    rows = KalshiWeatherMarketDiscovery(get_json).candidate_series(expected_location="Miami")
    assert [row.ticker for row in rows] == ["KXHOURLYMIA"]
    assert len(calls) == 2
    assert calls[0].endswith("/series/KXTEMPMIAH")
    assert "/series?" in calls[1]
    assert "category=" not in calls[1]
    assert "include_product_metadata=true" in calls[1]
