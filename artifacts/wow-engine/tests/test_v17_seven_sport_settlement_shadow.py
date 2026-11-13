"""Fail-closed sport settlement over an already-computed joint-game shadow."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest

from v17.seven_sport_settlement_shadow import settle_seven_sport_shadow

START = datetime(2026, 11, 14, 1, tzinfo=timezone.utc)


def packet(sport="NCAAF", *, home_spread=-3, game_total=49.5, draw=0.1):
    return {
        "event_id": f"fixture:{sport}:17", "fitted_source_model_id": "research-fitted-source",
        "fitted_artifact_sha256": "a" * 64, "captured_at": (START - timedelta(hours=1)).isoformat(),
        "event_start_at": START.isoformat(), "simulation_seed": 42, "draw_count": 50000,
        "moneyline_tie_rule": "THREE_WAY" if sport == "SOCCER" else (
            "PUSH" if sport in {"NFL", "NCAAF"} else "NO_TIES"),
        "moneyline": {"home_win": 0.5, "away_win": 0.5 - draw, "draw": draw},
        "home_spread": {"line": home_spread, "more": 0.3, "exact": 0.2 if home_spread == int(home_spread) else 0, "less": 0.5 if home_spread == int(home_spread) else 0.7},
        "game_total": {"line": game_total, "more": 0.4, "exact": 0.1 if game_total == int(game_total) else 0, "less": 0.5 if game_total == int(game_total) else 0.6},
        "player_props": {}, "shadow_only": True, "probability_publishable": False,
        "automatic_certification": False, "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def settlement(sport):
    return {
        "NFL": "FULL_GAME_INCLUDING_OVERTIME", "NCAAF": "FULL_GAME_INCLUDING_OVERTIME",
        "NBA": "FULL_GAME_INCLUDING_OVERTIME", "NCAAB": "FULL_GAME_INCLUDING_OVERTIME",
        "NHL": "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT", "SOCCER": "REGULATION",
        "TENNIS": "COMPLETED_MATCH_NO_RETIREMENT",
    }[sport]


def test_ncaaf_key_number_push_and_total_normalization():
    r = settle_seven_sport_shadow(sport="NCAAF", settlement_basis=settlement("NCAAF"), projection=packet())
    assert r["status"] == "RESEARCH_ONLY"
    assert r["winner"] == {"home": .5, "away": .4, "draw": .1, "tie_settlement": "PUSH"}
    assert r["spread"] == {"signed_home_line": -3.0, "p_cover": .3, "p_push": .2, "p_not_cover": .5}
    assert r["total"] == {"line": 49.5, "p_over": .4, "p_push": 0, "p_under": .6}
    assert r["probability_publishable"] is False and r["rank_eligible"] is False
    assert r["can_execute"] is False and r["model_certified"] is False


@pytest.mark.parametrize("sport", ["NFL", "NCAAF", "NBA", "NCAAB", "NHL", "SOCCER"])
def test_six_team_sports_validated_and_independently_settled(sport):
    p = packet(sport, home_spread=-2.5, game_total=49.5, draw=.1 if sport in {"NFL", "NCAAF", "SOCCER"} else 0)
    if sport == "NHL":
        p["home_spread"]["line"] = -1.5
    r = settle_seven_sport_shadow(sport=sport, settlement_basis=settlement(sport), projection=p)
    assert r["status"] == "RESEARCH_ONLY"
    assert r["winner"]["draw"] == p["moneyline"]["draw"]
    assert sum(r["total"][x] for x in ("p_over", "p_under", "p_push")) == pytest.approx(1)
    assert sum(r["spread"][x] for x in ("p_cover", "p_push", "p_not_cover")) == pytest.approx(1)


def test_tennis_games_cannot_be_inferred_as_match_winner():
    p = packet("TENNIS", home_spread=-2.5, game_total=22.5, draw=0)
    p["moneyline"] = {"home_win": 0.7, "away_win": .3, "draw": 0}
    r = settle_seven_sport_shadow(sport="TENNIS", settlement_basis=settlement("TENNIS"), projection=p, score_unit="MATCH_GAMES")
    assert r["winner"] is None
    assert "TENNIS_MATCH_WINNER_NOT_DERIVABLE_FROM_GAME_COUNTS" in r["reason_codes"]
    assert r["spread"]["p_cover"] == .3


@pytest.mark.parametrize("sport,bad_basis", [("NHL", "REGULATION"), ("SOCCER", "FULL_GAME_INCLUDING_OVERTIME"), ("TENNIS", "REGULATION"), ("NCAAB", "REGULATION")])
def test_mismatched_settlement_basis_fails_closed(sport, bad_basis):
    with pytest.raises(ValueError, match="SPORT_SETTLEMENT_BASIS_MISMATCH"):
        settle_seven_sport_shadow(sport=sport, settlement_basis=bad_basis, projection=packet(sport))


def test_nhl_full_game_requires_resolved_no_tie_winner():
    p = packet("NHL", draw=.1)
    with pytest.raises(ValueError, match="TIE_RULE_OR_DRAW_MASS_INVALID"):
        settle_seven_sport_shadow(sport="NHL", settlement_basis=settlement("NHL"), projection=p)
    p = packet("NHL", draw=0)
    p["moneyline_tie_rule"] = "THREE_WAY"
    with pytest.raises(ValueError, match="TIE_RULE_OR_DRAW_MASS_INVALID"):
        settle_seven_sport_shadow(sport="NHL", settlement_basis=settlement("NHL"), projection=p)


def test_soccer_requires_three_way_draw_and_cannot_use_push_rule():
    p = packet("SOCCER")
    p["moneyline_tie_rule"] = "PUSH"
    with pytest.raises(ValueError, match="TIE_RULE_OR_DRAW_MASS_INVALID"):
        settle_seven_sport_shadow(sport="SOCCER", settlement_basis=settlement("SOCCER"), projection=p)


def test_nfl_ncaaf_ties_cannot_disappear_via_no_ties_rule():
    for sport in ("NFL", "NCAAF"):
        p = packet(sport, draw=0)
        p["moneyline_tie_rule"] = "NO_TIES"
        with pytest.raises(ValueError, match="TIE_RULE_OR_DRAW_MASS_INVALID"):
            settle_seven_sport_shadow(sport=sport, settlement_basis=settlement(sport), projection=p)


def test_soccer_asian_quarter_handicap_is_held_without_joint_draw_replay():
    p = packet("SOCCER", home_spread=-.25, game_total=2.5)
    with pytest.raises(ValueError, match="ASIAN_QUARTER_REQUIRES_JOINT_DRAWS"):
        settle_seven_sport_shadow(sport="SOCCER", settlement_basis="REGULATION", projection=p)


def test_wrong_line_tick_and_half_point_push_cannot_be_faked():
    p = packet("NCAAB", home_spread=-3.1, draw=0)
    with pytest.raises(ValueError, match="EXACT_LINE_TICK_INVALID"):
        settle_seven_sport_shadow(sport="NCAAB", settlement_basis=settlement("NCAAB"), projection=p)
    p = packet("NCAAB", home_spread=-3.5, draw=0)
    p["home_spread"]["exact"] = .1
    p["home_spread"]["less"] = .6
    with pytest.raises(ValueError, match="HALF_POINT_PUSH_MASS_INVALID"):
        settle_seven_sport_shadow(sport="NCAAB", settlement_basis=settlement("NCAAB"), projection=p)


@pytest.mark.parametrize("mutation,expected", [
    (lambda p: p.update(probability_publishable=True), "UNAUTHORIZED_PROBABILITY_CLAIM"),
    (lambda p: p.update(can_execute=True), "EXECUTION_INVARIANT_VIOLATION"),
    (lambda p: p.update(draw_count=1), "DRAW_COUNT_INVALID"),
    (lambda p: p["moneyline"].update(home_win=float("nan")), "PROBABILITY_INVALID"),
    (lambda p: p["home_spread"].update(more=.8), "DISTRIBUTION_NOT_NORMALIZED"),
    (lambda p: p.update(event_id=""), "SOURCE_PROVENANCE_MISSING"),
    (lambda p: p.update(fitted_artifact_sha256="x"), "SOURCE_PROVENANCE_MISSING"),
    (lambda p: p.update(captured_at="2026-11-14T02:00:00+00:00"), "PREGAME_CAPTURE_INVALID"),
    (lambda p: p.update(event_start_at="2026-11-14T01:00:00"), "PREGAME_CAPTURE_INVALID"),
    (lambda p: p["game_total"].update(line=10**400), "EXACT_LINE_INVALID"),
])
def test_invalid_source_claims_or_numbers_fail_closed(mutation,expected):
    p = packet()
    mutation(p)
    with pytest.raises(ValueError, match=expected):
        settle_seven_sport_shadow(sport="NCAAF", settlement_basis=settlement("NCAAF"), projection=p)


def test_projection_is_immutable_and_cannot_grant_certification():
    p = packet("NCAAF")
    before = deepcopy(p)
    r = settle_seven_sport_shadow(sport="NCAAF", settlement_basis=settlement("NCAAF"), projection=p)
    assert p == before
    assert r["market_edge_established"] is False
    assert r["source_artifact_proven_certified"] is False
    assert r["controlling_engine_identity"] == "LLP_TEAM_BETTING_ENGINE"
    assert r["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_unsupported_undecidable_sport_and_untyped_tennis_inputs_hold():
    with pytest.raises(ValueError, match="SPORT_UNSUPPORTED"):
        settle_seven_sport_shadow(sport="CRICKET", settlement_basis="REGULATION", projection=packet())
    with pytest.raises(ValueError, match="TENNIS_SCORE_UNIT_INVALID"):
        settle_seven_sport_shadow(sport="TENNIS", settlement_basis=settlement("TENNIS"), projection=packet("TENNIS"))
