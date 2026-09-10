import pytest

from kalshi_weather_v2.source_adapters import OpenMeteoAdapter


def test_open_meteo_hourly_forecast_is_utc_fahrenheit_and_model_explicit():
    captured = {}

    def get_json(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return {
            "timezone": "GMT",
            "utc_offset_seconds": 0,
            "hourly_units": {"temperature_2m": "°F"},
            "hourly": {
                "time": ["2026-09-10T16:00", "2026-09-10T17:00"],
                "temperature_2m": [81.2, 82.1],
            },
        }

    snapshot = OpenMeteoAdapter(get_json).multi_model_hourly_temperatures(
        40.7829,
        -73.9654,
        "2026-09-10",
        "2026-09-10",
        ("gfs_seamless", "ecmwf_ifs025"),
        retrieved_at="2026-09-10T16:05:00Z",
    )

    assert snapshot.provider == "OPEN_METEO"
    assert snapshot.role == "SECONDARY_FORECAST"
    assert snapshot.valid_times == ("2026-09-10T16:00", "2026-09-10T17:00")
    assert "hourly=temperature_2m" in captured["url"]
    assert "models=gfs_seamless,ecmwf_ifs025" in captured["url"]
    assert "temperature_unit=fahrenheit" in captured["url"]
    assert "timezone=UTC" in captured["url"]
    assert captured["headers"] is None


def test_open_meteo_hourly_requires_explicit_model_set():
    adapter = OpenMeteoAdapter(lambda _url, _headers: {})
    with pytest.raises(ValueError, match="at least one Open-Meteo model is required"):
        adapter.multi_model_hourly_temperatures(
            40.7829,
            -73.9654,
            "2026-09-10",
            "2026-09-10",
            (),
            retrieved_at="2026-09-10T16:05:00Z",
        )
