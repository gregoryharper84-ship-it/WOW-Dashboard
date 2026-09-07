"""Targeted V17 Weather governance regressions.

Synthetic fixtures only. These tests are not calibration evidence, historical
replay evidence, or production-certification evidence.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kalshi_engine.portfolio_governor import run as governor_run
from kalshi_engine.weather_gate import check as weather_check


def _candidate(package_overrides: dict | None = None, **overrides) -> dict:
    package = {
        "probability_status": "COMPLETED",
        "calibration_status": "CALIBRATED",
        "calibrated_lower_bound": 0.72,
        "final_high_pmf": {"72": 0.30, "73": 0.70},
        "can_execute": False,
    }
    if package_overrides:
        package.update(package_overrides)
    candidate = {
        "category": "weather",
        "ticker": "KXNHIGH-TEST",
        "city": "TEST",
        "scan_date": "2026-09-07",
        "confidence_tier": "WEATHER_MODEL_READY",
        "forecast_horizon_hours": 6.0,
        "sigma_f": 3.5,
        "settlement_station_verified": True,
        "nws_gridpoint_available": True,
        "bracket_coverage_complete": True,
        "brackets": [{"yes_price": 0.90}, {"yes_price": 0.10}],
        "probability_normalization_pass": True,
        "weather_v17_probability_package": package,
        "market_open": True,
        "orderbook_nonempty": True,
        "price_age_minutes": 3.0,
        "edge_lower_bound": 0.05,
        "net_edge_lower_bound": 0.05,
        "portfolio_check_passed": True,
        "portfolio_rejection_reason": None,
        "is_multi_leg": False,
        "event_id": "TEST-WEATHER-2026-09-07",
        "bracket_span_f": 3.0,
        "research_eligible": True,
        "calibration_strength": 0.80,
        "model_uncertainty": 0.10,
        "calibrated_prob_lower_bound": 0.99,
        "settlement_clarity_grade": "A",
        "spread_cents": 3.0,
        "exposure_overlap": False,
    }
    candidate.update(overrides)
    return candidate


def test_present_but_incomplete_package_fails_closed():
    result = weather_check(_candidate({"probability_status": "PENDING"}))
    assert result["passed"] is False
    assert result["failure_gate"] == 7
    assert result["failure_category"] == "V17_PROBABILITY_NOT_COMPLETED"


def test_present_but_uncalibrated_package_fails_closed():
    result = weather_check(_candidate({"calibration_status": "UNCALIBRATED"}))
    assert result["passed"] is False
    assert result["failure_gate"] == 7
    assert result["failure_category"] == "V17_CALIBRATION_REQUIRED"


def test_missing_calibrated_lower_bound_fails_closed():
    result = weather_check(_candidate({"calibrated_lower_bound": None}))
    assert result["passed"] is False
    assert result["failure_gate"] == 7
    assert result["failure_category"] == "V17_CALIBRATED_LOWER_BOUND_REQUIRED"


def test_weather_package_must_preserve_can_execute_false():
    result = weather_check(_candidate({"can_execute": True}))
    assert result["passed"] is False
    assert result["failure_gate"] == 7
    assert result["failure_category"] == "V17_CAN_EXECUTE_MUST_BE_FALSE"


def test_weather_ranking_ignores_legacy_top_level_probability_field():
    stronger_package = _candidate(
        {"calibrated_lower_bound": 0.80},
        ticker="WEATHER-PACKAGE-STRONGER",
        city="AAA",
        event_id="WEATHER-A",
        calibrated_prob_lower_bound=0.01,
    )
    weaker_package = _candidate(
        {"calibrated_lower_bound": 0.60},
        ticker="WEATHER-LEGACY-FIELD-STRONGER",
        city="BBB",
        event_id="WEATHER-B",
        calibrated_prob_lower_bound=0.99,
    )
    result = governor_run([weaker_package, stronger_package])
    assert result["final_pool"][0]["ticker"] == "WEATHER-PACKAGE-STRONGER"
    assert result["ranking_detail"][0]["calibrated_prob_lower_bound"] == 0.80


def test_kalshi_prices_do_not_change_weather_governed_probability_rank():
    a = _candidate(
        {"calibrated_lower_bound": 0.75},
        ticker="WEATHER-A",
        city="AAA",
        event_id="WEATHER-A",
        brackets=[{"yes_price": 0.99}, {"yes_price": 0.01}],
        calibrated_prob_lower_bound=0.01,
    )
    b = _candidate(
        {"calibrated_lower_bound": 0.70},
        ticker="WEATHER-B",
        city="BBB",
        event_id="WEATHER-B",
        brackets=[{"yes_price": 0.50}, {"yes_price": 0.50}],
        calibrated_prob_lower_bound=0.99,
    )
    result = governor_run([b, a])
    assert result["final_pool"][0]["ticker"] == "WEATHER-A"
