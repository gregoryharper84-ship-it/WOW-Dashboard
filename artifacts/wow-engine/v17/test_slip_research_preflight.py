"""Synthetic pregame slip gate tests; no performance or model certification."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from v17.slip_research_preflight import audit_research_slips, exact_key
from v17.slip_portfolio_optimizer import optimize_portfolio

AS_OF = "2026-11-20T00:00:00Z"
START = "2026-11-20T02:00:00Z"


def leg(row, event, *, sport="NBA", market="MONEYLINE", side="HOME", line=None):
    data = dict(
        row_id=row, sport=sport, official_event_id=event, event_id=event,
        home_team_id="HOME", away_team_id="AWAY",
        market_family=market, market_period="FULL_GAME", side=side,
        exact_line=line, settlement_basis="INCL_OT",
        event_start_utc=START, source_snapshot_at_utc="2026-11-19T23:40:00Z",
        source_snapshot_receipt_id="synthetic-snapshot-" + event,
        max_quote_age_minutes=15, participant=row, direction=side,
        model_probability=.76, calibrated_probability=.75,
        calibrated_lower_bound=.60,
    )
    data["market_quote"] = dict(
        quote_source_id="synthetic-quote-" + row, bookmaker="FixtureBook",
        quoted_at_utc="2026-11-19T23:54:00Z", american_odds=-115,
        sport=sport, official_event_id=event, market_family=market,
        market_period="FULL_GAME", side=side, exact_line=line,
        settlement_basis="INCL_OT",
    )
    data["news_evidence"] = dict(
        sport=sport, official_event_id=event,
        last_reviewed_at_utc="2026-11-19T23:48:00Z",
        independent_review_receipt_id="fixture-review-" + row,
        official_source_id="fixture-source-" + row,
        material_change=False,
    )
    return data


def total(row, event, side="OVER", line=218.5):
    data = leg(row, event, market="GAME_TOTAL", side=side, line=line)
    data["total_evidence"] = dict(
        sport="NBA", official_event_id=event,
        source_receipt_id="synthetic-totals-context-" + row,
        context_observed_at_utc="2026-11-19T23:40:00Z",
        opponent_efficiency_source_id="synthetic-matchup",
        lineup_scenario_source_id="synthetic-lineups",
        rest_travel_source_id="synthetic-rest",
        tail_scenario_source_id="synthetic-tails",
        projected_possessions=100.0,
        home_adjusted_ppp=1.10, away_adjusted_ppp=1.10,
        fitted_total_distribution_receipt=dict(
            sport="NBA", official_event_id=event, side=side,
            exact_line=line,
            frozen_at_utc="2026-11-19T23:45:00Z",
            immutable_pregame_receipt_id="synthetic-total-forecast",
            fitted_model_hash="synthetic-fitted-hash",
            calibration_receipt_id="synthetic-calibrator",
        ),
    )
    return data


def prop(row, event, *, player="NUGGETS-15", team="HOME", line=24.5):
    data = leg(row, event, market="PLAYER_PROP", side="UNDER", line=line)
    data["official_player_id"] = player
    data["stat_type"] = "POINTS"
    data["market_quote"]["official_player_id"] = player
    data["market_quote"]["stat_type"] = "POINTS"
    data["player_roster_evidence"] = dict(
        sport="NBA", official_event_id=event,
        official_player_id=player, player_team_id=team,
        official_source_id="synthetic-official-roster",
        observed_at_utc="2026-11-19T23:45:00Z",
    )
    return data


def post_news_receipt(candidate):
    """A complete *claimed* post-news receipt; never independently authenticated."""
    news = candidate["news_evidence"]
    return dict(
        immutable_pregame_receipt_id=news["new_model_receipt_id"],
        fitted_model_hash="synthetic-fitted-model",
        calibration_receipt_id="synthetic-calibration",
        consumed_news_review_receipt_id=news["independent_review_receipt_id"],
        frozen_at_utc="2026-11-19T23:55:00Z",
        sport=candidate["sport"], official_event_id=candidate["official_event_id"],
        market_family=candidate["market_family"],
        market_period=candidate["market_period"], side=candidate["side"],
        settlement_basis=candidate["settlement_basis"],
        exact_line=candidate["exact_line"],
        official_player_id=candidate.get("official_player_id"),
        stat_type=candidate.get("stat_type"),
        selected_team_id=candidate.get("selected_team_id"),
    )


def cards(*rows):
    return [{"card_id": f"slip-{i}", "structure": "FLEX", "legs": list(legs)}
            for i, legs in enumerate(rows)]


def test_two_distinct_moneylines_are_only_research_reviewable_never_certified():
    original = cards([leg("heat", "nba-heat"), leg("kansas", "ncaab-kansas", sport="NCAAB")])
    frozen = deepcopy(original)
    out = audit_research_slips(original, as_of_utc=AS_OF)
    assert original == frozen
    assert out["status"] == "RESEARCH_CONTEXT_REVIEWABLE_UNCERTIFIED"
    assert out["cards"][0]["blockers"] == []
    assert out["joint_probability"] is None
    assert out["certified_sporting_probability"] is None
    assert not out["cards"][0]["executable_quote_verified"]
    assert out["can_execute"] is False


def test_repeated_uconn_spread_across_two_slips_is_held_once_not_double_counted():
    uconn = leg("uconn-a", "arizona-uconn", sport="NCAAB",
                market="POINT_SPREAD", side="AWAY", line=3.5)
    second = deepcopy(uconn)
    second["row_id"] = "uconn-b"
    result = audit_research_slips(cards(
        [uconn, leg("mavs", "nba-mavs", market="POINT_SPREAD", line=-4.5)],
        [second, leg("phoenix", "nba-phoenix")]), as_of_utc=AS_OF)
    assert result["cards"][0]["blockers"] == []
    assert "CROSS_SLIP_DUPLICATE_THESIS_EXPOSURE" in result["cards"][1]["blockers"]
    assert max(result["cross_slip_exact_exposure_counts"].values()) == 2


def test_same_raw_event_id_in_two_sports_is_not_false_duplicate():
    a = leg("nba-a", "common-id", sport="NBA")
    b = leg("ncaab-a", "common-id", sport="NCAAB")
    assert exact_key(a) != exact_key(b)
    out = audit_research_slips(cards([a, leg("c", "c")],
                                     [b, leg("d", "d")]), as_of_utc=AS_OF)
    assert not any("DUPLICATE" in x for c in out["cards"] for x in c["blockers"])


def test_totals_require_opponent_pace_lineups_rest_and_fitted_distribution_receipt():
    philly = total("philly", "phi-orl", side="UNDER", line=222.5)
    philly.pop("total_evidence")
    out = audit_research_slips(cards([philly, leg("heat", "heat")]), as_of_utc=AS_OF)
    assert "TOTAL_CONTEXT_MISSING" in out["cards"][0]["blockers"]
    assert "TOTAL_DISTRIBUTION_UNVERIFIED" in out["cards"][0]["blockers"]

    philly = total("philly", "phi-orl", side="UNDER", line=222.5)
    del philly["total_evidence"]["opponent_efficiency_source_id"]
    result = audit_research_slips(cards([philly, leg("heat", "heat")]), as_of_utc=AS_OF)
    assert "TOTAL_CONTEXT_MISSING" in result["cards"][0]["blockers"]
    philly = total("philly", "phi-orl", side="UNDER", line=222.5)
    philly["total_evidence"]["fitted_total_distribution_receipt"].pop("calibration_receipt_id")
    result = audit_research_slips(cards([philly, leg("heat", "heat")]), as_of_utc=AS_OF)
    assert "TOTAL_DISTRIBUTION_UNVERIFIED" in result["cards"][0]["blockers"]

    philly = total("philly", "phi-orl", side="UNDER", line=222.5)
    result = audit_research_slips(cards([philly, leg("heat", "heat")]), as_of_utc=AS_OF)
    assert result["cards"][0]["blockers"] == []
    assert result["cards"][0]["joint_probability_certified"] is False


def test_totals_direction_sanity_rejects_mean_opposite_to_selected_side():
    x = total("oakland", "oakland-asu", side="OVER", line=148.5)
    # Supplied pace and adjusted-PPP suggest 220, so OVER 148.5 is
    # direction-consistent (not certified or an authenticated fitted model).
    out = audit_research_slips(cards([x, leg("other", "other")]), as_of_utc=AS_OF)
    assert "TOTAL_POINT_ESTIMATE_CONTRADICTS_SIDE" not in out["cards"][0]["blockers"]
    x["exact_line"] = 232.5
    x["market_quote"]["exact_line"] = 232.5
    x["total_evidence"]["fitted_total_distribution_receipt"]["exact_line"] = 232.5
    out = audit_research_slips(cards([x, leg("other", "other")]), as_of_utc=AS_OF)
    assert "TOTAL_POINT_ESTIMATE_CONTRADICTS_SIDE" in out["cards"][0]["blockers"]
    assert out["cards"][0]["joint_probability"] is None


def test_nhl_total_uses_goals_process_not_basketball_possessions():
    nhl = leg("nhl-under", "nhl-1", sport="NHL",
              market="GAME_TOTAL", side="UNDER", line=6.5)
    nhl["total_evidence"] = dict(
        sport="NHL", official_event_id="nhl-1",
        context_observed_at_utc="2026-11-19T23:40:00Z",
        source_receipt_id="synthetic-goals-context",
        opponent_efficiency_source_id="nhl-defense",
        lineup_scenario_source_id="nhl-goalie",
        rest_travel_source_id="nhl-rest",
        tail_scenario_source_id="nhl-goal-tails",
        sport_score_process_source_id="nhl-fitted-goals-process",
        projected_total_score=5.6,
        fitted_total_distribution_receipt=dict(
            sport="NHL", official_event_id="nhl-1",
            side="UNDER", exact_line=6.5,
            frozen_at_utc="2026-11-19T23:45:00Z",
            immutable_pregame_receipt_id="synthetic-nhl-total",
            fitted_model_hash="synthetic-nhl-fit",
            calibration_receipt_id="synthetic-nhl-calibration",
        ),
    )
    out = audit_research_slips(cards([nhl, leg("other", "nba-2")]), as_of_utc=AS_OF)
    assert out["cards"][0]["blockers"] == []
    assert out["cards"][0]["joint_probability"] is None
    del nhl["total_evidence"]["sport_score_process_source_id"]
    out = audit_research_slips(cards([nhl, leg("other", "nba-2")]), as_of_utc=AS_OF)
    assert "TOTAL_CONTEXT_MISSING" in out["cards"][0]["blockers"]


def test_exact_quote_and_pregame_time_cannot_be_replaced_by_narrative():
    x = leg("a", "nba-a")
    x["market_quote"]["quoted_at_utc"] = "2026-11-19T23:10:00Z"
    result = audit_research_slips(cards([x, leg("b", "nba-b")]), as_of_utc=AS_OF)
    assert "MARKET_QUOTE_STALE" in result["cards"][0]["blockers"]
    x = leg("a", "nba-a")
    x["market_quote"]["exact_line"] = 1.5
    result = audit_research_slips(cards([x, leg("b", "nba-b")]), as_of_utc=AS_OF)
    assert "EXACT_MARKET_QUOTE_LINE_MISMATCH" in result["cards"][0]["blockers"]
    x = leg("a", "nba-a")
    x["source_snapshot_at_utc"] = START
    result = audit_research_slips(cards([x, leg("b", "nba-b")]), as_of_utc=AS_OF)
    assert "PREGAME_CHRONOLOGY_UNVERIFIED" in result["cards"][0]["blockers"]
    x = leg("a", "nba-a")
    x["market_quote"]["american_odds"] = False
    result = audit_research_slips(cards([x, leg("b", "nba-b")]), as_of_utc=AS_OF)
    assert "MARKET_QUOTE_PRICE_UNVERIFIED" in result["cards"][0]["blockers"]


def test_prop_quote_is_bound_to_player_and_stat_not_merely_event_and_line():
    p = prop("prop-one", "nba-one", player="NBA-34")
    p["market_quote"]["official_player_id"] = "NBA-99"
    result = audit_research_slips(cards([p, leg("other", "nba-other")]), as_of_utc=AS_OF)
    assert "PROP_QUOTE_PLAYER_STAT_MISMATCH" in result["cards"][0]["blockers"]
    p = prop("prop-one", "nba-one", player="NBA-34")
    p["market_quote"]["stat_type"] = "REBOUNDS"
    result = audit_research_slips(cards([p, leg("other", "nba-other")]), as_of_utc=AS_OF)
    assert "PROP_QUOTE_PLAYER_STAT_MISMATCH" in result["cards"][0]["blockers"]


def test_alias_reuse_is_blocked_across_ml_and_spread_label_variants():
    a = leg("a", "same-event", market="MONEYLINE")
    b = leg("b", "same-event", market="ML")
    x = leg("c", "spread-game", market="POINT_SPREAD", line=-4.5)
    y = leg("d", "spread-game", market="SPREAD", line=-4.5)
    assert exact_key(a) == exact_key(b)
    assert exact_key(x) == exact_key(y)
    outcome = audit_research_slips(
        cards([a, x], [b, y]), as_of_utc=AS_OF)
    assert outcome["cards"][0]["blockers"] == []
    assert "CROSS_SLIP_DUPLICATE_THESIS_EXPOSURE" in outcome["cards"][1]["blockers"]


def test_player_market_alias_does_not_hide_duplicate_exact_stat():
    a = prop("player-a", "nba-1", player="NBA-11")
    b = deepcopy(a)
    b["row_id"] = "player-b"
    b["market_family"] = "PLAYER_POINTS"
    b["market_quote"]["market_family"] = "PLAYER_POINTS"
    assert exact_key(a) == exact_key(b)
    result = audit_research_slips(cards(
        [a, leg("other", "nba-other")],
        [b, leg("third", "nba-third")]), as_of_utc=AS_OF)
    assert "CROSS_SLIP_DUPLICATE_THESIS_EXPOSURE" in result["cards"][1]["blockers"]


def test_team_total_estimates_selected_team_not_both_combined():
    t = total("team-total", "nba-team", side="UNDER", line=115.5)
    t["market_family"] = "TEAM_TOTAL"
    t["selected_team_id"] = "HOME"
    t["market_quote"]["market_family"] = "TEAM_TOTAL"
    t["market_quote"]["selected_team_id"] = "HOME"
    t["total_evidence"]["selected_team_id"] = "HOME"
    t["total_evidence"]["fitted_total_distribution_receipt"]["selected_team_id"] = "HOME"
    # Synthetic home expected score is 100 possessions x 1.10 PPP = 110,
    # not 220 both teams combined.
    result = audit_research_slips(cards([t, leg("b", "nba-other")]), as_of_utc=AS_OF)
    assert result["cards"][0]["blockers"] == []
    t["exact_line"] = 105.5
    t["market_quote"]["exact_line"] = 105.5
    t["total_evidence"]["fitted_total_distribution_receipt"]["exact_line"] = 105.5
    result = audit_research_slips(cards([t, leg("b", "nba-other")]), as_of_utc=AS_OF)
    assert "TOTAL_POINT_ESTIMATE_CONTRADICTS_SIDE" in result["cards"][0]["blockers"]
    t["market_quote"]["selected_team_id"] = "AWAY"
    result = audit_research_slips(cards([t, leg("b", "nba-other")]), as_of_utc=AS_OF)
    assert "TEAM_TOTAL_QUOTE_TEAM_MISMATCH" in result["cards"][0]["blockers"]


def test_player_team_transfer_injury_news_and_material_rescore_hold():
    player = prop("giannis", "mia-mil", player="NBA-34", team="OTHER")
    out = audit_research_slips(cards([player, leg("other", "other")]), as_of_utc=AS_OF)
    assert "PROP_ROSTER_IDENTITY_OR_FRESHNESS_UNVERIFIED" in out["cards"][0]["blockers"]
    player = prop("giannis", "mia-mil", player="NBA-34")
    player["news_evidence"]["material_change"] = True
    out = audit_research_slips(cards([player, leg("other", "other")]), as_of_utc=AS_OF)
    assert "MATERIAL_NEWS_RESCORE_NOT_VERIFIED" in out["cards"][0]["blockers"]
    # An arbitrary text receipt ID must not clear a material-news hold.
    player["news_evidence"]["new_model_receipt_id"] = "synthetic-refresh"
    out = audit_research_slips(cards([player, leg("other", "other")]), as_of_utc=AS_OF)
    assert "MATERIAL_NEWS_RESCORE_NOT_VERIFIED" in out["cards"][0]["blockers"]
    player["news_evidence"]["new_model_receipt"] = post_news_receipt(player)
    out = audit_research_slips(cards([player, leg("other", "other")]), as_of_utc=AS_OF)
    assert out["cards"][0]["status"] == "RESEARCH_CONTEXT_REVIEWABLE_UNCERTIFIED"
    assert not out["cards"][0]["model_rescore_verified"]


def test_five_legs_and_same_event_dependency_cannot_be_called_high_confidence():
    sample = [leg(f"x-{i}", f"nba-{i}") for i in range(5)]
    out = audit_research_slips(cards(sample), as_of_utc=AS_OF)
    assert "LONG_SLIP_JOINT_PROBABILITY_UNVERIFIED" in out["cards"][0]["blockers"]
    assert out["cards"][0]["joint_probability"] is None
    same = cards([leg("a", "nba-a"), total("t", "nba-a")])
    out = audit_research_slips(same, as_of_utc=AS_OF)
    assert "SAME_EVENT_JOINT_DEPENDENCE_UNVERIFIED" in out["cards"][0]["blockers"]


def test_optimizer_research_mode_holds_publication_but_leaves_existing_probabilities_unchanged():
    proposals = cards([leg("a", "nba-a"), leg("b", "nba-b")])
    original = deepcopy(proposals)
    out = optimize_portfolio(proposals, research_preflight_enabled=True,
                             research_as_of_utc=AS_OF)
    assert proposals == original
    assert out.research_preflight is not None
    assert out.research_preflight["cards"][0]["status"] == "RESEARCH_CONTEXT_REVIEWABLE_UNCERTIFIED"
    assert out.probability_fields_mutated is False
    assert out.cards_qualified == 0 and out.cards_held == 1
    governance = out.cards[0]["portfolio_governance"]
    assert governance["portfolio_qualified"] is False
    assert governance["portfolio_status"] == "HELD_RESEARCH_ONLY"
    assert governance["research_joint_probability"] is None
    assert out.can_execute is False


def test_replacement_missing_quote_is_reaudited_on_returned_card():
    weak = leg("weak", "nba-one")
    weak["calibrated_lower_bound"] = 0.60
    second = leg("second", "nba-two")
    second["calibrated_lower_bound"] = 0.76
    alternative = leg("alt", "nba-three")
    alternative["calibrated_lower_bound"] = 0.86
    del alternative["market_quote"]
    original = [{"card_id": "power", "structure": "POWER",
                 "power_admission_lower_bound_floor": 0.70,
                 "legs": [weak, second]}]
    out = optimize_portfolio(original, alternatives=[alternative],
                             research_preflight_enabled=True, research_as_of_utc=AS_OF,
                             max_power_critical_failure_share=1.0)
    assert any(r.get("replacement_row_id") == "alt" for r in out.replacements)
    assert "EXACT_MARKET_QUOTE_MISSING" in out.research_preflight["cards"][0]["blockers"]
    assert out.research_proposal_preflight["cards"][0]["blockers"] == []
    gov = out.cards[0]["portfolio_governance"]
    assert "EXACT_MARKET_QUOTE_MISSING" in gov["research_blockers"]
    assert gov["original_research_blockers"] == []
    assert gov["portfolio_qualified"] is False
    assert out.probability_fields_mutated is False


def test_old_portfolio_path_remains_unchanged_when_new_mode_not_requested():
    original = cards([leg("a", "nba-a"), leg("b", "nba-b")])
    out = optimize_portfolio(original)
    assert out.research_preflight is None
    assert "research_only" not in out.cards[0]["portfolio_governance"]
    assert out.can_execute is False


def test_invalid_asof_and_missing_card_handled_explicitly():
    with pytest.raises(ValueError, match="RESEARCH_PREFLIGHT_AS_OF_INVALID"):
        audit_research_slips(cards([leg("a", "a"), leg("b", "b")]), as_of_utc="2026-11-20T00:00:00")
    out = audit_research_slips([{"card_id": "bad", "legs": [None]}], as_of_utc=AS_OF)
    assert "RESEARCH_LEG_INVALID" in out["cards"][0]["blockers"]
    assert out["can_execute"] is False


SPORTS_13 = (
    "NFL", "NBA", "NHL", "NCAAF", "MLB", "WNBA", "NCAAB",
    "SOCCER", "TENNIS", "GOLF", "MMA", "BOXING", "CRICKET",
)


@pytest.mark.parametrize("sport", SPORTS_13)
@pytest.mark.parametrize("market,side,line", [
    ("MONEYLINE", "HOME", None),
    ("POINT_SPREAD", "AWAY", -3.5),
    ("GAME_TOTAL", "UNDER", 218.5),
    ("TEAM_TOTAL", "OVER", 110.5),
    ("PLAYER_PROP", "UNDER", 8.5),
])
def test_all_sports_all_markets_hold_old_receipt_after_material_change(sport, market, side, line):
    candidate = leg("person", "evt-1", sport=sport, market=market, side=side, line=line)
    candidate["news_evidence"]["material_change"] = True
    candidate["news_evidence"]["new_model_receipt_id"] = "new-receipt"
    # Regression: a bare ID is not proof of post-change specialist consumption.
    out = audit_research_slips(cards([candidate, leg("other", "evt-2")]), as_of_utc=AS_OF)
    assert "MATERIAL_NEWS_RESCORE_NOT_VERIFIED" in out["cards"][0]["blockers"]
    assert out["cards"][0]["can_publish_as_certified"] is False
    assert out["cards"][0]["joint_probability"] is None


@pytest.mark.parametrize("mutation", [
    "stale", "after-start", "wrong-event", "wrong-market", "wrong-side",
    "wrong-line", "wrong-review", "wrong-stat", "wrong-player",
])
def test_post_change_scorer_claim_must_match_exact_market_and_news(mutation):
    p = prop("hart", "nba-nyk", player="NBA-HART", line=8.5)
    p["stat_type"] = "REBOUNDS"
    p["market_quote"]["stat_type"] = "REBOUNDS"
    p["news_evidence"]["material_change"] = True
    p["news_evidence"]["new_model_receipt_id"] = "new-receipt"
    receipt = post_news_receipt(p)
    if mutation == "stale":
        receipt["frozen_at_utc"] = "2026-11-19T23:00:00Z"
    elif mutation == "after-start":
        receipt["frozen_at_utc"] = "2026-11-20T02:01:00Z"
    elif mutation == "wrong-event":
        receipt["official_event_id"] = "other"
    elif mutation == "wrong-market":
        receipt["market_family"] = "MONEYLINE"
    elif mutation == "wrong-side":
        receipt["side"] = "OVER"
    elif mutation == "wrong-line":
        receipt["exact_line"] = 9.5
    elif mutation == "wrong-review":
        receipt["consumed_news_review_receipt_id"] = "old-news"
    elif mutation == "wrong-stat":
        receipt["stat_type"] = "POINTS"
    elif mutation == "wrong-player":
        receipt["official_player_id"] = "NBA-KAT"
    p["news_evidence"]["new_model_receipt"] = receipt
    out = audit_research_slips(cards([p, leg("other", "nba-other")]), as_of_utc=AS_OF)
    assert "MATERIAL_NEWS_RESCORE_NOT_VERIFIED" in out["cards"][0]["blockers"]


def test_scout_context_invalidation_holds_a_card_even_if_news_field_claims_no_change():
    x = leg("hart", "nba-nyk", market="POINT_SPREAD", side="HOME", line=-3.5)
    x["news_evidence"]["material_change"] = False
    x["context_change_impact"] = {
        "impact_status": "CONTEXT_STALE_RECALC_REQUIRED",
        "research_invalidation_requested": True,
        "affected_market_families": ["SPREAD_HANDICAP", "PARTICIPANT_PROP_UNDER"],
    }
    out = audit_research_slips(cards([x, leg("other", "other")]), as_of_utc=AS_OF)
    assert "CONTEXT_STALE_RECALC_REQUIRED" in out["cards"][0]["blockers"]
    assert out["cards"][0]["can_publish_as_certified"] is False


def test_research_completeness_never_authenticates_post_news_fitted_consumption():
    x = prop("hart", "nba-nyk", player="NBA-HART", line=8.5)
    x["news_evidence"]["material_change"] = True
    x["news_evidence"]["new_model_receipt_id"] = "new-receipt"
    x["news_evidence"]["new_model_receipt"] = post_news_receipt(x)
    out = audit_research_slips(cards([x, leg("other", "other")]), as_of_utc=AS_OF)
    assert out["cards"][0]["blockers"] == []
    assert out["cards"][0]["status"] == "RESEARCH_CONTEXT_REVIEWABLE_UNCERTIFIED"
    assert out["cards"][0]["can_publish_as_certified"] is False
    assert out["cards"][0]["model_rescore_verified"] is False
    assert out["joint_probability"] is None


@pytest.mark.parametrize("market,side,line", [
    ("MONEYLINE", "HOME", None),
    ("POINT_SPREAD", "AWAY", -4.5),
    ("GAME_TOTAL", "UNDER", 215.5),
    ("TEAM_TOTAL", "OVER", 110.5),
    ("PLAYER_PROP", "UNDER", 8.5),
])
def test_explicit_peer_hold_survives_later_no_news_assessment(market, side, line):
    held = leg("hart", "nba-nyk", market=market, side=side, line=line)
    held["research_recommendation_status"] = "CONTEXT_STALE_RECALC_REQUIRED"
    held["context_change_impact"] = {
        "impact_status": "NO_MATERIAL_EVENT_RECEIPT",
        "research_invalidation_requested": False,
    }
    report = audit_research_slips(
        cards([held, leg("other", "nba-other")]), as_of_utc=AS_OF
    )
    assert "CONTEXT_STALE_RECALC_REQUIRED" in report["cards"][0]["blockers"]
    assert report["cards"][0]["can_publish_as_certified"] is False
    assert report["joint_probability"] is None


def test_impact_status_hold_survives_recommendation_reset():
    held = leg("hart", "nba-nyk")
    held["research_recommendation_status"] = "NO_MATERIAL_EVENT_RECEIPT"
    held["context_change_impact"] = {
        "impact_status": "CONTEXT_UNRESOLVED_REVIEW_REQUIRED",
        "research_invalidation_requested": False,
    }
    report = audit_research_slips(
        cards([held, leg("other", "nba-other")]), as_of_utc=AS_OF
    )
    assert "CONTEXT_STALE_RECALC_REQUIRED" in report["cards"][0]["blockers"]
    assert report["cards"][0]["can_publish_as_certified"] is False


def test_context_hold_remains_when_receipt_claim_is_supplied():
    held = prop("hart", "nba-nyk", player="NBA-HART", line=8.5)
    held["research_recommendation_status"] = "CONTEXT_STALE_RECALC_REQUIRED"
    held["context_change_impact"] = {
        "impact_status": "NO_MATERIAL_EVENT_RECEIPT",
        "research_invalidation_requested": False,
    }
    held["news_evidence"]["new_model_receipt_id"] = "new-receipt"
    held["news_evidence"]["new_model_receipt"] = post_news_receipt(held)
    report = audit_research_slips(
        cards([held, leg("other", "nba-other")]), as_of_utc=AS_OF
    )
    assert "CONTEXT_STALE_RECALC_REQUIRED" in report["cards"][0]["blockers"]
    assert report["cards"][0]["can_publish_as_certified"] is False
