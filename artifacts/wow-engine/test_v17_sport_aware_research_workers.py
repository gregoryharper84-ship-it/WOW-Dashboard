from datetime import datetime, timezone

from v17.scout_source_policy import conflict_policy, evidence_quality, policy_payload
from v17.sport_research_brief import SUPPORTED_RESEARCH_WORKERS, build_research_brief, resolve_cycle_stage
from v17.sport_scout_enrichment import enrich_candidate

EXPECTED_WORKERS = {
    "wow.source-provenance-researcher",
    "wow.participant-status-researcher",
    "wow.history-comparables-researcher",
    "wow.matchup-context-researcher",
    "wow.market-settlement-researcher",
}


def candidate(sport_key: str, commence_time: str = "2026-09-12T23:00:00Z"):
    return {
        "sport_key": sport_key,
        "official_event_id": "evt-1",
        "commence_time": commence_time,
        "home_team": "Home",
        "away_team": "Away",
        "route": "LLP_TEAM_BETTING_ENGINE",
        "can_execute": False,
    }


def test_exact_existing_research_worker_barrier_is_preserved():
    assert set(SUPPORTED_RESEARCH_WORKERS) == EXPECTED_WORKERS
    assert len(SUPPORTED_RESEARCH_WORKERS) == 5


def test_cfb_wednesday_uses_matchup_deep_dive_stage():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)  # Wednesday
    stage = resolve_cycle_stage(candidate("americanfootball_ncaaf"), now=now)
    assert "MATCHUP" in stage


def test_nfl_thursday_uses_thursday_research_stage():
    now = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)  # Thursday
    stage = resolve_cycle_stage(candidate("americanfootball_nfl"), now=now)
    assert "THURSDAY" in stage or "TNF" in stage or "SUNDAY" in stage


def test_mlb_near_event_moves_to_pregame_final_board():
    now = datetime(2026, 9, 12, 22, 0, tzinfo=timezone.utc)
    row = candidate("baseball_mlb", "2026-09-12T23:00:00Z")
    assert resolve_cycle_stage(row, now=now) == "PRE_GAME_FINAL_BOARD"


def test_sportsbook_feed_becomes_stale_after_sla_and_has_no_prediction_authority():
    result = evidence_quality("SPORTSBOOK_FEED", age_minutes=16, confirmed=True)
    assert result["stale"] is True
    assert result["research_usable"] is False
    assert result["prediction_authority"] is False
    assert result["can_execute"] is False


def test_unverified_social_source_cannot_independently_advance_research():
    result = evidence_quality("SOCIAL_UNVERIFIED", age_minutes=5, confirmed=True)
    assert result["research_usable"] is False
    assert result["prediction_authority"] is False


def test_high_impact_beat_report_requires_confirmation():
    unconfirmed = evidence_quality("PRIMARY_BEAT_REPORTER", age_minutes=10, confirmed=False, evidence_type="QB_STATUS")
    confirmed = evidence_quality("PRIMARY_BEAT_REPORTER", age_minutes=10, confirmed=True, evidence_type="QB_STATUS")
    assert unconfirmed["confirmation_required"] is True
    assert unconfirmed["research_usable"] is False
    assert confirmed["research_usable"] is True


def test_same_tier_material_conflict_quarantines_until_reconciled():
    assert conflict_policy()["same_tier_material_conflict"] == "QUARANTINE_UNTIL_RECONCILED"


def test_all_sport_policies_remain_research_only():
    for sport in ("americanfootball_ncaaf", "americanfootball_nfl", "baseball_mlb", "basketball_nba", "basketball_wnba"):
        policy = policy_payload(sport)
        assert policy["research_ceiling"] == "RESEARCH_INTEREST"
        assert policy["prediction_authority"] is False
        assert policy["sportsbook_evidence_only"] is True
        assert policy["can_execute"] is False


def test_enrichment_attaches_one_brief_per_existing_worker_without_probability_authority():
    row = enrich_candidate(candidate("americanfootball_ncaaf"), "TEAM_EVENT")
    briefs = row["research_worker_briefs"]
    assert set(briefs) == EXPECTED_WORKERS
    for worker_id, brief in briefs.items():
        assert brief["worker_id"] == worker_id
        assert brief["scout_team"] == "CFB_SCOUT_TEAM"
        assert brief["research_ceiling"] == "RESEARCH_INTEREST"
        assert brief["prediction_authority"] is False
        assert brief["output_requirements"]["may_create_probability"] is False
        assert brief["output_requirements"]["may_create_terminal_qualification"] is False
        assert brief["can_execute"] is False
    assert row["research_workers_may_create_probability"] is False
    assert row["can_execute"] is False


def test_participant_worker_gets_sport_specific_agent_missions():
    brief = build_research_brief(candidate("americanfootball_nfl"), "wow.participant-status-researcher")
    names = {a["agent"] for a in brief["agent_requirements"]}
    assert any("PERSONNEL" in name or "USAGE" in name or "QB" in name for name in names)
