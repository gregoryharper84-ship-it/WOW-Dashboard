from services.specialist_contracts import (
    CAN_EXECUTE,
    MLB_HITTER_FANTASY_SCORE,
    MLB_PITCHER_FANTASY_SCORE,
    NBA_DFS_FANTASY_SCORE,
    WNBA_DFS_FANTASY_SCORE,
    classify_specialist,
    research_proxy_can_publish_governed_probability,
    validate_non_nfl_fantasy_score_inputs,
)


def _base(sport, prop_type):
    return {
        "sport": sport,
        "prop_type": prop_type,
        "player": "Player A",
        "event_id": "evt-1",
        "event_date": "2026-09-15",
        "team": "AAA",
        "opponent": "BBB",
        "exact_line": 30.5,
        "side": "MORE",
        "settlement_rule": "OFFICIAL_BOX_SCORE",
        "scoring_profile": {
            "profile_id": "TEST_PROFILE",
            "verified": True,
            "weights": {"x": 1.0},
        },
    }


def test_classifier_routes_all_current_non_nfl_fantasy_score_lanes_exactly():
    assert classify_specialist("NBA", "Fantasy Score") == NBA_DFS_FANTASY_SCORE
    assert classify_specialist("WNBA", "Fantasy Points") == WNBA_DFS_FANTASY_SCORE
    assert classify_specialist("MLB", "Hitter Fantasy Score") == MLB_HITTER_FANTASY_SCORE
    assert classify_specialist("baseball", "Pitcher Fantasy Score") == MLB_PITCHER_FANTASY_SCORE


def test_generic_mlb_fantasy_score_does_not_guess_hitter_vs_pitcher():
    assert classify_specialist("MLB", "Fantasy Score") is None
    result = validate_non_nfl_fantasy_score_inputs(_base("MLB", "Fantasy Score"))
    assert result.model_input_ready is False
    assert result.terminal_status == "MODEL_INPUTS_INSUFFICIENT"
    assert "MLB_FANTASY_SCORE_ROLE_IDENTITY_UNRESOLVED" in result.blockers


def test_nba_candidate_contract_requires_minutes_pace_usage_context():
    payload = _base("NBA", "Fantasy Score")
    result = validate_non_nfl_fantasy_score_inputs(payload)
    assert result.model_input_ready is False
    assert set(result.missing_fields) >= {"minutes_model", "pace_model", "usage_model"}

    payload.update({
        "minutes_model": {"status": "READY"},
        "pace_model": {"status": "READY"},
        "usage_model": {"status": "READY"},
    })
    result = validate_non_nfl_fantasy_score_inputs(payload)
    assert result.model_input_ready is True
    assert result.terminal_status == "MODEL_READY"
    assert result.controlling_specialist == "wow.nba-dfs-fantasy-score-expert"
    assert result.can_execute is False


def test_mlb_hitter_and_pitcher_require_different_exact_contexts():
    hitter = _base("MLB", "Hitter Fantasy Score")
    hitter.update({
        "plate_appearance_model": {"status": "READY"},
        "lineup_role_model": {"status": "READY"},
        "opponent_pitching_model": {"status": "READY"},
    })
    assert validate_non_nfl_fantasy_score_inputs(hitter).model_input_ready is True

    pitcher = _base("MLB", "Pitcher Fantasy Score")
    pitcher.update({
        "workload_model": {"status": "READY"},
        "opponent_model": {"status": "READY"},
        "bullpen_hook_model": {"status": "READY"},
    })
    assert validate_non_nfl_fantasy_score_inputs(pitcher).model_input_ready is True


def test_external_or_generic_fantasy_projection_never_gains_probability_authority():
    assert research_proxy_can_publish_governed_probability("EXTERNAL DFS PROJECTION") is False
    assert research_proxy_can_publish_governed_probability("GENERIC FANTASY PROJECTION") is False


def test_can_execute_remains_false():
    assert CAN_EXECUTE is False
