from kalshi_weather_v2.calibration_fit import ForecastResidual, fit_candidate_calibration_profile
from kalshi_weather_v2.observation_reconstruction import reconstruct_extreme, reconstruct_temperature_series


def test_observation_series_reconstructs_max_without_daily_extreme_field():
    features = [
        {"id": "a", "properties": {"timestamp": "2026-09-06T14:00:00Z", "temperature": {"value": 30.0, "unitCode": "wmoUnit:degC"}}},
        {"id": "b", "properties": {"timestamp": "2026-09-06T15:00:00Z", "temperature": {"value": 88.0, "unitCode": "wmoUnit:degF"}}},
        {"id": "c", "properties": {"timestamp": "2026-09-06T16:00:00Z", "temperature": {"value": None, "unitCode": "wmoUnit:degC"}}},
    ]
    points = reconstruct_temperature_series(features)
    result = reconstruct_extreme(points, "MAX")
    assert result.complete is True
    assert result.points_used == 2
    assert round(result.value_f, 1) == 88.0
    assert result.observation_time == "2026-09-06T15:00:00Z"


def test_observation_series_does_not_interpolate_empty_data():
    result = reconstruct_extreme((), "MAX")
    assert result.complete is False
    assert result.value_f is None
    assert result.blockers == ("OFFICIAL_OBSERVATION_SERIES_EMPTY",)


def _rows(n=40):
    rows = []
    for i in range(n):
        settled = 80.0 + (i % 7)
        forecast = settled + ((i % 5) - 2) * 0.4 + 0.3
        rows.append(ForecastResidual(
            forecast_value_f=forecast,
            settled_value_f=settled,
            forecast_as_of=f"2026-07-{(i % 28) + 1:02d}T12:00:00Z",
            settlement_time=f"2026-08-{(i % 28) + 1:02d}T23:59:00Z",
        ))
    return rows


def test_candidate_calibrator_never_self_certifies_or_publishes():
    result = fit_candidate_calibration_profile(
        _rows(), station_id="KNYC", lane="DAILY_HIGH_TEMPERATURE", lead_time_bucket="DAY_AHEAD"
    )
    assert result.status == "CALIBRATION_CANDIDATE_FIT_RESEARCH_ONLY"
    assert result.profile is not None
    assert result.profile.certified is False
    assert result.can_publish is False
    assert result.can_execute is False
    assert result.profile.sample_n == 40
    assert result.profile.sigma_f > 0


def test_candidate_calibrator_fails_closed_on_small_sample():
    result = fit_candidate_calibration_profile(
        _rows(10), station_id="KNYC", lane="DAILY_HIGH_TEMPERATURE", lead_time_bucket="DAY_AHEAD"
    )
    assert result.profile is None
    assert "CALIBRATION_SAMPLE_INSUFFICIENT" in result.blockers


def test_candidate_calibrator_rejects_lookahead():
    rows = _rows(40)
    rows[0] = ForecastResidual(85.0, 84.0, "2026-09-07T00:00:00Z", "2026-09-06T23:59:00Z")
    result = fit_candidate_calibration_profile(
        rows, station_id="KNYC", lane="DAILY_HIGH_TEMPERATURE", lead_time_bucket="DAY_AHEAD"
    )
    assert result.profile is None
    assert "CALIBRATION_LOOKAHEAD_LEAKAGE" in result.blockers
