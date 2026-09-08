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


def _weather_candidate(overrides: dict | None = None) -> dict:
    base = {
        "category": "weather", "ticker": "KXNHIGH-NYC-25-2026-07-22", "city": "NYC", "scan_date": "2026-07-22",
        "confidence_tier": "WEATHER_MODEL_READY", "forecast_horizon_hours": 6.0, "sigma_f": 3.5,
        "settlement_station_verified": True, "nws_gridpoint_available": True, "bracket_coverage_complete": True,
        "probability_normalization_pass": True, "brackets": [{"yes_price": 0.55}, {"yes_price": 0.45}],
        "weather_v17_probability_package": {"probability_status": "COMPLETED", "calibration_status": "CALIBRATED", "calibrated_lower_bound": 0.72, "final_high_pmf": {"72": 0.30, "73": 0.70}, "can_execute": False},
        "market_open": True, "orderbook_nonempty": True, "price_age_minutes": 3.0, "edge_lower_bound": 0.052,
        "portfolio_check_passed": True, "portfolio_rejection_reason": None, "is_multi_leg": False,
        "event_id": "NYC-NHIGH-2026-07-22", "bracket_span_f": 3.0, "research_eligible": True,
        "net_edge_lower_bound": 0.052, "calibration_strength": 0.82, "model_uncertainty": 0.08,
        "calibrated_prob_lower_bound": 0.72, "settlement_clarity_grade": "A", "spread_cents": 3.0, "exposure_overlap": False,
    }
    if overrides: base.update(overrides)
    return base


def _sports_candidate(overrides: dict | None = None) -> dict:
    base = {
        "category": "sports_winner", "ticker": "KXMLBGAME-NYY-BOS-2026-07-22", "event_ticker": "KXMLBGAME-NYY-BOS-2026-07-22",
        "market_title": "New York Yankees vs Boston Red Sox", "settlement_condition": "The team with more runs wins the game.",
        "market_type": "full_game_outright_winner", "trading_active": True, "kalshi_orderbook_source": "direct_api", "price_age_minutes": 4.5,
        "calibrated_prob_lower_bound": 0.71, "lineup_status": "CONFIRMED", "consensus_odds": {"status": "AVAILABLE", "single_book_fallback": False, "consensus_fair_probability": 0.69},
        "market_prior_weight": 0.40, "net_edge_lower_bound": 0.031, "settlement_grade_result": {"settlement_risk": "LOW", "resolution_clarity_grade": "A"},
        "portfolio_check_passed": True, "portfolio_rejection_reason": None, "is_multi_leg": False, "event_id": "MLB-NYY-BOS-2026-07-22",
        "city": None, "scan_date": "2026-07-22", "research_eligible": True, "calibration_strength": 0.78, "model_uncertainty": 0.10,
        "settlement_clarity_grade": "A", "spread_cents": 4.0, "exposure_overlap": False,
    }
    if overrides: base.update(overrides)
    return base


def test_weather_routes_before_sports_equal_quality():
    result = governor_run([_weather_candidate({"net_edge_lower_bound":0.05,"calibration_strength":0.85}), _sports_candidate({"net_edge_lower_bound":0.05,"calibration_strength":0.80})])
    assert result["final_pool"] and result["final_pool"][0]["category"] == "weather"

def test_weather_watch_never_reaches_final_pool():
    r=weather_check(_weather_candidate({"confidence_tier":"WEATHER_WATCH"})); assert r["passed"] is False and r["failure_gate"]==1 and r["failure_category"]=="WEATHER_WATCH_NOT_ELIGIBLE"

def test_stale_weather_orderbook_data_unobtainable():
    r=weather_check(_weather_candidate({"price_age_minutes":15.0})); assert r["passed"] is False and r["failure_gate"]==10 and r["failure_category"]=="KALSHI_DATA_UNOBTAINABLE"

