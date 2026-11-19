"""Regression tests for default research-only matchup discovery."""
from datetime import datetime, timezone

from v17.matchup_discovery import discover_matchup, feature_requirements, profile_for
from v17.sport_scout_enrichment import enrich_candidate

NOW = datetime(2026, 11, 19, 17, 5, tzinfo=timezone.utc)
START = "2026-11-19T23:00:00Z"


def candidate(sport="icehockey_nhl"):
    return {
        "official_event_id": "evt-10",
        "sport_key": sport,
        "commence_time": START,
        "home_team": "Home",
        "away_team": "Away",
        "can_execute": False,
    }


def row(*, source_class="ESTABLISHED_STATS_PROVIDER", captured_at="2026-11-19T17:00:00Z",
        confirmed=True, event_id="evt-10", value=1):
    return {
        "official_event_id": event_id,
        "source_id": "source-1",
        "source_class": source_class,
        "captured_at": captured_at,
        "confirmed": confirmed,
        "value": value,
    }


def test_every_requested_sport_has_specialist_feature_map():
    expected = {
        "americanfootball_nfl": "FOOTBALL", "americanfootball_ncaaf": "FOOTBALL",
        "basketball_nba": "BASKETBALL", "basketball_wnba": "BASKETBALL",
        "basketball_ncaab": "BASKETBALL", "baseball_mlb": "BASEBALL",
        "icehockey_nhl": "HOCKEY", "soccer_epl": "SOCCER",
        "mma_ufc": "COMBAT", "boxing": "COMBAT",
        "tennis_atp": "TENNIS", "golf_pga": "GOLF", "cricket_ipl": "CRICKET",
    }
    for sport, profile in expected.items():
        assert profile_for(sport) == profile
        requirements = feature_requirements(sport)
        assert len(requirements["SPORT_SPECIFIC"]) >= 5
        assert len(requirements["GAME_SCRIPT"]) >= 3


def test_default_audit_is_requested_but_empty_evidence_never_claims_completed_review():
    result = discover_matchup(candidate(), now=NOW)
    assert result["review_requested"] is True
    assert result["review_status"] == "MATCHUP_EVIDENCE_NOT_OBSERVED"
    assert "confirmed_starting_goalies" in result["missing_critical_features"]
    assert "five_on_five_xg_and_shots" in result["missing_critical_features"]
    assert result["screened_count"] == 0
    assert result["prediction_authority"] is False
    assert result["can_execute"] is False
    assert result["confidence_or_model_probability_created"] is False


def test_screened_sourced_evidence_is_not_specialist_probability_or_verification():
    c = candidate("basketball_nba")
    c["matchup_metric_evidence"] = {
        "opponent_adjusted_strength": row(value=0),
        "minutes_and_foul_rotation": row(),
    }
    result = discover_matchup(c, now=NOW)
    assert result["screened_features"] == ["opponent_adjusted_strength", "minutes_and_foul_rotation"]
    assert result["review_status"] == "PARTIAL_MATCHUP_REVIEW"
    assert result["source_screening_is_not_independent_model_verification"] is True
    assert result["specialist_rescore_verified"] is False
    assert "opponent_adjusted_strength" not in result["missing_critical_features"]


def test_wrong_event_future_stale_unverified_and_conflicted_sources_never_pass():
    c = candidate()
    c["matchup_metric_evidence"] = {
        "confirmed_starting_goalies": row(event_id="wrong", source_class="TEAM_OFFICIAL"),
        "goalie_gsax_and_workload": row(captured_at="2026-11-19T18:00:00Z"),
        "five_on_five_xg_and_shots": row(captured_at="2026-11-17T17:00:00Z"),
        "power_play_vs_penalty_kill": row(source_class="SOCIAL_UNVERIFIED"),
        "score_effects_and_empty_net": {**row(), "conflict": True},
    }
    result = discover_matchup(c, now=NOW)
    assert result["review_status"] == "CONFLICT_QUARANTINED"
    assert result["invalid_or_stale_features"]["confirmed_starting_goalies"] == "EVENT_ID_MISMATCH"
    assert result["invalid_or_stale_features"]["goalie_gsax_and_workload"] == "TIMESTAMP_NOT_VALID_PREGAME"
    assert result["invalid_or_stale_features"]["five_on_five_xg_and_shots"] == "STALE_OR_UNCONFIRMED_SOURCE"
    assert result["invalid_or_stale_features"]["score_effects_and_empty_net"] == "CONFLICT_QUARANTINED"


def test_evidence_captured_after_kickoff_is_not_replayed_as_pregame():
    c = candidate()
    c["matchup_metric_evidence"] = {
        "opponent_adjusted_strength": row(captured_at="2026-11-19T23:01:00Z"),
    }
    result = discover_matchup(c, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert result["review_status"] == "PREGAME_WINDOW_CLOSED"
    assert result["screened_count"] == 0
    assert result["invalid_or_stale_features"]["opponent_adjusted_strength"] == "TIMESTAMP_NOT_VALID_PREGAME"


def test_nightly_enrichment_requests_matchup_review_for_prop_and_event():
    for lane in ("PROP", "TEAM_EVENT"):
        candidate_row = candidate("americanfootball_nfl")
        enriched = enrich_candidate(candidate_row, lane)
        assert enriched["matchup_discovery"]["review_requested"] is True
        assert enriched["matchup_discovery"]["profile"] == "FOOTBALL"
        assert "ol_vs_dl_pressure" in enriched["matchup_discovery"]["missing_critical_features"]
        assert enriched["matchup_discovery"]["prediction_authority"] is False
        assert enriched["can_execute"] is False
