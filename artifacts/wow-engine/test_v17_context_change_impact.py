"""P0 regression matrix: news-context invalidation is position- and market-wide.

These tests cover the research handoff only. Terminal rejection and fresh fitted
rescore require their own downstream acceptance and must not be inferred here.
"""
from datetime import datetime, timezone

import pytest

from v17.context_change_impact import (
    ALL_MARKET_FAMILIES,
    ALL_POSITION_SCOPE,
    assess_context_change_impact,
    hold_scored_after_material_change,
)
from v17.sport_scout_enrichment import enrich_candidate

NOW = datetime(2026, 11, 19, 17, 5, tzinfo=timezone.utc)
SPORTS_13 = (
    "americanfootball_nfl", "basketball_nba", "icehockey_nhl",
    "americanfootball_ncaaf", "baseball_mlb", "basketball_wnba",
    "basketball_ncaab", "soccer_epl", "tennis_atp", "golf_pga",
    "mma_ufc", "boxing", "cricket_ipl",
)
EXPECTED_MARKETS = {
    "MONEYLINE_WINNER", "SPREAD_HANDICAP", "GAME_TOTAL_OVER",
    "GAME_TOTAL_UNDER", "TEAM_TOTAL_OVER", "TEAM_TOTAL_UNDER",
    "PARTICIPANT_PROP_OVER", "PARTICIPANT_PROP_UNDER",
    "PARTICIPANT_PROP_OTHER", "UPSET_INTELLIGENCE",
    "MULTI_LEG_CORRELATION_RESEARCH",
}


def event(sport="basketball_nba"):
    return {
        "sport_key": sport, "official_event_id": "evt-10",
        "commence_time": "2026-11-19T23:00:00Z",
        "home_team": "Home", "away_team": "Away", "can_execute": False,
    }


def material_handoff(sport="basketball_nba", subject="KAT"):
    return {
        "sport_key": sport, "official_event_id": "evt-10",
        "event_start_utc": "2026-11-19T23:00:00+00:00",
        "research_status": "NEWS_RESEARCH_RESCORE_REQUESTED",
        "research_rescore_requested": True,
        "receipt_sha256": "a" * 64,
        "news_context": [{
            "source_url": "https://team.example/injury",
            "independent_review_ref": "review-1",
            "independently_reviewed_at_utc": "2026-11-19T17:00:00+00:00",
            "material_change": True, "subject_id": subject,
            "evidence_kind": "INJURY_STATUS",
        }],
        "can_execute": False, "prediction_authority": False,
    }


@pytest.mark.parametrize("sport", SPORTS_13)
def test_material_change_requests_every_market_and_position_scope(sport):
    impact = assess_context_change_impact(event(sport), material_handoff(sport), now=NOW)
    assert impact["impact_status"] == "CONTEXT_STALE_RECALC_REQUIRED"
    assert set(impact["affected_market_families"]) == EXPECTED_MARKETS
    assert impact["position_scope"] == ALL_POSITION_SCOPE
    assert impact["no_position_exclusions"] is True
    assert impact["market_scope_policy"] == "EVENT_WIDE_CONSERVATIVE_FAIL_CLOSED"
    assert impact["research_invalidation_requested"] is True
    assert impact["research_rescore_requested"] is True
    assert impact["fitted_specialist_rescore_verified"] is False
    assert impact["terminal_invalidation_enforced_by_this_module"] is False
    assert impact["model_probability_created"] is False
    assert impact["can_execute"] is False


@pytest.mark.parametrize("subject", [
    "CENTER_STARTER", "GUARD_BENCH", "QB", "KICKER", "PUNTER",
    "GOALIE", "RELIEF_PITCHER", "BATTER", "DEFENDER", "SUBSTITUTE",
    "UMPIRE", "BOWLER", "FIGHTER", "GOLFER",
])
def test_no_position_or_specialist_filtered_by_static_whitelist(subject):
    result = assess_context_change_impact(
        event("americanfootball_nfl"), material_handoff("americanfootball_nfl", subject), now=NOW
    )
    assert result["source_subject_ids"] == [subject]
    assert set(result["affected_market_families"]) == set(ALL_MARKET_FAMILIES)


def test_hart_kat_counterexample_is_stale_not_automatic_over_or_under():
    impact = assess_context_change_impact(event(), material_handoff(), now=NOW)
    assert impact["source_subject_ids"] == ["KAT"]
    assert impact["impact_status"] == "CONTEXT_STALE_RECALC_REQUIRED"
    assert impact["prediction_authority"] is False
    assert "PARTICIPANT_PROP_UNDER" in impact["affected_market_families"]
    assert "PARTICIPANT_PROP_OVER" in impact["affected_market_families"]


@pytest.mark.parametrize("kind", ["FIELD_GOAL_ATTEMPTS", "FIELD_GOALS_MADE", "KICKING_POINTS"])
def test_kicker_changes_cover_distinct_prop_markets_and_team_totals(kind):
    news = material_handoff("americanfootball_nfl", "KICKER")
    news["news_context"][0]["evidence_kind"] = kind
    impact = assess_context_change_impact(event("americanfootball_nfl"), news, now=NOW)
    assert "PARTICIPANT_PROP_OVER" in impact["affected_market_families"]
    assert "TEAM_TOTAL_UNDER" in impact["affected_market_families"]
    assert "MONEYLINE_WINNER" in impact["affected_market_families"]