def test_sports_empty_inventory_stops_before_modeling():
    r=sports_check(_sports_candidate(),inventory_signal="INVENTORY_EMPTY"); assert r["passed"] is False and r["failure_gate"]==1 and r["failure_category"]=="INVENTORY_NOT_READY"

def test_upset_cannot_occupy_final_slot():
    r=sports_check(_sports_candidate({"calibrated_prob_lower_bound":0.52}),inventory_signal="INVENTORY_READY"); assert r["passed"] is False and r["failure_gate"]==5 and r["failure_category"]=="UPSET_REJECTED"

def test_high_prob_favorite_negative_edge_rejected():
    r=sports_check(_sports_candidate({"calibrated_prob_lower_bound":0.78,"net_edge_lower_bound":-0.012}),inventory_signal="INVENTORY_READY"); assert r["passed"] is False and r["failure_gate"]==9 and r["failure_category"]=="EDGE_BELOW_FLOOR"

def test_combo_market_rejected_as_combo():
    r=classify_market({"mve_collection_ticker":"KXMLBSLATE-2026-07-22","category":"sports","market_type":"full_game_outright_winner","ticker":"KXMLBSLATE-2026-07-22-A"}); assert r["eligible"] is False and r["rejection_code"]=="KALSHI_REJECT_COMBO_DISABLED" and r["category"]=="combo"

def test_multi_underlying_count_rejected():
    r=classify_market({"underlying_count":2,"category":"sports","market_type":"full_game_outright_winner","ticker":"KXMLBGAME-MULTI"}); assert r["eligible"] is False and r["rejection_code"]=="KALSHI_REJECT_COMBO_DISABLED"

def test_two_weather_same_city_date_portfolio_rejected():
    r=governor_run([_weather_candidate({"event_id":"NYC-NHIGH-2026-07-22-A"}),_weather_candidate({"event_id":"NYC-NHIGH-2026-07-22-B"})]); assert len(r["final_pool"])==1

def test_caller_supplied_price_cannot_pass_sports_gate():
    r=sports_check(_sports_candidate({"kalshi_orderbook_source":"caller_supplied"}),inventory_signal="INVENTORY_READY"); assert r["passed"] is False and r["failure_gate"]==8 and "NOT_DIRECT_API" in r["failure_category"]

def test_screenshot_price_cannot_pass_sports_gate():
    r=sports_check(_sports_candidate({"kalshi_orderbook_source":"screenshot"}),inventory_signal="INVENTORY_READY"); assert r["passed"] is False and r["failure_gate"]==8

def test_economics_never_gets_probability():
    for c in ("economics","macro_economics","scheduled_economics"):
        r=classify_market({"category":c,"ticker":"KXECON-TEST","market_type":"binary"}); assert r["eligible"] is False and r["rejection_code"]=="RESEARCH_LANE_NOT_BUILT" and r["lane"]=="RESEARCH_LANE_NOT_BUILT"

def test_disabled_categories_never_fall_through():
    for c in ("politics","entertainment","mentions","breaking_news","celebrity"):
        r=classify_market({"category":c,"market_type":"binary","ticker":"KX"}); assert r["eligible"] is False and r["rejection_code"]=="CATEGORY_DISABLED_OR_UNSUPPORTED"

def test_sports_derivative_not_eligible():
    r=classify_market({"category":"baseball","market_type":"first_5_innings_winner","ticker":"KXMLBF5-TEST"}); assert r["eligible"] is False and r["rejection_code"]=="CATEGORY_DISABLED_OR_UNSUPPORTED"

def test_final_pool_can_be_zero():
    r=governor_run([]); assert r["final_pool"]==[] and r["survivors"]==[] and r["rejected"]==[]

def test_final_pool_can_be_one():
    assert len(governor_run([_weather_candidate(),_weather_candidate({"event_id":"NYC-NHIGH-2026-07-22-B","net_edge_lower_bound":0.07})])["final_pool"])==1

