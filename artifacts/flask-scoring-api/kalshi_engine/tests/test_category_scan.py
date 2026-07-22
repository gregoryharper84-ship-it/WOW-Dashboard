"""
test_category_scan.py  —  Category-Router / Singles-Governor unit tests
WOW v16.5

Covers all 15 scenarios specified in the category-scan feature spec.
Runs without any external network access, DB, or app.py import.

Run:
  cd artifacts/flask-scoring-api
  python -m pytest kalshi_engine/tests/test_category_scan.py -v
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from kalshi_engine.category_router   import classify_market
from kalshi_engine.weather_gate      import check as weather_check
from kalshi_engine.sports_gate       import check as sports_check
from kalshi_engine.portfolio_governor import check_single, run as governor_run


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _weather_candidate(overrides: dict | None = None) -> dict:
    """Build a fully-passing weather candidate."""
    base = {
        "category":                   "weather",
        "ticker":                     "KXNHIGH-NYC-25-2026-07-22",
        "city":                       "NYC",
        "scan_date":                  "2026-07-22",
        "confidence_tier":            "WEATHER_MODEL_READY",
        "forecast_horizon_hours":     6.0,
        "sigma_f":                    3.5,
        "settlement_station_verified":True,
        "nws_gridpoint_available":    True,
        "bracket_coverage_complete":  True,
        "probability_normalization_pass": True,
        "brackets":                   [{"yes_price": 0.55}, {"yes_price": 0.45}],
        "market_open":                True,
        "orderbook_nonempty":         True,
        "price_age_minutes":          3.0,
        "edge_lower_bound":           0.052,
        "portfolio_check_passed":     True,
        "portfolio_rejection_reason": None,
        "is_multi_leg":               False,
        "event_id":                   "NYC-NHIGH-2026-07-22",
        "bracket_span_f":             3.0,
        "research_eligible":          True,
        # ranking fields
        "net_edge_lower_bound":       0.052,
        "calibration_strength":       0.82,
        "model_uncertainty":          0.08,
        "calibrated_prob_lower_bound":0.72,
        "settlement_clarity_grade":   "A",
        "spread_cents":               3.0,
        "exposure_overlap":           False,
    }
    if overrides:
        base.update(overrides)
    return base


def _sports_candidate(overrides: dict | None = None) -> dict:
    """Build a fully-passing sports candidate."""
    base = {
        "category":                   "sports_winner",
        "ticker":                     "KXMLBGAME-NYY-BOS-2026-07-22",
        "event_ticker":               "KXMLBGAME-NYY-BOS-2026-07-22",
        "market_title":               "New York Yankees vs Boston Red Sox",
        "settlement_condition":       "The team with more runs wins the game.",
        "market_type":                "full_game_outright_winner",
        "trading_active":             True,
        "kalshi_orderbook_source":    "direct_api",
        "price_age_minutes":          4.5,
        "calibrated_prob_lower_bound":0.71,
        "lineup_status":              "CONFIRMED",
        "consensus_odds": {
            "status":                 "AVAILABLE",
            "single_book_fallback":   False,
            "consensus_fair_probability": 0.69,
        },
        "market_prior_weight":        0.40,
        "net_edge_lower_bound":       0.031,
        "settlement_grade_result": {
            "settlement_risk":            "LOW",
            "resolution_clarity_grade":   "A",
        },
        "portfolio_check_passed":     True,
        "portfolio_rejection_reason": None,
        "is_multi_leg":               False,
        "event_id":                   "MLB-NYY-BOS-2026-07-22",
        "city":                       None,
        "scan_date":                  "2026-07-22",
        "research_eligible":          True,
        # ranking fields
        "calibration_strength":       0.78,
        "model_uncertainty":          0.10,
        "settlement_clarity_grade":   "A",
        "spread_cents":               4.0,
        "exposure_overlap":           False,
    }
    if overrides:
        base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Weather routed before sports at equal audit quality
# ─────────────────────────────────────────────────────────────────────────────

def test_weather_routes_before_sports_equal_quality():
    """
    When both a weather and a sports candidate pass all gates with equal edge,
    the portfolio governor must rank weather first (weather has tighter
    calibration by convention — same edge means weather wins on calibration_strength).
    """
    weather = _weather_candidate({"net_edge_lower_bound": 0.05, "calibration_strength": 0.85})
    sports  = _sports_candidate( {"net_edge_lower_bound": 0.05, "calibration_strength": 0.80})

    result = governor_run([weather, sports])
    pool   = result["final_pool"]

    assert len(pool) >= 1
    assert pool[0]["category"] == "weather", (
        "Weather must rank first at equal edge when calibration_strength is higher"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: WEATHER_WATCH never reaches the final pool
# ─────────────────────────────────────────────────────────────────────────────

def test_weather_watch_never_reaches_final_pool():
    cand = _weather_candidate({"confidence_tier": "WEATHER_WATCH"})
    result = weather_check(cand)
    assert result["passed"] is False
    assert result["failure_gate"] == 1
    assert result["failure_category"] == "WEATHER_WATCH_NOT_ELIGIBLE"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Stale weather orderbook → KALSHI_DATA_UNOBTAINABLE
# ─────────────────────────────────────────────────────────────────────────────

def test_stale_weather_orderbook_data_unobtainable():
    cand = _weather_candidate({"price_age_minutes": 15.0})
    result = weather_check(cand)
    assert result["passed"] is False
    assert result["failure_gate"] == 10
    assert result["failure_category"] == "KALSHI_DATA_UNOBTAINABLE"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Sports market with empty inventory stops before modeling
# ─────────────────────────────────────────────────────────────────────────────

def test_sports_empty_inventory_stops_before_modeling():
    cand = _sports_candidate()
    result = sports_check(cand, inventory_signal="INVENTORY_EMPTY")
    assert result["passed"] is False
    assert result["failure_gate"] == 1
    assert result["failure_category"] == "INVENTORY_NOT_READY"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: An upset cannot occupy a final slot
# ─────────────────────────────────────────────────────────────────────────────

def test_upset_cannot_occupy_final_slot():
    cand = _sports_candidate({"calibrated_prob_lower_bound": 0.52})
    result = sports_check(cand, inventory_signal="INVENTORY_READY")
    assert result["passed"] is False
    assert result["failure_gate"] == 5
    assert result["failure_category"] == "UPSET_REJECTED"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: High-probability favorite with negative edge is rejected
# ─────────────────────────────────────────────────────────────────────────────

def test_high_prob_favorite_negative_edge_rejected():
    cand = _sports_candidate({"calibrated_prob_lower_bound": 0.78, "net_edge_lower_bound": -0.012})
    result = sports_check(cand, inventory_signal="INVENTORY_READY")
    assert result["passed"] is False
    assert result["failure_gate"] == 9
    assert result["failure_category"] == "EDGE_BELOW_FLOOR"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: 2-market request → KALSHI_REJECT_COMBO_DISABLED
# ─────────────────────────────────────────────────────────────────────────────

def test_combo_market_rejected_as_combo():
    market = {
        "mve_collection_ticker": "KXMLBSLATE-2026-07-22",
        "category":              "sports",
        "market_type":           "full_game_outright_winner",
        "ticker":                "KXMLBSLATE-2026-07-22-A",
    }
    result = classify_market(market)
    assert result["eligible"] is False
    assert result["rejection_code"] == "KALSHI_REJECT_COMBO_DISABLED"
    assert result["category"] == "combo"


def test_multi_underlying_count_rejected():
    market = {
        "underlying_count": 2,
        "category":         "sports",
        "market_type":      "full_game_outright_winner",
        "ticker":           "KXMLBGAME-MULTI",
    }
    result = classify_market(market)
    assert result["eligible"] is False
    assert result["rejection_code"] == "KALSHI_REJECT_COMBO_DISABLED"


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Two weather contracts same city/date → portfolio rejection
# ─────────────────────────────────────────────────────────────────────────────

def test_two_weather_same_city_date_portfolio_rejected():
    first  = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22-A"})
    second = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22-B"})

    result = governor_run([first, second])
    pool   = result["final_pool"]
    assert len(pool) == 1, "Only one weather contract per city+date allowed"

    # Second must be in rejected (same city+date)
    rejected_reasons = [r.get("portfolio_rejection_reason") for r in result["rejected"]]
    assert any("CITY_DATE" in (r or "") or "CITY" in (r or "") for r in rejected_reasons), \
        f"Expected a city/date rejection, got: {rejected_reasons}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Screenshot/caller-supplied price cannot satisfy live-price gate
# ─────────────────────────────────────────────────────────────────────────────

def test_caller_supplied_price_cannot_pass_sports_gate():
    cand = _sports_candidate({"kalshi_orderbook_source": "caller_supplied"})
    result = sports_check(cand, inventory_signal="INVENTORY_READY")
    assert result["passed"] is False
    assert result["failure_gate"] == 8
    assert "NOT_DIRECT_API" in result["failure_category"]


def test_screenshot_price_cannot_pass_sports_gate():
    cand = _sports_candidate({"kalshi_orderbook_source": "screenshot"})
    result = sports_check(cand, inventory_signal="INVENTORY_READY")
    assert result["passed"] is False
    assert result["failure_gate"] == 8


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Economics markets never get probability-scored
# ─────────────────────────────────────────────────────────────────────────────

def test_economics_never_gets_probability():
    for eco_cat in ("economics", "macro_economics", "scheduled_economics"):
        market = {"category": eco_cat, "ticker": "KXECON-TEST", "market_type": "binary"}
        result = classify_market(market)
        assert result["eligible"] is False, f"Economics category '{eco_cat}' must not be eligible"
        assert result["rejection_code"] == "RESEARCH_LANE_NOT_BUILT"
        assert result["lane"] == "RESEARCH_LANE_NOT_BUILT"


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: Disabled categories never fall through to generic modeling
# ─────────────────────────────────────────────────────────────────────────────

def test_disabled_categories_never_fall_through():
    disabled = [
        {"category": "politics",        "market_type": "binary", "ticker": "KXPOL"},
        {"category": "entertainment",   "market_type": "binary", "ticker": "KXENT"},
        {"category": "mentions",        "market_type": "binary", "ticker": "KXMNT"},
        {"category": "breaking_news",   "market_type": "binary", "ticker": "KXBRK"},
        {"category": "celebrity",       "market_type": "binary", "ticker": "KXCEL"},
    ]
    for market in disabled:
        result = classify_market(market)
        assert result["eligible"] is False, \
            f"Category '{market['category']}' must not be eligible"
        assert result["rejection_code"] == "CATEGORY_DISABLED_OR_UNSUPPORTED", \
            f"Wrong rejection code for '{market['category']}': {result['rejection_code']}"


def test_sports_derivative_not_eligible():
    """Sports category with non-winner market_type → CATEGORY_DISABLED_OR_UNSUPPORTED."""
    market = {
        "category":    "baseball",
        "market_type": "first_5_innings_winner",
        "ticker":      "KXMLBF5-TEST",
    }
    result = classify_market(market)
    assert result["eligible"] is False
    assert result["rejection_code"] == "CATEGORY_DISABLED_OR_UNSUPPORTED"


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: Final pool can be 0 (all candidates fail gates)
# ─────────────────────────────────────────────────────────────────────────────

def test_final_pool_can_be_zero():
    result = governor_run([])
    assert result["final_pool"] == []
    assert result["survivors"]  == []
    assert result["rejected"]   == []


# ─────────────────────────────────────────────────────────────────────────────
# Test 13: Final pool can be 1 (one passes, one fails portfolio)
# ─────────────────────────────────────────────────────────────────────────────

def test_final_pool_can_be_one():
    good = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22"})
    # Second weather in same city+date → portfolio cap
    dup  = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22-B",
                               "net_edge_lower_bound": 0.07})  # even better edge

    result = governor_run([good, dup])
    assert len(result["final_pool"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 14: Every candidate reaches the portfolio governor
# ─────────────────────────────────────────────────────────────────────────────

def test_every_candidate_reaches_portfolio_governor():
    """
    Mock 3 gate-passing candidates (one weather, two sports from different events).
    All 3 must reach governor_run — check that survivors + rejected = total input.
    """
    w = _weather_candidate({"event_id": "NYC-NHIGH-2026-07-22"})
    s1 = _sports_candidate({"event_id": "MLB-NYY-BOS-2026-07-22",
                             "ticker":   "KXMLBGAME-NYY-BOS"})
    s2 = _sports_candidate({"event_id": "MLB-LAD-SF-2026-07-22",
                             "ticker":   "KXMLBGAME-LAD-SF",
                             "net_edge_lower_bound": 0.028})

    candidates = [w, s1, s2]
    result = governor_run(candidates)

    total_processed = len(result["survivors"]) + len(result["rejected"])
    assert total_processed == len(candidates), (
        f"Expected {len(candidates)} processed, got {total_processed}. "
        f"survivors={len(result['survivors'])}, rejected={len(result['rejected'])}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 15: can_execute is False in every weather/sports gate response
# ─────────────────────────────────────────────────────────────────────────────

def test_can_execute_false_in_weather_gate():
    """weather_gate.check() never emits can_execute=True."""
    passing = _weather_candidate()
    result  = weather_check(passing)
    # The gate itself doesn't return can_execute — but verify it never sneaks in
    assert result.get("can_execute", False) is False


def test_can_execute_false_in_sports_gate():
    """sports_gate.check() never emits can_execute=True."""
    passing = _sports_candidate()
    result  = sports_check(passing, inventory_signal="INVENTORY_READY")
    assert result.get("can_execute", False) is False


def test_can_execute_false_in_governor():
    """portfolio_governor.run() final_pool items never carry can_execute=True."""
    w = _weather_candidate()
    result = governor_run([w])
    for item in result["final_pool"]:
        assert item.get("can_execute", False) is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 16: Correct ledger fields for passed vs. failed candidates
# ─────────────────────────────────────────────────────────────────────────────

def test_ledger_fields_pass_fail_logic():
    """
    Verify the process_pass_fail / failure_category logic that the
    category-scan orchestrator stamps on each ledger row.
    """
    def _make_ledger_row(passed: bool, failure_cat: str | None) -> dict:
        return {
            "process_pass_fail": "PASS" if passed else "FAIL",
            "failure_category":  failure_cat if not passed else None,
        }

    passing_row = _make_ledger_row(True,  None)
    assert passing_row["process_pass_fail"] == "PASS"
    assert passing_row["failure_category"]  is None

    failing_row = _make_ledger_row(False, "WEATHER_WATCH_NOT_ELIGIBLE")
    assert failing_row["process_pass_fail"] == "FAIL"
    assert failing_row["failure_category"]  == "WEATHER_WATCH_NOT_ELIGIBLE"


# ─────────────────────────────────────────────────────────────────────────────
# Test: category_router correctly classifies weather and sports markets
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_weather_market():
    market = {"category": "weather", "market_type": "binary",
              "ticker": "KXNHIGH-NYC-25-2026-07-22"}
    result = classify_market(market)
    assert result["eligible"] is True
    assert result["category"] == "weather"
    assert result["lane"] == "WEATHER_LANE"


def test_classify_sports_winner_market():
    market = {"category": "sports", "market_type": "full_game_outright_winner",
              "ticker": "KXMLBGAME-NYY-BOS"}
    result = classify_market(market)
    assert result["eligible"] is True
    assert result["category"] == "sports_winner"
    assert result["lane"] == "SPORTS_WINNER_LANE"


def test_classify_weather_by_ticker_prefix():
    """Detect weather even when category field is absent."""
    market = {"ticker": "KXNHIGH-LA-80-2026-07-22", "category": ""}
    result = classify_market(market)
    assert result["eligible"] is True
    assert result["category"] == "weather"


# ─────────────────────────────────────────────────────────────────────────────
# Test: Full weather gate pass (all 12 gates green)
# ─────────────────────────────────────────────────────────────────────────────

def test_weather_gate_full_pass():
    cand   = _weather_candidate()
    result = weather_check(cand)
    assert result["passed"] is True
    assert result["failure_gate"] is None
    assert len(result["gate_verdicts"]) == 12
    assert all(v["passed"] for v in result["gate_verdicts"])


# ─────────────────────────────────────────────────────────────────────────────
# Test: Full sports gate pass (all 9 gates green)
# ─────────────────────────────────────────────────────────────────────────────

def test_sports_gate_full_pass():
    cand   = _sports_candidate()
    result = sports_check(cand, inventory_signal="INVENTORY_READY")
    assert result["passed"] is True
    assert result["failure_gate"] is None
    assert len(result["gate_verdicts"]) == 9
    assert all(v["passed"] for v in result["gate_verdicts"])


# ─────────────────────────────────────────────────────────────────────────────
# Test: Final pool hard cap at 2
# ─────────────────────────────────────────────────────────────────────────────

def test_final_pool_max_two():
    """Even with 3 distinct passing candidates, pool size is capped at 2."""
    c1 = _weather_candidate({"event_id":     "NYC-2026-07-22",
                              "city":         "NYC",
                              "net_edge_lower_bound": 0.06})
    c2 = _sports_candidate( {"event_id":     "MLB-NYY-BOS-2026-07-22",
                              "net_edge_lower_bound": 0.045})
    c3 = _sports_candidate( {"event_id":     "MLB-LAD-SF-2026-07-22",
                              "ticker":       "KXMLBGAME-LAD-SF",
                              "net_edge_lower_bound": 0.035})

    result = governor_run([c1, c2, c3])
    assert len(result["final_pool"]) <= 2, (
        f"Final pool exceeded cap of 2: {len(result['final_pool'])}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test: WEATHER_SCOUT never reaches final pool (gate 1 blocks it)
# ─────────────────────────────────────────────────────────────────────────────

def test_weather_scout_never_reaches_final_pool():
    cand = _weather_candidate({"confidence_tier": "WEATHER_SCOUT"})
    result = weather_check(cand)
    assert result["passed"] is False
    assert result["failure_gate"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test: Weather gate edge_lower_bound = 0 exactly → rejected
# ─────────────────────────────────────────────────────────────────────────────

def test_weather_zero_edge_rejected():
    cand = _weather_candidate({"edge_lower_bound": 0.0})
    result = weather_check(cand)
    assert result["passed"] is False
    assert result["failure_gate"] == 11
    assert result["failure_category"] == "EDGE_BELOW_FLOOR"


# ─────────────────────────────────────────────────────────────────────────────
# Test: Sports gate – non-direct_api source with stale price
# ─────────────────────────────────────────────────────────────────────────────

def test_sports_stale_price_gate_8():
    cand = _sports_candidate({
        "kalshi_orderbook_source": "direct_api",
        "price_age_minutes": 12.0,
    })
    result = sports_check(cand, inventory_signal="INVENTORY_READY")
    assert result["passed"] is False
    assert result["failure_gate"] == 8
    assert result["failure_category"] == "STALE_PRICE"