def test_news_source_conflict_holds_all_markets_without_choosing_a_side():
    news = material_handoff()
    news["research_status"] = "NEWS_SOURCE_CONFLICT"
    news["news_context"] = []
    impact = assess_context_change_impact(event(), news, now=NOW)
    assert impact["impact_status"] == "CONTEXT_UNRESOLVED_REVIEW_REQUIRED"
    assert impact["research_invalidation_requested"] is True
    assert impact["research_rescore_requested"] is False


def test_wrong_event_or_stale_news_cannot_authorize_recalculation():
    news = material_handoff()
    news["official_event_id"] = "wrong"
    mismatched = assess_context_change_impact(event(), news, now=NOW)
    assert mismatched["impact_status"] == "CONTEXT_EVIDENCE_EVENT_IDENTITY_MISMATCH"
    assert mismatched["research_rescore_requested"] is False
    assert mismatched["research_invalidation_requested"] is True
    assert not mismatched["model_probability_created"]
    news = material_handoff()
    news["news_context"][0]["independently_reviewed_at_utc"] = "2026-11-19T23:01:00Z"
    late = assess_context_change_impact(event(), news, now=NOW)
    assert late["impact_status"] == "CONTEXT_MATERIAL_EVIDENCE_INVALID"
    assert late["research_rescore_requested"] is False


def test_no_news_never_claims_material_rescore_was_performed():
    result = assess_context_change_impact(event(), None, now=NOW)
    assert result["impact_status"] == "NO_MATERIAL_EVENT_RECEIPT"
    assert result["research_rescore_requested"] is False
    assert result["fitted_specialist_rescore_verified"] is False


def test_after_tipoff_cannot_recreate_pregame_receipt():
    result = assess_context_change_impact(
        event(), material_handoff(), now=datetime(2026, 11, 20, tzinfo=timezone.utc)
    )
    assert result["impact_status"] == "PREGAME_WINDOW_CLOSED"
    assert result["research_rescore_requested"] is False


def test_scout_candidate_attaches_request_and_never_claims_fitted_rescore(monkeypatch):
    from v17 import sport_scout_enrichment as scout
    monkeypatch.setattr(scout, "reconcile_news_handoff", lambda *_a, **_kw: material_handoff())
    candidate = event()
    candidate["news_discovery_receipt"] = {"source_code": "fixture"}
    output = enrich_candidate(candidate, "PROP")
    assert output["research_recommendation_status"] == "CONTEXT_STALE_RECALC_REQUIRED"
    assert output["context_change_impact"]["market_scope_policy"] == (
        "EVENT_WIDE_CONSERVATIVE_FAIL_CLOSED"
    )
    assert output["context_change_impact"]["fitted_specialist_rescore_verified"] is False
    assert output["can_execute"] is False


@pytest.mark.parametrize("sport", SPORTS_13)
@pytest.mark.parametrize("market_family", ALL_MARKET_FAMILIES)
def test_all_sport_market_scores_cannot_keep_approval_with_newer_material_context(sport, market_family):
    scored = {
        "sport_key": sport, "official_event_id": "evt-10",
        "market_family": market_family, "market_period": "FULL_GAME",
        "immutable_model_timestamp": "2026-11-19T16:00:00Z",
        "calibrated_probability": 0.72, "calibrated_lower_bound": 0.61,
        "rank_eligible": True, "probability_publishable": True,
    }
    result = hold_scored_after_material_change(
        scored, latest_material_update_timestamp="2026-11-19T17:00:00Z"
    )
    assert result["material_context_chronology_status"] == "CONTEXT_STALE_RECALC_REQUIRED"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["calibrated_probability"] == 0.72
    assert result["calibrated_lower_bound"] == 0.61
    assert result["can_execute"] is False
    assert scored["probability_publishable"] is True  # pure/non-mutating


@pytest.mark.parametrize("update", ["not-a-date", "2026-11-19T17:00:00"])
def test_invalid_material_timestamp_cannot_clear_a_scoring_hold(update):
    scored = {"probability_publishable": True, "rank_eligible": True,
              "immutable_model_timestamp": "2026-11-19T18:00:00Z"}
    result = hold_scored_after_material_change(
        scored, latest_material_update_timestamp=update
    )
    assert result["material_context_chronology_status"] == "CONTEXT_STALE_RECALC_REQUIRED"
    assert result["probability_publishable"] is False


def test_chronology_ok_is_not_a_proof_of_actual_feature_consumption():
    scored = {"probability_publishable": False, "rank_eligible": False,
              "model_timestamp": "2026-11-19T18:00:00Z",
              "calibrated_probability": 0.72}
    result = hold_scored_after_material_change(
        scored, latest_material_update_timestamp="2026-11-19T17:00:00Z"
    )
    assert result["material_context_chronology_status"] == "CHRONOLOGY_OK_CONSUMPTION_NOT_PROVEN"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