def test_every_candidate_reaches_portfolio_governor():
    cs=[_weather_candidate(),_sports_candidate(),_sports_candidate({"event_id":"MLB-LAD-SF-2026-07-22","ticker":"KXMLBGAME-LAD-SF","net_edge_lower_bound":0.028})]; r=governor_run(cs); assert len(r["survivors"])+len(r["rejected"])==len(cs)

def test_can_execute_false_in_weather_gate(): assert weather_check(_weather_candidate()).get("can_execute",False) is False

def test_can_execute_false_in_sports_gate(): assert sports_check(_sports_candidate(),inventory_signal="INVENTORY_READY").get("can_execute",False) is False

def test_can_execute_false_in_governor():
    for item in governor_run([_weather_candidate()])["final_pool"]: assert item.get("can_execute",False) is False

def test_ledger_fields_pass_fail_logic():
    p={"process_pass_fail":"PASS","failure_category":None}; f={"process_pass_fail":"FAIL","failure_category":"WEATHER_WATCH_NOT_ELIGIBLE"}; assert p["failure_category"] is None and f["failure_category"]=="WEATHER_WATCH_NOT_ELIGIBLE"

def test_classify_weather_market():
    r=classify_market({"category":"weather","market_type":"binary","ticker":"KXNHIGH-NYC-25-2026-07-22"}); assert r["eligible"] and r["category"]=="weather" and r["lane"]=="WEATHER_LANE"

def test_classify_sports_winner_market():
    r=classify_market({"category":"sports","market_type":"full_game_outright_winner","ticker":"KXMLBGAME-NYY-BOS"}); assert r["eligible"] and r["category"]=="sports_winner" and r["lane"]=="SPORTS_WINNER_LANE"

def test_classify_weather_by_ticker_prefix(): assert classify_market({"ticker":"KXNHIGH-LA-80-2026-07-22","category":""})["category"]=="weather"

def test_weather_gate_full_pass():
    r=weather_check(_weather_candidate()); assert r["passed"] is True and r["failure_gate"] is None and len(r["gate_verdicts"])==12 and r["probability_governance_status"]=="V17_GOVERNED" and r["governed_probability_eligible"] is True

def test_legacy_weather_price_sum_cannot_satisfy_v17_probability_gate():
    c=_weather_candidate(); c.pop("weather_v17_probability_package"); c.pop("probability_normalization_pass"); c["brackets"]=[{"yes_price":0.55},{"yes_price":0.45}]; c["calibrated_prob_lower_bound"]=0.70; r=weather_check(c); assert r["passed"] is False and r["failure_gate"]==7 and r["failure_category"]=="V17_PROBABILITY_PACKAGE_REQUIRED" and r["probability_governance_status"]=="LEGACY_RESEARCH_ONLY" and r["governed_probability_eligible"] is False

def test_sports_gate_full_pass():
    r=sports_check(_sports_candidate(),inventory_signal="INVENTORY_READY"); assert r["passed"] is True and r["failure_gate"] is None and len(r["gate_verdicts"])==10

def test_final_pool_max_two(): assert len(governor_run([_weather_candidate(),_sports_candidate(),_sports_candidate({"event_id":"MLB-LAD-SF","ticker":"KXMLBGAME-LAD-SF"})])["final_pool"])<=2

def test_weather_scout_never_reaches_final_pool():
    r=weather_check(_weather_candidate({"confidence_tier":"WEATHER_SCOUT"})); assert r["passed"] is False and r["failure_gate"]==1

def test_weather_zero_edge_rejected():
    r=weather_check(_weather_candidate({"edge_lower_bound":0.0})); assert r["passed"] is False and r["failure_gate"]==11 and r["failure_category"]=="EDGE_BELOW_FLOOR"

def test_sports_stale_price_gate_8():
    r=sports_check(_sports_candidate({"kalshi_orderbook_source":"direct_api","price_age_minutes":12.0}),inventory_signal="INVENTORY_READY"); assert r["passed"] is False and r["failure_gate"]==8 and r["failure_category"]=="STALE_PRICE"
