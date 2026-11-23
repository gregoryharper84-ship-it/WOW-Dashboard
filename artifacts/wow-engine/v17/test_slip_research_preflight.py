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
    data["official_fixture_evidence"] = dict(
        official_source_id="synthetic-official-schedule",
        manifest_receipt_id="synthetic-official-event-" + event,
        verified_at_utc="2026-11-19T23:30:00Z",
        event_start_utc=START,
        event_status="SCHEDULED",
        sport=sport, official_event_id=event,
        home_team_id="HOME", away_team_id="AWAY",
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
    data["prop_matchup_evidence"] = dict(
        sport="NBA", official_event_id=event, official_player_id=player,
        stat_type="POINTS", observed_at_utc="2026-11-19T23:42:00Z",
        source_receipt_id="synthetic-prop-context",
        opponent_efficiency_source_id="synthetic-defense",
        role_minutes_source_id="synthetic-minutes",
        usage_source_id="synthetic-usage",
        pace_game_script_source_id="synthetic-scripts",
        tail_scenario_source_id="synthetic-variance",
    )
    data["opportunity_evidence"] = dict(
        sport="NBA", official_event_id=event,
        official_player_id=player, stat_type="POINTS",
        source_receipt_id="synthetic-workload-" + row,
        lineup_scenario_source_id="synthetic-lineup-scenarios",
        role_source_id="synthetic-role-ledger",
        observed_at_utc="2026-11-19T23:55:00Z",
        availability="ACTIVE_CONFIRMED",
        forecast_method="ROLE_CONDITIONAL_MINUTES_ATTEMPTS",
        opportunity_unit="FGA",
        expected_opportunities=16.0,
        opportunity_p10=9.0,
        opportunity_p90=24.0,
    )
    return data


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
    player["news_evidence"]["new_model_receipt_id"] = "synthetic-refresh"
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


def test_wrong_date_or_already_final_official_fixture_holds_research_slip():
    # McDavid-style prior-day fixture must never be relabeled today's pregame event.
    old = leg("mc-david", "nhl-prior-game", sport="NHL")
    old["official_fixture_evidence"]["event_start_utc"] = "2026-11-19T02:00:00Z"
    result = audit_research_slips(
        cards([old, leg("valid", "nba-current")]), as_of_utc=AS_OF)
    assert "OFFICIAL_FIXTURE_IDENTITY_OR_CHRONOLOGY_UNVERIFIED" in result["cards"][0]["blockers"]
    current = leg("already-final", "nba-final")
    current["official_fixture_evidence"]["event_status"] = "FINAL"
    result = audit_research_slips(
        cards([current, leg("valid", "nba-other")]), as_of_utc=AS_OF)
    assert "OFFICIAL_FIXTURE_NOT_SCHEDULED" in result["cards"][0]["blockers"]
    assert result["joint_probability"] is None


def test_fixture_provenance_absent_or_wrong_team_is_not_inferred_from_bookmaker_quote():
    row = leg("one", "nba-one")
    del row["official_fixture_evidence"]
    result = audit_research_slips(cards([row, leg("two", "nba-two")]), as_of_utc=AS_OF)
    assert "OFFICIAL_FIXTURE_EVIDENCE_MISSING" in result["cards"][0]["blockers"]
    row = leg("one", "nba-one")
    row["official_fixture_evidence"]["away_team_id"] = "OTHER"
    result = audit_research_slips(cards([row, leg("two", "nba-two")]), as_of_utc=AS_OF)
    assert "OFFICIAL_FIXTURE_IDENTITY_OR_CHRONOLOGY_UNVERIFIED" in result["cards"][0]["blockers"]


def test_robertson_style_common_game_hinge_across_three_cards_is_held():
    # Different props on the same event are not exact duplicate theses, but
    # they still concentrate event/game-script risk across the whole session.
    one = prop("robertson", "stars-penguins", player="NHL-ROB")
    two = prop("johnston", "stars-penguins", player="NHL-JOH")
    three = prop("malkin", "stars-penguins", player="NHL-MAL")
    for row in (one, two, three):
        row["sport"] = "NHL"
        row["market_quote"]["sport"] = "NHL"
        row["player_roster_evidence"]["sport"] = "NHL"
        row["opportunity_evidence"]["sport"] = "NHL"
        row["prop_matchup_evidence"]["sport"] = "NHL"
        row["official_fixture_evidence"]["sport"] = "NHL"
        row["news_evidence"]["sport"] = "NHL"
    proposed = cards(
        [one, leg("other-a", "nba-a")],
        [two, leg("other-b", "nba-b")],
        [three, leg("other-c", "nba-c")],
    )
    snapshot = deepcopy(proposed)
    report = audit_research_slips(proposed, as_of_utc=AS_OF)
    assert proposed == snapshot
    assert report["cards"][0]["blockers"] == []
    assert report["cards"][1]["blockers"] == []
    assert "CROSS_SLIP_EVENT_HINGE_CONCENTRATION" in report["cards"][2]["blockers"]
    assert report["cross_slip_event_exposure_counts"]["NHL|stars-penguins"] == 3
    assert max(report["cross_slip_exact_exposure_counts"].values()) == 1
    assert report["can_execute"] is False


def test_claimed_price_edge_is_rejected_when_research_probability_below_breakeven():
    jax = leg("jacksonville", "jaguars-giants")
    jax["market_quote"]["american_odds"] = -266
    jax["value_claim"] = "STRONG_VALUE"
    jax["research_value_evidence"] = dict(
        source_id="synthetic-external-forecast",
        observed_at_utc="2026-11-19T23:45:00Z",
        sport="NBA", official_event_id="jaguars-giants",
        market_family="MONEYLINE", market_period="FULL_GAME",
        side="HOME", exact_line=None,
        settlement_basis="INCL_OT",
        estimated_selection_probability=0.70,
    )
    result = audit_research_slips(cards([jax, leg("second", "nba-second")]), as_of_utc=AS_OF)
    assert "RESEARCH_PRICE_EDGE_NOT_SUPPORTED" in result["cards"][0]["blockers"]
    jax["research_value_evidence"]["estimated_selection_probability"] = 0.80
    result = audit_research_slips(cards([jax, leg("second", "nba-second")]), as_of_utc=AS_OF)
    assert "RESEARCH_PRICE_EDGE_NOT_SUPPORTED" not in result["cards"][0]["blockers"]
    assert result["cards"][0]["can_publish_as_certified"] is False
    assert result["cards"][0]["joint_probability"] is None
    del jax["research_value_evidence"]
    result = audit_research_slips(cards([jax, leg("second", "nba-second")]), as_of_utc=AS_OF)
    assert "RESEARCH_VALUE_CLAIM_UNSUPPORTED" in result["cards"][0]["blockers"]


def test_gordon_style_under_requires_pace_role_usage_and_tail_context():
    gordon = prop("gordon-under", "nuggets-bulls", player="NBA-GORDON")
    del gordon["prop_matchup_evidence"]["pace_game_script_source_id"]
    result = audit_research_slips(cards([gordon, leg("other", "other")]), as_of_utc=AS_OF)
    assert "PROP_MATCHUP_CONTEXT_UNVERIFIED" in result["cards"][0]["blockers"]
    gordon = prop("gordon-under", "nuggets-bulls", player="NBA-GORDON")
    del gordon["prop_matchup_evidence"]
    result = audit_research_slips(cards([gordon, leg("other", "other")]), as_of_utc=AS_OF)
    assert "PROP_MATCHUP_CONTEXT_MISSING" in result["cards"][0]["blockers"]


def test_duplicate_card_id_cannot_launder_repeated_exposure_as_one_card():
    duplicated = cards([leg("a", "nba-x"), leg("b", "nba-y")],
                       [leg("c", "nba-x"), leg("d", "nba-z")])
    duplicated[1]["card_id"] = duplicated[0]["card_id"]
    result = audit_research_slips(duplicated, as_of_utc=AS_OF)
    assert "RESEARCH_CARD_ID_DUPLICATE" in result["cards"][1]["blockers"]
    assert result["can_execute"] is False


def test_research_value_cannot_launder_other_player_or_stat_into_price_edge():
    gordon = prop("gordon", "nuggets-bulls", player="NBA-GORDON")
    gordon["value_claim"] = "STRONG_VALUE"
    gordon["research_value_evidence"] = dict(
        source_id="synthetic-player-estimate",
        observed_at_utc="2026-11-19T23:45:00Z",
        sport="NBA", official_event_id="nuggets-bulls",
        market_family="PLAYER_PROP", market_period="FULL_GAME",
        side="UNDER", exact_line=24.5,
        settlement_basis="INCL_OT",
        official_player_id="NBA-JOKIC", stat_type="REBOUNDS",
        estimated_selection_probability=0.80,
    )
    unrelated = leg("other", "nba-other")
    result = audit_research_slips(cards([gordon, unrelated]), as_of_utc=AS_OF)
    assert "RESEARCH_VALUE_PLAYER_STAT_MISMATCH" in result["cards"][0]["blockers"]
    gordon["research_value_evidence"]["official_player_id"] = "NBA-GORDON"
    result = audit_research_slips(cards([gordon, unrelated]), as_of_utc=AS_OF)
    assert "RESEARCH_VALUE_PLAYER_STAT_MISMATCH" in result["cards"][0]["blockers"]
    gordon["research_value_evidence"]["stat_type"] = "POINTS"
    result = audit_research_slips(cards([gordon, unrelated]), as_of_utc=AS_OF)
    assert "RESEARCH_VALUE_PLAYER_STAT_MISMATCH" not in result["cards"][0]["blockers"]
    assert result["cards"][0]["can_publish_as_certified"] is False
    assert result["cards"][0]["joint_probability"] is None


def test_research_value_team_total_must_match_exact_selected_team():
    g = total("home-team-total", "nba-1", side="UNDER", line=115.5)
    g["market_family"] = "TEAM_TOTAL"
    g["selected_team_id"] = "HOME"
    g["market_quote"]["market_family"] = "TEAM_TOTAL"
    g["market_quote"]["selected_team_id"] = "HOME"
    g["total_evidence"]["selected_team_id"] = "HOME"
    g["total_evidence"]["fitted_total_distribution_receipt"]["selected_team_id"] = "HOME"
    g["value_claim"] = "STRONG_VALUE"
    g["research_value_evidence"] = dict(
        source_id="synthetic-team-estimate",
        observed_at_utc="2026-11-19T23:45:00Z",
        sport="NBA", official_event_id="nba-1",
        market_family="TEAM_TOTAL", market_period="FULL_GAME",
        side="UNDER", exact_line=115.5,
        settlement_basis="INCL_OT",
        selected_team_id="AWAY", estimated_selection_probability=0.80,
    )
    result = audit_research_slips(cards([g, leg("other", "nba-2")]), as_of_utc=AS_OF)
    assert "RESEARCH_VALUE_TEAM_MISMATCH" in result["cards"][0]["blockers"]
    g["research_value_evidence"]["selected_team_id"] = "HOME"
    result = audit_research_slips(cards([g, leg("other", "nba-2")]), as_of_utc=AS_OF)
    assert "RESEARCH_VALUE_TEAM_MISMATCH" not in result["cards"][0]["blockers"]
    assert result["cards"][0]["can_publish_as_certified"] is False


def test_research_value_settlement_basis_must_match_exact_over_time_rule():
    gordon = prop("gordon-under", "nuggets-bulls", player="NBA-GORDON")
    gordon["value_claim"] = "STRONG_VALUE"
    gordon["research_value_evidence"] = dict(
        source_id="synthetic-stat-prob",
        observed_at_utc="2026-11-19T23:45:00Z",
        sport="NBA", official_event_id="nuggets-bulls",
        market_family="PLAYER_PROP", market_period="FULL_GAME",
        side="UNDER", exact_line=24.5,
        settlement_basis="EXCL_OT",
        official_player_id="NBA-GORDON", stat_type="POINTS",
        estimated_selection_probability=0.80,
    )
    out = audit_research_slips(cards([gordon, leg("other", "nba-other")]), as_of_utc=AS_OF)
    assert "RESEARCH_VALUE_EVIDENCE_UNVERIFIED" in out["cards"][0]["blockers"]
    gordon["research_value_evidence"]["settlement_basis"] = "INCL_OT"
    out = audit_research_slips(cards([gordon, leg("other", "nba-other")]), as_of_utc=AS_OF)
    assert "RESEARCH_VALUE_EVIDENCE_UNVERIFIED" not in out["cards"][0]["blockers"]
    assert out["joint_probability"] is None
    assert out["cards"][0]["can_publish_as_certified"] is False

def prizepicks_prop(row, event, *, player="NBA-34", line=10.5, tier="STANDARD"):
    p = prop(row, event, player=player, line=line)
    p.update(platform="PRIZEPICKS", side="MORE", direction="MORE")
    p["market_quote"]["side"] = "MORE"
    p["platform_offer"] = dict(
        platform="PRIZEPICKS", source_receipt_id="synthetic-board-" + row,
        sport="NBA", official_event_id=event, official_player_id=player,
        stat_type="POINTS", direction="MORE", exact_line=line,
        projection_type=tier, status="ACTIVE",
        captured_at_utc="2026-11-19T23:57:00Z",
    )
    p["sporting_receipt_binding"] = dict(
        immutable_prediction_id="synthetic-prediction-" + row,
        sport="NBA", official_event_id=event, official_player_id=player,
        stat_type="POINTS", direction="MORE", exact_line=line,
        frozen_at_utc="2026-11-19T23:56:00Z",
    )
    return p


def prizepicks_card(*props):
    return dict(
        card_id="prizepicks-flex", platform="PRIZEPICKS", structure="FLEX",
        legs=list(props),
        platform_lineup_payout=dict(
            platform="PRIZEPICKS",
            source_receipt_id="synthetic-payout-board",
            structure="FLEX", leg_count=len(props),
            projection_types=[p["platform_offer"]["projection_type"] for p in props],
            ordered_offer_bindings=[
                {
                    "source_receipt_id": p["platform_offer"]["source_receipt_id"],
                    "sport": p["sport"],
                    "official_event_id": p["official_event_id"],
                    "official_player_id": p["official_player_id"],
                    "stat_type": p["stat_type"],
                    "direction": p["side"],
                    "exact_line": p["exact_line"],
                    "projection_type": p["platform_offer"]["projection_type"],
                }
                for p in props
            ],
            gross_payout_multiple=2.5,
            captured_at_utc="2026-11-19T23:58:00Z",
        ),
    )


def test_prizepicks_verified_shape_is_still_uncertified_and_not_executable():
    card = prizepicks_card(
        prizepicks_prop("turner", "mil-por", tier="GOBLIN"),
        prizepicks_prop("towns", "nyk-min", tier="STANDARD"),
    )
    frozen = deepcopy(card)
    result = audit_research_slips([card], as_of_utc=AS_OF)
    assert card == frozen
    assert result["cards"][0]["blockers"] == []
    assert result["cards"][0]["status"] == "RESEARCH_CONTEXT_REVIEWABLE_UNCERTIFIED"
    assert result["cards"][0]["can_publish_as_certified"] is False
    assert result["cards"][0]["executable_quote_verified"] is False
    assert result["can_execute"] is False


def test_nov19_line_change_requires_exact_rescore_even_when_underlying_quote_matches():
    castle = prizepicks_prop("castle", "sas-lac", line=7.0)
    # Original research used LESS 7.5. The actual entry was LESS 7.
    castle["sporting_receipt_binding"]["exact_line"] = 7.5
    castle["platform_offer"]["exact_line"] = 7.0
    castle["side"] = castle["direction"] = "LESS"
    castle["platform_offer"]["direction"] = "LESS"
    castle["market_quote"]["side"] = "LESS"
    castle["sporting_receipt_binding"]["direction"] = "LESS"
    result = audit_research_slips(
        [prizepicks_card(castle, prizepicks_prop("other", "por-mil"))],
        as_of_utc=AS_OF,
    )
    assert "PRIZEPICKS_EXACT_MODEL_RECEIPT_MISMATCH_RESCORING_REQUIRED" in result["cards"][0]["blockers"]
    assert result["joint_probability"] is None


def test_prizepicks_goblin_tier_and_lineup_payout_need_fresh_matching_receipts():
    a = prizepicks_prop("dunn", "lac-sas", tier="GOBLIN")
    b = prizepicks_prop("towns", "nyk-min")
    card = prizepicks_card(a, b)
    card["platform_lineup_payout"]["projection_types"] = ["STANDARD", "STANDARD"]
    result = audit_research_slips([card], as_of_utc=AS_OF)
    assert "PRIZEPICKS_LINEUP_PAYOUT_CONTEXT_MISMATCH" in result["cards"][0]["blockers"]
    card["platform_lineup_payout"]["projection_types"] = ["GOBLIN", "STANDARD"]
    a["platform_offer"]["captured_at_utc"] = "2026-11-19T23:00:00Z"
    result = audit_research_slips([card], as_of_utc=AS_OF)
    assert "PRIZEPICKS_OFFER_STALE_OR_NOT_PREGAME" in result["cards"][0]["blockers"]
    a.pop("platform_offer")
    result = audit_research_slips([card], as_of_utc=AS_OF)
    assert "PRIZEPICKS_EXACT_OFFER_MISSING" in result["cards"][0]["blockers"]


def test_nov19_turner_out_and_unverified_workload_cannot_anchor_slip():
    turner = prizepicks_prop("turner", "mil-por", tier="GOBLIN")
    other = prizepicks_prop("towns", "nyk-min")
    turner["opportunity_evidence"]["availability"] = "OUT"
    result = audit_research_slips([prizepicks_card(turner, other)], as_of_utc=AS_OF)
    assert "PROP_PLAYER_RULED_OUT" in result["cards"][0]["blockers"]
    turner["opportunity_evidence"]["availability"] = "QUESTIONABLE"
    turner["opportunity_evidence"]["forecast_method"] = "LAST_10_HIT_RATE"
    result = audit_research_slips([prizepicks_card(turner, other)], as_of_utc=AS_OF)
    assert "PROP_AVAILABILITY_NOT_CONFIRMED" in result["cards"][0]["blockers"]
    assert "PROP_WORKLOAD_DISTRIBUTION_UNVERIFIED" in result["cards"][0]["blockers"]
    turner.pop("opportunity_evidence")
    result = audit_research_slips([prizepicks_card(turner, other)], as_of_utc=AS_OF)
    assert "PROP_OPPORTUNITY_EVIDENCE_MISSING" in result["cards"][0]["blockers"]


def test_six_leg_requested_quota_is_held_without_certified_joint_probability():
    six = [prizepicks_prop(str(i), "game-" + str(i)) for i in range(6)]
    result = audit_research_slips([prizepicks_card(*six)], as_of_utc=AS_OF)
    assert "LONG_SLIP_JOINT_PROBABILITY_UNVERIFIED" in result["cards"][0]["blockers"]
    assert result["cards"][0]["joint_probability"] is None
    assert result["cards"][0]["can_publish_as_certified"] is False


def test_prizepicks_optimizer_cannot_skip_research_hold_by_default():
    pp = prizepicks_card(
        prizepicks_prop("dunn", "lac-sas", tier="GOBLIN"),
        prizepicks_prop("towns", "nyk-min"),
    )
    pp["platform_lineup_payout"]["projection_types"] = ["STANDARD", "STANDARD"]
    source = deepcopy(pp)
    out = optimize_portfolio([pp], research_as_of_utc=AS_OF)
    assert pp == source
    assert out.research_preflight is not None
    assert out.cards_qualified == 0
    assert "PRIZEPICKS_LINEUP_PAYOUT_CONTEXT_MISMATCH" in out.research_preflight["cards"][0]["blockers"]
    assert out.cards[0]["portfolio_governance"]["portfolio_status"] == "HELD_RESEARCH_ONLY"
    assert out.can_execute is False


def test_material_injury_news_requires_new_opportunity_scenario_not_only_model_id():
    castle = prizepicks_prop("castle", "sas-lac")
    castle["news_evidence"]["material_change"] = True
    castle["news_evidence"]["last_reviewed_at_utc"] = "2026-11-19T23:59:00Z"
    castle["news_evidence"]["new_model_receipt_id"] = "synthetic-rescore-id"
    review = audit_research_slips(
        [prizepicks_card(castle, prizepicks_prop("towns", "nyk-min"))],
        as_of_utc=AS_OF,
    )
    assert "PROP_OPPORTUNITY_RESCENARIO_REQUIRED" in review["cards"][0]["blockers"]
    assert review["cards"][0]["can_publish_as_certified"] is False
    assert review["joint_probability"] is None


def test_automatic_prizepicks_hold_does_not_demote_other_platform_cards():
    pp = prizepicks_card(
        prizepicks_prop("dunn", "lac-sas"),
        prizepicks_prop("towns", "nyk-min"),
    )
    non_pp = cards([leg("independent-a", "other-1"), leg("independent-b", "other-2")])[0]
    non_pp["card_id"] = "non-prizepicks"
    original = deepcopy([pp, non_pp])
    result = optimize_portfolio(original, research_as_of_utc=AS_OF)
    assert result.cards[0]["portfolio_governance"]["portfolio_status"] == "HELD_RESEARCH_ONLY"
    assert result.cards[1]["portfolio_governance"]["portfolio_qualified"] is True
    assert "research_only" not in result.cards[1]["portfolio_governance"]
    assert result.cards_qualified == 1
    assert result.cards_held == 1
    assert original == [pp, non_pp]
    assert result.probability_fields_mutated is False


def test_payout_binding_rejects_equal_tiers_but_different_lineup_and_order():
    original = prizepicks_card(
        prizepicks_prop("castle", "sas-lac"),
        prizepicks_prop("towns", "nyk-min", tier="GOBLIN"),
    )
    substituted = prizepicks_card(
        prizepicks_prop("different-a", "por-mil"),
        prizepicks_prop("different-b", "hou-ind", tier="GOBLIN"),
    )
    substituted["platform_lineup_payout"] = deepcopy(original["platform_lineup_payout"])
    r = audit_research_slips([substituted], as_of_utc=AS_OF)
    assert "PRIZEPICKS_LINEUP_PAYOUT_CONTEXT_MISMATCH" in r["cards"][0]["blockers"]
    reordered = prizepicks_card(
        prizepicks_prop("towns", "nyk-min", tier="GOBLIN"),
        prizepicks_prop("castle", "sas-lac"),
    )
    reordered["platform_lineup_payout"] = deepcopy(original["platform_lineup_payout"])
    r = audit_research_slips([reordered], as_of_utc=AS_OF)
    assert "PRIZEPICKS_LINEUP_PAYOUT_CONTEXT_MISMATCH" in r["cards"][0]["blockers"]


def test_prizepicks_malformed_leg_produces_typed_hold_not_exception():
    card = prizepicks_card(prizepicks_prop("dunn", "lac-sas"),
                           prizepicks_prop("towns", "nyk-min"))
    card["legs"][0] = None
    r = audit_research_slips([card], as_of_utc=AS_OF)
    assert "RESEARCH_LEG_INVALID" in r["cards"][0]["blockers"]
    assert "PRIZEPICKS_LINEUP_PAYOUT_CONTEXT_MISMATCH" in r["cards"][0]["blockers"]
    assert r["can_execute"] is False


def test_material_news_requires_matching_post_change_prediction_receipt():
    castle = prizepicks_prop("castle", "sas-lac")
    castle["news_evidence"].update(
        material_change=True,
        last_reviewed_at_utc="2026-11-19T23:58:00Z",
        new_model_receipt_id="synthetic-revision",
    )
    castle["opportunity_evidence"]["observed_at_utc"] = "2026-11-19T23:59:00Z"
    card = prizepicks_card(castle, prizepicks_prop("towns", "nyk-min"))
    r = audit_research_slips([card], as_of_utc=AS_OF)
    assert "PROP_OPPORTUNITY_RESCENARIO_REQUIRED" not in r["cards"][0]["blockers"]
    assert "PRIZEPICKS_MATERIAL_NEWS_PREDICTION_STALE" in r["cards"][0]["blockers"]
    castle["sporting_receipt_binding"]["frozen_at_utc"] = "2026-11-19T23:59:30Z"
    # Newer timestamp alone cannot bind a different prediction ID.
    r = audit_research_slips([card], as_of_utc=AS_OF)
    assert "PRIZEPICKS_MATERIAL_NEWS_PREDICTION_STALE" in r["cards"][0]["blockers"]
    castle["sporting_receipt_binding"]["immutable_prediction_id"] = "synthetic-revision"
    r = audit_research_slips([card], as_of_utc=AS_OF)
    assert "PRIZEPICKS_MATERIAL_NEWS_PREDICTION_STALE" not in r["cards"][0]["blockers"]
    assert r["cards"][0]["can_publish_as_certified"] is False


def test_prizepicks_platform_conflict_cannot_bypass_exact_offer_check():
    castle = prizepicks_prop("castle", "sas-lac")
    castle["platform"] = "DRAFTKINGS"
    castle["platform_offer"]["exact_line"] = 999.0
    castle.pop("sporting_receipt_binding")
    card = prizepicks_card(castle, prizepicks_prop("towns", "nyk-min"))
    r = audit_research_slips([card], as_of_utc=AS_OF)
    reasons = r["cards"][0]["blockers"]
    assert "SLIP_CARD_LEG_PLATFORM_CONFLICT" in reasons
    assert "PRIZEPICKS_EXACT_OFFER_IDENTITY_MISMATCH" in reasons
    assert "PRIZEPICKS_EXACT_MODEL_RECEIPT_MISSING" in reasons


def test_optimizer_generic_cards_with_prizepicks_leg_metadata_remain_unchanged():
    row1 = leg("generic-a", "g-a")
    row2 = leg("generic-b", "g-b")
    row1["platform"] = row2["platform"] = "PrizePicks"
    data = cards([row1, row2])
    r = optimize_portfolio(data)
    assert r.research_preflight is None
    assert r.cards[0]["portfolio_governance"]["portfolio_qualified"] is True
    assert "research_only" not in r.cards[0]["portfolio_governance"]
    assert r.can_execute is False


def test_lineup_refresh_invalidates_old_payout_quote_after_optimizer_replacement():
    a = prizepicks_prop("original-a", "nba-old")
    b = prizepicks_prop("original-b", "nba-second")
    card = prizepicks_card(a, b)
    card["legs"][0] = prizepicks_prop("replacement-a", "nba-new")
    r = audit_research_slips([card], as_of_utc=AS_OF)
    assert "PRIZEPICKS_LINEUP_PAYOUT_CONTEXT_MISMATCH" in r["cards"][0]["blockers"]

def test_nov22_tate_under_longest_reception_requires_explosive_tail_and_return_scenario():
    tate = prop("tate", "ten-dal", player="NFL-TATE", line=23.5)
    tate.update(sport="NFL", stat_type="LONGEST_RECEPTION_YARDS", side="UNDER", direction="UNDER")
    tate["market_quote"].update(sport="NFL", side="UNDER", stat_type="LONGEST_RECEPTION_YARDS")
    tate["player_roster_evidence"].update(sport="NFL", official_player_id="NFL-TATE")
    tate["prop_matchup_evidence"].update(sport="NFL", official_player_id="NFL-TATE",
                                         stat_type="LONGEST_RECEPTION_YARDS")
    tate["news_evidence"].update(sport="NFL", official_event_id="ten-dal",
                                  material_change=True,
                                  last_reviewed_at_utc="2026-11-19T23:48:00Z",
                                  new_model_receipt_id="synthetic-not-authenticated")
    tate["opportunity_evidence"].update(
        sport="NFL", stat_type="LONGEST_RECEPTION_YARDS",
        official_player_id="NFL-TATE", opportunity_unit="TARGETS",
        observed_at_utc="2026-11-19T23:55:00Z",
    )
    tate["platform"] = "NONE"
    card = {"card_id": "tate-provisional", "structure": "FLEX",
            "legs": [tate, leg("other", "other-game")]}
    first = audit_research_slips([card], as_of_utc=AS_OF)
    assert "LONGEST_RECEPTION_TAIL_SCENARIOS_UNVERIFIED" in first["cards"][0]["blockers"]
    assert first["can_execute"] is False
    tate["longest_reception_tail_evidence"] = dict(
        sport="NFL", official_event_id="ten-dal", official_player_id="NFL-TATE",
        stat_type="LONGEST_RECEPTION_YARDS", side="UNDER", exact_line=23.5,
        observed_at_utc="2026-11-19T23:45:00Z",
        route_depth_distribution_source_id="fixture-route-depth",
        explosive_target_source_id="fixture-target-tails",
        yards_after_catch_distribution_source_id="fixture-yac-tails",
        opponent_coverage_source_id="fixture-coverage",
        single_play_tail_scenario_source_id="fixture-single-play",
        source_receipt_id="fixture-snapshot",
        return_from_injury=True,
    )
    second = audit_research_slips([card], as_of_utc=AS_OF)
    assert "LONGEST_RECEPTION_RETURN_TO_PLAY_SCENARIO_UNVERIFIED" in second["cards"][0]["blockers"]
    assert "LONGEST_RECEPTION_CONTEXT_STALE_AFTER_MATERIAL_NEWS" in second["cards"][0]["blockers"]
    tate["longest_reception_tail_evidence"].update(
        observed_at_utc="2026-11-19T23:57:00Z",
        return_to_play_workload_scenario_id="fixture-active-return-workload",
    )
    third = audit_research_slips([card], as_of_utc=AS_OF)
    assert not any(x.startswith("LONGEST_RECEPTION_") for x in third["cards"][0]["blockers"])
    assert third["cards"][0]["can_publish_as_certified"] is False
    assert third["joint_probability"] is None
