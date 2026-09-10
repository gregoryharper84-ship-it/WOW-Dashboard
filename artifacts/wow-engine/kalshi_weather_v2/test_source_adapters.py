from __future__ import annotations

from kalshi_weather_v2.source_adapters import NoaaNceiAdapter, NwsAdapter, OpenMeteoAdapter, XweatherAdapter


def test_nws_adapter_uses_official_weather_gov_hosts():
    seen = []
    def get_json(url, headers):
        seen.append((url, headers))
        return {"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/X/1,1/forecast/hourly"}}
    snap = NwsAdapter(get_json).point_metadata(40.0, -75.0, retrieved_at="2026-09-02T00:00:00Z")
    assert snap.provider == "NWS"
    assert seen[0][0].startswith("https://api.weather.gov/points/")


def test_open_meteo_adapter_is_secondary_and_keyless():
    seen = []
    def get_json(url, headers):
        seen.append((url, headers))
        return {"daily": {"time": ["2026-09-02"]}}
    snap = OpenMeteoAdapter(get_json).multi_model_daily_highs(
        40.0, -75.0, "2026-09-02", ["gfs_seamless", "ecmwf_ifs025"], retrieved_at="2026-09-02T00:00:00Z"
    )
    assert snap.role == "SECONDARY_FORECAST"
    assert "models=gfs_seamless,ecmwf_ifs025" in seen[0][0]
    assert "timezone=UTC" in seen[0][0]
    assert seen[0][1] is None


def test_open_meteo_daily_high_can_use_exact_contract_timezone():
    seen = []
    def get_json(url, headers):
        seen.append((url, headers))
        return {"daily": {"time": ["2026-09-02"]}}
    OpenMeteoAdapter(get_json).multi_model_daily_highs(
        40.0, -75.0, "2026-09-02", ["gfs_seamless"],
        retrieved_at="2026-09-02T00:00:00Z", timezone_name="America/New_York"
    )
    assert "timezone=America/New_York" in seen[0][0]


def test_noaa_adapter_accepts_optional_free_token():
    seen = []
    def get_json(url, headers):
        seen.append((url, headers))
        return {"results": []}
    NoaaNceiAdapter(get_json, token="free-token").daily_station_history(
        "GHCND", "GHCND:TEST", "2026-01-01", "2026-01-02", retrieved_at="2026-09-02T00:00:00Z"
    )
    assert seen[0][1] == {"token": "free-token"}


def test_xweather_is_explicitly_corroboration_only():
    def get_json(url, headers):
        return {"response": []}
    snap = XweatherAdapter(get_json).conditions("knyc", retrieved_at="2026-09-02T00:00:00Z")
    assert snap.role == "CORROBORATION_ONLY"
