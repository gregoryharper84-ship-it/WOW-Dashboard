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
        projected_possessions=101.0,
        home_adjusted_ppp=1.11, away_adjusted_ppp=1.10,
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
    data["player_roster_evidence"] = dict(
        sport="NBA", official_event_id=event,
        official_player_id=player, player_team_id=team,
        official_source_id="synthetic-official-roster",
        observed_at_utc="2026-11-19T23:45:00Z",
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
