"""Regression tests for research-only Kalshi slip topology and shared failure sources."""
import importlib.util
from pathlib import Path

import pytest

FILE = Path(__file__).with_name("research") / "kalshi_slip_construction_taxonomy.py"
spec = importlib.util.spec_from_file_location("kalshi_slip_construction_taxonomy", FILE)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
diagnose = module.diagnose_slip_structure


def l(sport, event, player, team, stat, market):
    return {"sport": sport, "event_id": event, "player_id": player,
            "team_id": team, "stat_family": stat, "market_id": market}


def test_same_player_two_stats_detects_common_cause_not_certified_independence():
    r = diagnose([l("NBA", "g", "p1", "a", "POINTS", "m1"),
                  l("NBA", "g", "p1", "a", "ASSISTS", "m2")])
    assert r["structure"] == "SINGLE_PLAYER_SAME_GAME_MULTI_MARKET"
    assert "ONE_PLAYER_MINUTES_OR_AVAILABILITY_COMMON_CAUSE" in r["shared_failure_factors"]
    assert r["same_team_receiver_competition_groups"] == 0
    assert r["complementary_stat_player_groups"] == 1
    assert r["verified_nested_threshold_groups"] == 0
    assert r["joint_probability"] is None
    assert r["can_execute"] is False


def test_competing_same_team_receiver_tails_flag_opportunity_allocation():
    r = diagnose([
        l("NFL", "g", "cin1", "CIN", "RECEIVING_YARDS", "m1"),
        l("NFL", "g", "cin2", "CIN", "RECEIVING_YARDS", "m2"),
        l("NFL", "g", "atl1", "ATL", "RECEIVING_YARDS", "m3"),
        l("NFL", "g", "atl2", "ATL", "RUSHING_YARDS", "m4"),
    ])
    assert r["structure"] == "MULTI_PLAYER_SAME_GAME"
    assert r["same_team_receiver_competition_groups"] == 1
    assert "SAME_TEAM_RECEIVING_OPPORTUNITY_COMPETITION" in r["shared_failure_factors"]
    assert r["correlation_direction"] == "UNKNOWN_NOT_ESTIMATED"


def test_cross_event_not_silently_same_game():
    r = diagnose([l("NBA", "g1", "a", "x", "POINTS", "m1"),
                  l("NBA", "g2", "b", "y", "POINTS", "m2")])
    assert r["structure"] == "MULTI_EVENT_COMBINATION"
    assert "SAME_GAME_SCRIPT_COMMON_CAUSE" not in r["shared_failure_factors"]


def test_ambiguous_market_types_are_not_imputed_from_threshold_text():
    r = diagnose([l("NBA", "g", "a", "x", "UNKNOWN", "m1"),
                  l("NBA", "g", "a", "x", "POINTS", "m2")])
    assert r["status"] == "UNVERIFIED_STRUCTURE_METADATA"
    assert "leg[0].stat_family" in r["missing_fields"]
    assert r["structure"] is None
    assert r["joint_probability"] is None
    assert r["model_feature_eligible"] is False


def test_no_stable_market_id_prevents_structural_classification():
    r = diagnose([l("NFL", "g", "a", "x", "RECEIVING_YARDS", ""),
                  l("NFL", "g", "b", "x", "RECEIVING_YARDS", "m2")])
    assert r["status"] == "UNVERIFIED_STRUCTURE_METADATA"


def test_duplicate_market_identity_flagged():
    r = diagnose([l("NBA", "g", "p", "x", "POINTS", "m"),
                  l("NBA", "g", "p", "x", "POINTS", "m")])
    assert r["duplicate_market_count"] == 1
    assert "DUPLICATE_MARKET_IDENTITY" in r["shared_failure_factors"]


@pytest.mark.parametrize("value", [[], [{}], [42, {}], None])
def test_invalid_card_shapes_fail_closed(value):
    with pytest.raises(ValueError):
        diagnose(value)


def test_no_classification_can_become_fitted_model_or_live_execution():
    r = diagnose([l("NFL", "g", "a", "x", "POINTS", "m1"),
                  l("NFL", "g", "b", "y", "POINTS", "m2")])
    assert r["calibration_eligible"] is False
    assert r["probability_publishable"] is False
    assert r["can_execute"] is False


