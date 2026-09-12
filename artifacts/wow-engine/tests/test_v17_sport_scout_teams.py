from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1] / "v17"
sys.path.insert(0, str(ROOT))

from sport_scout_registry import REGISTRY, scout_team_for, registry_payload
from sport_scout_enrichment import enrich_handoff
from scout_research_ledger import empty_ledger, merge_handoff, apply_research_update, priority_board


def sample_handoff():
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": "DISCOVERY_COMPLETE",
        "run_id": "wow-scout-test-1",
        "can_execute": False,
        "governance": {},
        "model_handoff": {
            "team_event_candidates": [
                {
                    "official_event_id": "cfb-1",
                    "sport_key": "americanfootball_ncaaf",
                    "home_team": "Home U",
                    "away_team": "Away U",
                    "commence_time": "2026-09-12T23:00:00Z",
                    "route": "LLP_TEAM_BETTING_ENGINE",
                    "discovery_status": "DISCOVERY_ONLY",
                    "research_ceiling": "RESEARCH_INTEREST",
                    "market_evidence": [],
                    "game_script_hypotheses": ["SHOOTOUT"],
                }
            ],
            "prop_candidates": [
                {
                    "official_event_id": "nfl-1",
                    "sport_key": "americanfootball_nfl",
                    "home_team": "Home NFL",
                    "away_team": "Away NFL",
                    "commence_time": "2026-09-13T17:00:00Z",
                    "route": "WOW_PROP_LANE",
                    "discovery_status": "DISCOVERY_ONLY",
                    "research_ceiling": "RESEARCH_INTEREST",
                    "market_evidence": {"market_key": "player_rushing_yards", "description": "RB", "point": 72.5},
                    "game_script_hypotheses": ["RUN_HEAVY_LEADING"],
                }
            ],
        },
    }


def test_required_sport_teams_exist():
    assert set(REGISTRY) >= {
        "americanfootball_ncaaf",
        "americanfootball_nfl",
        "baseball_mlb",
        "basketball_nba",
        "basketball_wnba",
    }
    assert scout_team_for("americanfootball_ncaaf").team_id == "CFB_SCOUT_TEAM"
    assert scout_team_for("americanfootball_nfl").team_id == "NFL_SCOUT_TEAM"


def test_registry_is_discovery_only_and_non_executable():
    payload = registry_payload()
    assert payload["research_ceiling"] == "RESEARCH_INTEREST"
    assert payload["can_execute"] is False
    assert payload["governance"]["scout_probability_authority"] is False
    assert payload["governance"]["sportsbook_probability_authority"] is False
    assert payload["governance"]["final_probability_requires_controlling_specialist"] is True


def test_football_teams_have_specialist_agents_and_weekly_cycles():
    cfb = scout_team_for("americanfootball_ncaaf")
    nfl = scout_team_for("americanfootball_nfl")
    cfb_agents = {a.name for a in cfb.agents}
    nfl_agents = {a.name for a in nfl.agents}
    for expected in {"QB_SCOUT", "TRENCHES_SCOUT", "USAGE_SCOUT", "SCHEME_MATCHUP_SCOUT", "CONTRARIAN_RED_TEAM_SCOUT"}:
        assert expected in cfb_agents
        assert expected in nfl_agents
    assert "MON_POSTMORTEM_AND_OPENERS" in cfb.research_cycle
    assert "SAT_FINAL_BOARD" in cfb.research_cycle
    assert "THU_TNF_FINAL_AND_SUN_PRELIM" in nfl.research_cycle
    assert "SUN_INACTIVES_WEATHER_FINAL" in nfl.research_cycle


def test_mlb_and_basketball_have_domain_specific_agents():
    mlb = {a.name for a in scout_team_for("baseball_mlb").agents}
    nba = {a.name for a in scout_team_for("basketball_nba").agents}
    assert {"STARTING_PITCHER_SCOUT", "BULLPEN_SCOUT", "LINEUP_HITTING_SCOUT", "PARK_WEATHER_SCOUT"} <= mlb
    assert {"ROTATION_USAGE_SCOUT", "MATCHUP_PACE_SCOUT", "SCHEDULE_FATIGUE_SCOUT"} <= nba


def test_enrichment_routes_candidates_to_controlling_specialists():
    enriched = enrich_handoff(sample_handoff())
    team = enriched["model_handoff"]["team_event_candidates"][0]
    prop = enriched["model_handoff"]["prop_candidates"][0]
    assert team["scout_team_id"] == "CFB_SCOUT_TEAM"
    assert team["controlling_specialist_route"] == "LLP_TEAM_BETTING_ENGINE"
    assert team["route"] == "LLP_TEAM_BETTING_ENGINE"
    assert prop["scout_team_id"] == "NFL_SCOUT_TEAM"
    assert prop["controlling_specialist_route"] == "WOW_PROP_LANE"
    assert prop["route"] == "WOW_PROP_LANE"
    assert team["probability_authority"] is False
    assert prop["probability_authority"] is False
    assert enriched["can_execute"] is False


def test_ledger_persists_candidate_observations_without_probability():
    enriched = enrich_handoff(sample_handoff())
    ledger = merge_handoff(empty_ledger(), enriched)
    assert len(ledger["candidates"]) == 2
    cfb = next(v for v in ledger["candidates"].values() if v["sport_key"] == "americanfootball_ncaaf")
    assert cfb["research_status"] == "WATCH"
    assert cfb["controlling_specialist_route"] == "LLP_TEAM_BETTING_ENGINE"
    assert cfb["observations"][0]["probability"] is None
    assert cfb["can_execute"] is False


def test_research_update_can_promote_interest_but_not_add_probability():
    ledger = merge_handoff(empty_ledger(), enrich_handoff(sample_handoff()))
    candidate_id = next(iter(ledger["candidates"]))
    updated = apply_research_update(ledger, {
        "candidate_id": candidate_id,
        "research_status": "RESEARCH_INTEREST_HIGH",
        "edge_classes": ["SCHEME_EDGE", "PERSONNEL_EDGE"],
        "thesis": ["OL/DL interaction favors offense"],
        "contradictory_evidence": ["market moved against thesis"],
        "red_team": {"status": "PASS_WITH_FLAGS", "flags": ["WEATHER_SHIFT"]},
        "probability": 0.99,
    })
    entry = updated["candidates"][candidate_id]
    assert entry["research_status"] == "RESEARCH_INTEREST_HIGH"
    assert entry["probability"] is None
    assert entry["can_execute"] is False


def test_priority_board_orders_high_interest_before_watch_and_keeps_quarantine_visible():
    ledger = merge_handoff(empty_ledger(), enrich_handoff(sample_handoff()))
    ids = list(ledger["candidates"])
    apply_research_update(ledger, {"candidate_id": ids[0], "research_status": "QUARANTINED", "red_team": {"status": "QUARANTINED", "flags": ["IDENTITY_AMBIGUITY"]}})
    apply_research_update(ledger, {"candidate_id": ids[1], "research_status": "RESEARCH_INTEREST_HIGH"})
    board = priority_board(ledger)
    assert board["candidates"][0]["research_status"] == "RESEARCH_INTEREST_HIGH"
    assert any(row["research_status"] == "QUARANTINED" for row in board["candidates"])
    assert board["can_execute"] is False


def test_executable_handoff_is_rejected():
    payload = sample_handoff()
    payload["can_execute"] = True
    try:
        enrich_handoff(payload)
    except ValueError as exc:
        assert str(exc) == "SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE"
    else:
        raise AssertionError("executable Scout handoff must be rejected")
