from decimal import Decimal

import pytest

from kalshi_weather_v2.hourly_index import KalshiWeatherIndexAdapter, WeatherIndexError


RETRIEVED_AT = "2026-09-10T16:30:00Z"


def test_hourly_index_preserves_real_minute_gaps_without_interpolation():
    payload = {
        "city": "nyc",
        "config_version": "nyc-temperature-v1.0-cal-20260907",
        "units": "fahrenheit",
        "timeseries": [
            {"t": 1789057800000, "v": "76.21", "status": "normal", "contributors": 5},
            # The next minute is deliberately absent: quorum gaps are evidence,
            # not values for this adapter to synthesize.
            {"t": 1789057920000, "v": "76.34", "status": "degraded", "contributors": 3},
        ],
    }
    snapshot = KalshiWeatherIndexAdapter(lambda _url, _headers: payload).snapshot(
        "nyc", retrieved_at=RETRIEVED_AT
    )

    assert [point.timestamp_ms for point in snapshot.points] == [1789057800000, 1789057920000]
    assert [point.value_f for point in snapshot.points] == [Decimal("76.21"), Decimal("76.34")]
    assert len(snapshot.points) == 2
    assert snapshot.can_execute is False


def test_incomplete_point_is_preserved_without_fabricated_value():
    payload = {
        "city": "nyc",
        "units": "fahrenheit",
        "timeseries": [{"t": 1789057800000, "status": "incomplete"}],
    }
    snapshot = KalshiWeatherIndexAdapter(lambda _url, _headers: payload).snapshot(
        "nyc", retrieved_at=RETRIEVED_AT
    )
    assert snapshot.points[0].value_f is None
    assert snapshot.incomplete_points == snapshot.points


def test_incomplete_point_with_value_fails_closed():
    payload = {
        "city": "nyc",
        "units": "fahrenheit",
        "timeseries": [
            {"t": 1789057800000, "v": "76.21", "status": "incomplete"},
        ],
    }
    with pytest.raises(WeatherIndexError) as exc:
        KalshiWeatherIndexAdapter(lambda _url, _headers: payload).snapshot(
            "nyc", retrieved_at=RETRIEVED_AT
        )
    assert "INCOMPLETE_POINT_HAS_CANONICAL_VALUE" in exc.value.blockers


def test_unknown_index_status_fails_closed_instead_of_becoming_normal():
    payload = {
        "city": "nyc",
        "units": "fahrenheit",
        "timeseries": [{"t": 1789057800000, "v": "76.21", "status": "mystery"}],
    }
    with pytest.raises(WeatherIndexError) as exc:
        KalshiWeatherIndexAdapter(lambda _url, _headers: payload).snapshot(
            "nyc", retrieved_at=RETRIEVED_AT
        )
    assert exc.value.code == "WEATHER_INDEX_STATUS_UNSUPPORTED"


def test_city_identity_and_fahrenheit_units_are_exact():
    with pytest.raises(WeatherIndexError) as city_exc:
        KalshiWeatherIndexAdapter(
            lambda _url, _headers: {"city": "chicago", "units": "fahrenheit", "timeseries": []}
        ).snapshot("nyc", retrieved_at=RETRIEVED_AT)
    assert city_exc.value.code == "WEATHER_INDEX_IDENTITY_MISMATCH"

    with pytest.raises(WeatherIndexError) as unit_exc:
        KalshiWeatherIndexAdapter(
            lambda _url, _headers: {"city": "nyc", "units": "celsius", "timeseries": []}
        ).snapshot("nyc", retrieved_at=RETRIEVED_AT)
    assert unit_exc.value.code == "WEATHER_INDEX_UNITS_UNSUPPORTED"


def test_non_monotonic_or_duplicate_minutes_fail_temporal_validation():
    payload = {
        "city": "nyc",
        "units": "fahrenheit",
        "timeseries": [
            {"t": 1789057800000, "v": "76.21", "status": "normal"},
            {"t": 1789057800000, "v": "76.22", "status": "normal"},
        ],
    }
    with pytest.raises(WeatherIndexError) as exc:
        KalshiWeatherIndexAdapter(lambda _url, _headers: payload).snapshot(
            "nyc", retrieved_at=RETRIEVED_AT
        )
    assert exc.value.code == "WEATHER_INDEX_TEMPORAL_INVALID"


def test_detailed_station_readings_preserve_source_and_qc_code():
    seen = []
    payload = {
        "city": "nyc",
        "units": "fahrenheit",
        "timeseries": [
            {
                "t": 1789057800000,
                "v": "76.21",
                "status": "normal",
                "contributors": 5,
                "stations": [
                    {
                        "station_id": "KLGA1M",
                        "code": "ok",
                        "source": "metar",
                        "temp_f": "76.10",
                    }
                ],
            }
        ],
    }

    def get_json(url, _headers):
        seen.append(url)
        return payload

    snapshot = KalshiWeatherIndexAdapter(get_json).snapshot(
        "nyc", retrieved_at=RETRIEVED_AT, detailed=True
    )
    reading = snapshot.points[0].station_readings[0]
    assert reading.station_id == "KLGA1M"
    assert reading.code == "ok"
    assert reading.source == "metar"
    assert reading.temp_f == Decimal("76.10")
    assert seen == [
        "https://external-api.kalshi.com/trade-api/v2/live_data/weather/nyc?detailed=true"
    ]


def test_receipt_basis_is_preserved_without_reclassifying_settlement_eligibility():
    basis = {"source": "backfill", "received_at": "2026-09-10T16:20:00Z"}
    payload = {
        "city": "nyc",
        "units": "fahrenheit",
        "timeseries": [
            {
                "t": 1789057800000,
                "v": "76.21",
                "status": "normal",
                "receipt_basis": basis,
            }
        ],
    }
    snapshot = KalshiWeatherIndexAdapter(lambda _url, _headers: payload).snapshot(
        "nyc", retrieved_at=RETRIEVED_AT
    )
    assert snapshot.points[0].receipt_basis == basis


def test_calibration_timeline_is_frozen_raw_pending_separate_semantic_parser():
    seen = []
    payload = {
        "city": "nyc",
        "calibrations": [
            {
                "config_version": "nyc-temperature-v1.0-cal-20260907",
                "effective_ts": 1788739200000,
                "stations": [{"station_id": "KLGA1M", "weight": "0.20", "offset_c": "0.10"}],
            }
        ],
    }

    def get_json(url, _headers):
        seen.append(url)
        return payload

    snapshot = KalshiWeatherIndexAdapter(get_json).calibrations(
        "nyc", retrieved_at=RETRIEVED_AT
    )
    assert snapshot.city == "nyc"
    assert snapshot.calibrations[0]["config_version"] == "nyc-temperature-v1.0-cal-20260907"
    assert snapshot.can_execute is False
    assert seen == [
        "https://external-api.kalshi.com/trade-api/v2/live_data/weather/nyc/calibrations"
    ]


def test_invalid_city_slug_is_rejected_before_request():
    called = False

    def get_json(_url, _headers):
        nonlocal called
        called = True
        return {}

    with pytest.raises(WeatherIndexError) as exc:
        KalshiWeatherIndexAdapter(get_json).snapshot("new york", retrieved_at=RETRIEVED_AT)
    assert exc.value.code == "WEATHER_INDEX_CITY_INVALID"
    assert called is False