def test_nested_threshold_requires_matching_stat_period_rule_and_direction():
    a = l("NBA", "g", "p", "x", "THREES_MADE", "m1")
    b = l("NBA", "g", "p", "x", "THREES_MADE", "m2")
    a.update(threshold=3, direction="GE", period_id="FULL", settlement_rule_id="v1")
    b.update(threshold=4, direction="GE", period_id="FULL", settlement_rule_id="v1")
    r = diagnose([a, b])
    assert r["potential_same_stat_groups"] == 1
    assert r["verified_nested_threshold_groups"] == 1
    assert "VERIFIED_NESTED_THRESHOLD_REDUNDANCY" in r["shared_failure_factors"]
    assert r["joint_probability"] is None


def test_same_stat_without_exact_threshold_qualified_as_potential_only():
    a = l("NBA", "g", "p", "x", "THREES_MADE", "m1")
    b = l("NBA", "g", "p", "x", "THREES_MADE", "m2")
    r = diagnose([a, b])
    assert r["potential_same_stat_groups"] == 1
    assert r["verified_nested_threshold_groups"] == 0


def test_same_stat_mixed_period_not_confirmed_as_nested():
    a = l("NBA", "g", "p", "x", "POINTS", "m1")
    b = l("NBA", "g", "p", "x", "POINTS", "m2")
    a.update(threshold=20, direction="GE", period_id="FIRST_HALF",
             settlement_rule_id="v1")
    b.update(threshold=30, direction="GE", period_id="FULL",
             settlement_rule_id="v1")
    r = diagnose([a, b])
    assert r["potential_same_stat_groups"] == 1
    assert r["verified_nested_threshold_groups"] == 0


def test_malformed_threshold_and_direction_rejected():
    a = l("NBA", "g", "p", "x", "POINTS", "m1")
    b = l("NBA", "g", "p", "x", "POINTS", "m2")
    a["threshold"] = float("nan")
    with pytest.raises(ValueError, match="finite numeric"):
        diagnose([a, b])
    a["threshold"] = 20
    a["direction"] = "UNKNOWN"
    with pytest.raises(ValueError, match="direction must"):
        diagnose([a, b])


def test_mixed_event_card_keeps_repeated_player_and_game_risks():
    r = diagnose([
        l("NFL", "event1", "player1", "TEAM1", "RECEIVING_YARDS", "m1"),
        l("NFL", "event1", "player1", "TEAM1", "RECEPTIONS", "m2"),
        l("NFL", "event2", "player2", "TEAM2", "RUSHING_YARDS", "m3"),
    ])
    assert r["structure"] == "MULTI_EVENT_COMBINATION"
    assert "REPEATED_EVENT_EXPOSURE" in r["shared_failure_factors"]
    assert "REPEATED_PLAYER_EXPOSURE" in r["shared_failure_factors"]
    assert "SAME_GAME_SCRIPT_COMMON_CAUSE" in r["shared_failure_factors"]
    assert "ONE_PLAYER_MINUTES_OR_AVAILABILITY_COMMON_CAUSE" in r["shared_failure_factors"]


@pytest.mark.parametrize("field", ["period_id", "settlement_rule_id"])
@pytest.mark.parametrize("sentinel", ["UNKNOWN", " UNVERIFIED ", "N/A"])
def test_unverified_nesting_identifiers_are_never_confirmed(field, sentinel):
    a = l("NBA", "g", "p", "t", "POINTS", "m1")
    b = l("NBA", "g", "p", "t", "POINTS", "m2")
    for leg, threshold in ((a, 20), (b, 30)):
        leg.update(threshold=threshold, direction="GE",
                   period_id="FULL", settlement_rule_id="EXACT_R1")
    b[field] = sentinel
    r = diagnose([a, b])
    assert r["potential_same_stat_groups"] == 1
    assert r["verified_nested_threshold_groups"] == 0
    assert "VERIFIED_NESTED_THRESHOLD_REDUNDANCY" not in r["shared_failure_factors"]
