from kalshi_weather_v2.source_adapters import NoaaNceiAdapter, NwsAdapter, OpenMeteoAdapter, XweatherAdapter


class Recorder:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or {"properties": {"generatedAt": "2026-09-06T17:00:00Z", "periods": [{"startTime": "2026-09-06T18:00:00Z"}]}}

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        return self.payload


def test_nws_point_metadata_uses_weather_gov_and_user_agent():
    r = Recorder({"properties": {}})
    snap = NwsAdapter(r).point_metadata(40.78, -73.97, retrieved_at="t")
    assert snap.provider == "NWS"
    assert snap.role == "PRIMARY_FORECAST"
    assert r.calls[0][0].startswith("https://api.weather.gov/points/")
    assert "User-Agent" in r.calls[0][1]


def test_nws_hourly_preserves_issue_and_valid_times():
    r = Recorder()
    snap = NwsAdapter(r).hourly_forecast("https://api.weather.gov/gridpoints/OKX/1,1/forecast/hourly", retrieved_at="t")
    assert snap.issued_at == "2026-09-06T17:00:00Z"
    assert snap.valid_times == ("2026-09-06T18:00:00Z",)


def test_open_meteo_adapter_requests_explicit_models():
    r = Recorder({})
    OpenMeteoAdapter(r).multi_model_daily_highs(40.78, -73.97, "2026-09-06", ["gfs_seamless", "ecmwf_ifs025"], retrieved_at="t")
    url = r.calls[0][0]
    assert "models=gfs_seamless,ecmwf_ifs025" in url
    assert "temperature_unit=fahrenheit" in url


def test_ncei_token_is_optional_but_forwarded_when_present():
    r = Recorder({})
    NoaaNceiAdapter(r, token="abc").daily_station_history("GHCND", "GHCND:TEST", "2026-08-01", "2026-08-31", retrieved_at="t")
    assert r.calls[0][1] == {"token": "abc"}


def test_xweather_is_corroboration_only():
    r = Recorder({})
    snap = XweatherAdapter(r).conditions("newyork,ny", retrieved_at="t")
    assert snap.role == "CORROBORATION_ONLY"
