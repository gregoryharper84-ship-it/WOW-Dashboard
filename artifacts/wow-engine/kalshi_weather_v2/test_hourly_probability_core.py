from kalshi_weather_v2.models import ContractSnapshot, WeatherEvidenceSnapshot
from kalshi_weather_v2.probability_core import CalibrationProfile, WeatherProbabilityCore


def _contract(*, lane="HOURLY_TEMPERATURE", lower=81.99, upper=None, lower_inclusive=False, upper_inclusive=True):
    return ContractSnapshot(
        market_title="Temperature in New York City today at 1pm EDT?",
        contract_title="82° or above",
        ticker="KXTEMPNYCHS-26SEP1013-T81.99",
        lane=lane,
        yes_condition="index > 81.99°F",
        no_condition="index <= 81.99°F",
        location="New York City",
        metric="kalshi_weather_index_point_temperature",
        units="F",
        observation_window="POINT_IN_TIME:2026-09-10T17:00:00Z",
        timezone="EDT(UTC-04:00)",
        settlement_source="Synoptic Data",
        settlement_station_id=None,
        settlement_station_name=None,
        rounding_convention="USE_OFFICIAL_KALSHI_WEATHER_INDEX_AS_PUBLISHED;NO_ENGINE_ROUNDING",
        trace_measurement_rules="POINT_INDEX_VALUE;PRESERVE_INCOMPLETE_MINUTES;NO_STATION_SUBSTITUTION",
        market_close_time="2026-09-10T18:00:00Z",
        rule_snapshot_id="rule-hourly",
        threshold_lower=lower,
        threshold_upper=upper,
        lower_inclusive=lower_inclusive,
        upper_inclusive=upper_inclusive,
        settlement_location_type="SOURCE_LOCATION_CODE",
        settlement_location_code="KALSHI_WEATHER_INDEX:NYC",
    )


def _evidence(mu=82.0):
    return WeatherEvidenceSnapshot(
        analysis_time="2026-09-10T16:00:00Z",
        latest_official_observation_time=None,
        forecast_issue_time="2026-09-10T16:00:00Z",
        source_snapshot_ids=("forecast-1",),
        central_estimate=mu,
        evidence_complete=True,
        settlement_location_verified=True,
        settlement_source_verified=True,
        temporal_provenance_verified=True,
    )


def _calibration(lane="HOURLY_TEMPERATURE", sigma=1.0):
    return CalibrationProfile(
        station_id="KALSHI_WEATHER_INDEX:NYC",
        lane=lane,
        lead_time_bucket="H1",
        bias_f=0.0,
        sigma_f=sigma,
        lower_sigma_f=sigma,
        upper_sigma_f=sigma,
        sample_n=100,
        method="TEST_CERTIFIED_POINT_RESIDUALS",
        certified=True,
    )


def test_hourly_greater_uses_exact_8199_boundary_not_daily_half_degree_shift():
    package = WeatherProbabilityCore().build(
        contract=_contract(), evidence=_evidence(82.0), calibration=_calibration()
    )
    # P(X > 81.99) for N(82,1) is just over one half. Applying the daily
    # continuity correction would materially change this value and is forbidden.
    assert 0.503 < package.p_yes < 0.505
    assert package.probability_source == "KALSHI_WEATHER_V2_POINT_HORIZON_MODEL"
    assert package.calibrated is True


def test_daily_lane_retains_existing_strict_greater_continuity_correction():
    daily = _contract(lane="DAILY_HIGH_TEMPERATURE", lower=82, lower_inclusive=False)
    package = WeatherProbabilityCore().build(
        contract=daily,
        evidence=_evidence(82.0),
        calibration=_calibration(lane="DAILY_HIGH_TEMPERATURE"),
    )
    # Strict >82 on a whole-degree reported daily extreme uses the 82.5°F
    # continuity boundary, so N(82,1) produces ~0.3085.
    assert 0.308 < package.p_yes < 0.309


def test_hourly_does_not_condition_on_daily_observed_extreme_so_far():
    evidence = WeatherEvidenceSnapshot(
        **{**_evidence(82.0).__dict__, "observed_extreme_so_far": 90.0}
    )
    package = WeatherProbabilityCore().build(
        contract=_contract(), evidence=evidence, calibration=_calibration()
    )
    assert 0.503 < package.p_yes < 0.505


def test_hourly_between_uses_exact_decimal_edges():
    package = WeatherProbabilityCore().build(
        contract=_contract(lower=81.5, upper=82.5, lower_inclusive=True, upper_inclusive=True),
        evidence=_evidence(82.0),
        calibration=_calibration(),
    )
    assert 0.382 < package.p_yes < 0.384


def test_calibration_lane_mismatch_fails_closed():
    package = WeatherProbabilityCore().build(
        contract=_contract(),
        evidence=_evidence(),
        calibration=_calibration(lane="DAILY_HIGH_TEMPERATURE"),
    )
    assert package.p_yes is None
    assert package.probability_source == "CALIBRATION_LANE_MISMATCH"
    assert package.coherent is False
    assert package.calibrated is False
