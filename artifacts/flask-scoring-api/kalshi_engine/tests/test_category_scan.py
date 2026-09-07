"""
test_category_scan.py — Category-Router / Singles-Governor unit tests
WOW v16.5 compatibility coverage under V17 weather governance.

Runs without external network access, DB, or app.py import.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from kalshi_engine.category_router import classify_market
from kalshi_engine.weather_gate import check as weather_check
from kalshi_engine.sports_gate import check as sports_check
from kalshi_engine.portfolio_governor import check_single, run as governor_run


def _weather_candidate(overrides: dict | None = None) -> dict:
    """Build a fully-passing synthetic weather candidate under the V17 contract.

    The probability package below is test fixture data only. It is not replay,
    calibration evidence, or production-certification evidence.
    """
    base = {
        "category": "weather",
        "ticker": "KXNHIGH-NYC-25-2026-07-22",
        "city": "NYC",
        "scan_date": "2026-07-22",
        "confidence_tier": "WEATHER_MODEL_READY",
        "forecast_horizon_hours": 6.0,
        "sigma_f": 3.5,
        "settlement_station_verified": True,
        "nws_gridpoint_available": True,
        "bracket_coverage_complete": True,
        # Retained only as legacy market-coherence evidence. It does not
        # satisfy or control V17 probability normalization.
        "probability_normalization_pass": True,
        "brackets": [{"yes_price": 0.55}, {"yes_price": 0.45}],
        # Synthetic V17 probability fixture. The PMF, calibration status, and
        # lower bound are isolated from the Kalshi price fields above.
        "weather_v17_probability_package": {
            "probability_status": "COMPLETED",
            "calibration_status": "CALIBRATED",
            "calibrated_lower_bound": 0.72,
            "final_high_pmf": {"72": 0.30, "73": 0.70},
            "can_execute": False,
        },
        "market_open": True,
        "orderbook_nonempty": True,
        "price_age_minutes": 3.0,
        "edge_lower_bound": 0.052,
        "portfolio_check_passed": True,
        "portfolio_rejection_reason": None,
        "is_multi_leg": False,
        "event_id": "NYC-NHIGH-2026-07-22",
        "bracket_span_f": 3.0,
        "research_eligible": True,
        "net_edge_lower_bound": 0.052,
        "calibration_strength": 0.82,
        "model_uncertainty": 0.08,
        "calibrated_prob_lower_bound": 0.72,
        "settlement_clarity_grade": "A",
        "spread_cents": 3.0,
        "exposure_overlap": False,
    }
    if overrides:
        base.update(overrides)
    return base


def _sports_candidate(overrides: dict | None = None) -> dict:
    """Build a fully-passing sports candidate."""
    base = {
        "category": "sports_winner",
        "ticker": "KXMLBGAME-NYY-BOS-2026-07-22",
        "event_ticker": "KXMLBGAME-NYY-BOS-2026-07-22",
        "market_title": "New York Yankees vs Boston Red Sox",
        "settlement_condition": "The team with more runs wins the game.",
        "market_type": "full_game_outright_winner",
        "trading_active": True,
        "kalshi_orderbook_source": "direct_api",
        "price_age_minutes": 4.5,
        "calibrated_prob_lower_bound": 0.71,
        "lineup_status": "CONFIRMED",
        "consensus_odds": {
            "status": "AVAILABLE",
            "single_book_fallback": False,
            "consensus_fair_probability": 0.69,
        },
        "market_prior_weight": 0.40,
        "net_edge_lower_bound": 0.031,
        "settlement_grade_result": {
            "settlement_risk": "LOW",
            "resolution_clarity_grade": "A",
        },
        "portfolio_check_passed": True,
        "portfolio_rejection_reason": None,
        "is_multi_leg": False,
        "event_id": "MLB-NYY-BOS-2026-07-22",
        "city": None,
        "scan_date": "2026-07-22",
        "research_eligible": True,
        "calibration_strength": 0.78,
        "model_uncertainty": 0.10,
        "settlement_clarity_grade": "A",
        "spread_cents": 4.0,
        "exposure_overlap": False,
    }
    if overrides:
        base.update(overrides)
    return base


def test_weather_routes_before_sports_equal_quality():
    weather = _weather_candidate({"net_edge_lower_bound": 0.05, "calibration_strength": 0.85})
    sports = _sports_candidate({"net_edge_lower_bound": 0.05, "calibration_strength": 0.80})
    result = governor_run([weather, sports])
    pool = result["final_pool"]
    assert len(pool) >= 1
    assert pool[0]["category"] == "weather"


def test_weather_watch_never_reaches_final_pool():
    result = weather_check(_weather_candidate({"confidence_tier": "WEATHER_WATCH"}))
    assert result["passed"] is False
    assert result["failure_gate"] == 1
    assert result["failure_category"] == "WEATHER_WATCH_NOT_ELIGIBLE"


def test_stale_weather_orderbook_data_unobtainable():
    result = weather_check(_weather_candidate({"price_age_minutes": 15.0}))
    assert result["passed"] is False
    assert result["failure_gate"] == 10
    assert result["failure_category"] == "KALSHI_DATA_UNOBTAINABLE"


def test_sports_empty_inventory_stops_before_modeling():
    result = sports_check(_sports_candidate(), inventory_signal="INVENTORY_EMPTY")
    assert result["passed"] is False
    assert result["failure_gate"] == 1
    assert result["failure_category"] == "INVENTORY_NOT_READY"


def test_upset_cannot_occupy_final_slot():
    result = sports_check(_sports_candidate({"calibrated_prob_lower_bound": 0.52}), inventory_signal="INVENTORY_READY")
    assert result["passed"] is False
    assert result["failure_gate"] == 5
    assert result["failure_category"] == "UPSET_REJECTED"


def test_high_prob_favorite_negative_edge_rejected():
    result = sports_check(
        _sports_candidate({"calibrated_prob_lower_bound": 0.78, "net_edge_lower_bound": -0.012}),
        inventory_signal="INVENTORY_READY",
    )
    assert result["passed"] is False
    assert result["failure_gate"] == 9
    assert result["failure_category"] == "EDGE_BELOW_FLOOR"


def test_combo_market_rejected_as_combo():
    result = classify_market({
        "mve_collection_ticker": "KXMLBSLATE-2026-07-22",
        "category": "sports",
        "market_type": "full_game_outright_winner",
        "ticker": "KXMLBSLATE-2026-07-22-A",
    })
    assert result["eligible"] is False
    assert result["rejection_code"] == "KALSHI_REJECT_COMBO_DISABLED"
    assert result["category"] == "combo"


def test_multi_underlying_count_rejected():
    result = classify_market({
        "underlying_count": 2,
        "category": "sports",
        "market_type": "full_game_outright_winner",
        "ticker": "KXMLBGAME-MULTI",
    })
    assert result["eligible"] is False
    assert result["rejection_code"] == "KALSHI_REJECT_COMBO_DISABLED"


def test_two_weather_same_city_date_portfolio_rejected():
    first = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22-A"})
    second = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22-B"})
    result = governor_run([first, second])
    assert len(result["final_pool"]) == 1
    rejected_reasons = [r.get("portfolio_rejection_reason") for r in result["rejected"]]
    assert any("CITY_DATE" in (r or "") or "CITY" in (r or "") for r in rejected_reasons)


def test_caller_supplied_price_cannot_pass_sports_gate():
    result = sports_check(_sports_candidate({"kalshi_orderbook_source": "caller_supplied"}), inventory_signal="INVENTORY_READY")
    assert result["passed"] is False
    assert result["failure_gate"] == 8
    assert "NOT_DIRECT_API" in result["failure_category"]


def test_screenshot_price_cannot_pass_sports_gate():
    result = sports_check(_sports_candidate({"kalshi_orderbook_source": "screenshot"}), inventory_signal="INVENTORY_READY")
    assert result["passed"] is False
    assert result["failure_gate"] == 8


def test_economics_never_gets_probability():
    for eco_cat in ("economics", "macro_economics", "scheduled_economics"):
        result = classify_market({"category": eco_cat, "ticker": "KXECON-TEST", "market_type": "binary"})
        assert result["eligible"] is False
        assert result["rejection_code"] == "RESEARCH_LANE_NOT_BUILT"
        assert result["lane"] == "RESEARCH_LANE_NOT_BUILT"


def test_disabled_categories_never_fall_through():
    disabled = [
        {"category": "politics", "market_type": "binary", "ticker": "KXPOL"},
        {"category": "entertainment", "market_type": "binary", "ticker": "KXENT"},
        {"category": "mentions", "market_type": "binary", "ticker": "KXMNT"},
        {"category": "breaking_news", "market_type": "binary", "ticker": "KXBRK"},
        {"category": "celebrity", "market_type": "binary", "ticker": "KXCEL"},
    ]
    for market in disabled:
        result = classify_market(market)
        assert result["eligible"] is False
        assert result["rejection_code"] == "CATEGORY_DISABLED_OR_UNSUPPORTED"


def test_sports_derivative_not_eligible():
    result = classify_market({
        "category": "baseball",
        "market_type": "first_5_innings_winner",
        "ticker": "KXMLBF5-TEST",
    })
    assert result["eligible"] is False
    assert result["rejection_code"] == "CATEGORY_DISABLED_OR_UNSUPPORTED"


def test_final_pool_can_be_zero():
    result = governor_run([])
    assert result["final_pool"] == []
    assert result["survivors"] == []
    assert result["rejected"] == []


def test_final_pool_can_be_one():
    good = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22"})
    dup = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22-B", "net_edge_lower_bound": 0.07})
    result = governor_run([good, dup])
    assert len(result["final_pool"]) == 1


def test_every_candidate_reaches_portfolio_governor():
    candidates = [
        _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22"}),
        _sports_candidate({"event_id": "MLB-NYY-BOS-2026-07-22", "ticker": "KXMLBGAME-NYY-BOS"}),
        _sports_candidate({"event_id": "MLB-LAD-SF-2026-07-22", "ticker": "KXMLBGAME-LAD-SF", "net_edge_lower_bound": 0.028}),
    ]
    result = governor_run(candidates)
    assert len(result["survivors"]) + len(result["rejected"]) == len(candidates)


def test_can_execute_false_in_weather_gate():
    result = weather_check(_weather_candidate())
    assert result.get("can_execute", False) is False


def test_can_execute_false_in_sports_gate():
    result = sports_check(_sports_candidate(), inventory_signal="INVENTORY_READY")
    assert result.get("can_execute", False) is False


def test_can_execute_false_in_governor():
    result = governor_run([_weather_candidate()])
    for item in result["final_pool"]:
        assert item.get("can_execute", False) is False


def test_ledger_fields_pass_fail_logic():
    def _make_ledger_row(passed: bool, failure_cat: str | None) -> dict:
        return {
            "process_pass_fail": "PASS" if passed else "FAIL",
            "failure_category": failure_cat if not passed else None,
        }

    passing_row = _make_ledger_row(True, None)
    assert passing_row["process_pass_fail"] == "PASS"
    assert passing_row["failure_category"] is None
    failing_row = _make_ledger_row(False, "WEATHER_WATCH_NOT_ELIGIBLE")
    assert failing_row["process_pass_fail"] == "FAIL"
    assert failing_row["failure_category"] == "WEATHER_WATCH_NOT_ELIGIBLE"


def test_classify_weather_market():
    result = classify_market({"category": "weather", "market_type": "binary", "ticker": "KXNHIGH-NYC-25-2026-07-22"})
    assert result["eligible"] is True
    assert result["category"] == "weather"
    assert result["lane"] == "WEATHER_LANE"


def test_classify_sports_winner_market():
    result = classify_market({"category": "sports", "market_type": "full_game_outright_winner", "ticker": "KXMLBGAME-NYY-BOS"})
    assert result["eligible"] is True
    assert result["category"] == "sports_winner"
    assert result["lane"] == "SPORTS_WINNER_LANE"


def test_classify_weather_by_ticker_prefix():
    result = classify_market({"ticker": "KXNHIGH-LA-80-2026-07-22", "category": ""})
    assert result["eligible"] is True
    assert result["category"] == "weather"


def test_weather_gate_full_pass():
    result = weather_check(_weather_candidate())
    assert result["passed"] is True
    assert result["failure_gate"] is None
    assert len(result["gate_verdicts"]) == 12
    assert all(v["passed"] for v in result["gate_verdicts"])
    assert result["probability_governance_status"] == "V17_GOVERNED"
    assert result["governed_probability_eligible"] is True


def test_legacy_weather_price_sum_cannot_satisfy_v17_probability_gate():
    candidate = _weather_candidate()
    candidate.pop("weather_v17_probability_package")
    candidate.pop("probability_normalization_pass")
    candidate["brackets"] = [{"yes_price": 0.55}, {"yes_price": 0.45}]
    candidate["calibrated_prob_lower_bound"] = 0.70

    result = weather_check(candidate)

    assert result["passed"] is False
    assert result["failure_gate"] == 7
    assert result["failure_category"] == "V17_PROBABILITY_PACKAGE_REQUIRED"
    assert result["probability_governance_status"] == "LEGACY_RESEARCH_ONLY"
    assert result["governed_probability_eligible"] is False


def test_sports_gate_full_pass():
    result = sports_check(_sports_candidate(), inventory_signal="INVENTORY_READY")
    assert result["passed"] is True
    assert result["failure_gate"] is None
    assert len(result["gate_verdicts"]) == 10
    assert all(v["passed"] for v in result["gate_verdicts"])


def test_final_pool_max_two():
    candidates = [
        _weather_candidate({"event_id": "NYC-2026-07-22", "city": "NYC", "net_edge_lower_bound": 0.06}),
        _sports_candidate({"event_id": "MLB-NYY-BOS-2026-07-22", "net_edge_lower_bound": 0.045}),
        _sports_candidate({"event_id": "MLB-LAD-SF-2026-07-22", "ticker": "KXMLBGAME-LAD-SF", "net_edge_lower_bound": 0.035}),
    ]
    result = governor_run(candidates)
    assert len(result["final_pool"]) <= 2


def test_weather_scout_never_reaches_final_pool():
    result = weather_check(_weather_candidate({"confidence_tier": "WEATHER_SCOUT"}))
    assert result["passed"] is False
    assert result["failure_gate"] == 1


def test_weather_zero_edge_rejected():
    result = weather_check(_weather_candidate({"edge_lower_bound": 0.0}))
    assert result["passed"] is False
    assert result["failure_gate"] == 11
    assert result["failure_category"] == "EDGE_BELOW_FLOOR"


def test_sports_stale_price_gate_8():
    result = sports_check(
        _sports_candidate({"kalshi_orderbook_source": "direct_api", "price_age_minutes": 12.0}),
        inventory_signal="INVENTORY_READY",
    )
    assert result["passed"] is False
    assert result["failure_gate"] == 8
    assert result["failure_category"] == "STALE_PRICE"
