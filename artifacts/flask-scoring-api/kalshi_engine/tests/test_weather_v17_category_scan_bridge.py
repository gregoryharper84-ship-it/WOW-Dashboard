from __future__ import annotations

from copy import deepcopy

from kalshi_engine.weather_v17.legacy_bridge import category_scan_candidate_from_legacy_evaluate


CONTRACT = {"kind": "AT_LEAST", "threshold_f": 94, "side": "YES"}


def _legacy_candidate() -> dict:
    return {
        "city": "AUS",
        "station": "KAUS",
        "series": "KXHIGHAUS",
        "forecast_high": 95.0,
        "forecast_horizon_hours": 18,
        "forecast_timestamp": "2026-09-07T10:00:00Z",
        "forecast_source": "NWS",
        "weather_data_source_tier": "NWS_OFFICIAL_GRIDPOINT",
        # Legacy category-scan compatibility values. The V17 bridge must not
        # consume these as model probability or calibration evidence.
        "confidence_tier": "WEATHER_MODEL_READY",
        "calibrated_prob_lower_bound": 0.70,
        "model_probability": 0.70,
        # Market evidence is deliberately retained downstream but must not enter
        # the Weather probability model.
        "yes_price": 0.41,
        "no_price": 0.60,
        "market_edge": 0.12,
        "can_execute": True,
    }


def test_category_scan_bridge_erases_legacy_heuristic_without_calibration():
    candidate = category_scan_candidate_from_legacy_evaluate(
        _legacy_candidate(),
        CONTRACT,
        scored_at="2026-09-07T11:00:00Z",
    )

    package = candidate["weather_v17_probability_package"]
    assert package["legacy_heuristic_lower_bound_consumed"] is False
    assert candidate["legacy_heuristic_lower_bound_consumed"] is False
    assert candidate["calibrated_prob_lower_bound"] is None
    assert candidate["confidence_tier"] == "WEATHER_WATCH"
    assert candidate["can_execute"] is False
    assert candidate["DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"] is True
    assert candidate["yes_price"] == 0.41
    assert candidate["market_edge"] == 0.12


def test_category_scan_bridge_market_prices_do_not_change_model_probability():
    first = _legacy_candidate()
    second = deepcopy(first)
    second.update({"yes_price": 0.77, "no_price": 0.24, "market_edge": -0.08})

    a = category_scan_candidate_from_legacy_evaluate(
        first,
        CONTRACT,
        scored_at="2026-09-07T11:00:00Z",
    )
    b = category_scan_candidate_from_legacy_evaluate(
        second,
        CONTRACT,
        scored_at="2026-09-07T11:00:00Z",
    )

    pa = a["weather_v17_probability_package"]
    pb = b["weather_v17_probability_package"]
    assert pa["raw_probability"] == pb["raw_probability"]
    assert pa["final_high_pmf"] == pb["final_high_pmf"]
    assert pa["calibrated_lower_bound"] is None
    assert pb["calibrated_lower_bound"] is None
    assert a["yes_price"] != b["yes_price"]
    assert a["can_execute"] is False
    assert b["can_execute"] is False
