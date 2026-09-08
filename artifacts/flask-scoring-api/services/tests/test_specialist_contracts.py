from services.specialist_contracts import (
    NFL_DFS_FANTASY_SCORE,
    SOCCER_PASSES_ATTEMPTED,
    classify_specialist,
    reconstruct_attempts_per90,
    research_proxy_can_publish_governed_probability,
    score_nfl_fantasy_components,
    validate_nfl_dfs_inputs,
    validate_soccer_pass_attempts_inputs,
)


def test_routes_soccer_passes_attempted_to_exact_specialist():
    assert classify_specialist("SOCCER", "Passes Attempted") == SOCCER_PASSES_ATTEMPTED
    assert classify_specialist("SOCCER", "Accurate Passes") is None


def test_routes_nfl_fantasy_score_to_exact_specialist():
    assert classify_specialist("NFL", "Fantasy Score") == NFL_DFS_FANTASY_SCORE
    assert classify_specialist("NFL", "Receiving Yards") is None


def test_accurate_pass_reconstruction_is_evidence_only():
    out = reconstruct_attempts_per90(accurate_passes_per90=72, completion_rate=90)
    assert out["reconstructed_attempts_per90"] == 80.0
    assert out["source_status"] == "RECONSTRUCTED"
    assert out["governed_probability"] is None
    assert out["maximum_ceiling_without_specialist"] == "RESEARCH_INTEREST"


def test_soccer_per90_proxy_does_not_make_missing_model_inputs_ready():
    result = validate_soccer_pass_attempts_inputs({
        "player": "Example Player",
        "event_id": "evt",
        "event_date": "2026-09-09",
        "team": "A",
        "opponent": "B",
        "prop_type": "Passes Attempted",
        "exact_line": 42.5,
        "side": "MORE",
        "settlement_rule": "exact",
        "accurate_passes_per90": 50.0,
    })
    assert result.model_input_ready is False
    assert result.terminal_status == "MODEL_INPUTS_INSUFFICIENT"
    assert "expected_minutes_distribution" in result.missing_fields
    assert "PER90_RESEARCH_PROXY_NOT_MODEL_PROBABILITY" in result.blockers


def test_soccer_full_input_contract_can_be_model_ready_without_publishing_probability():
    payload = {
        "player": "Example Player",
        "event_id": "evt",
        "event_date": "2026-09-09",
        "team": "A",
        "opponent": "B",
        "stat_type": "Passes Attempted",
        "exact_line": 42.5,
        "side": "MORE",
        "settlement_rule": "exact",
        "starting_probability": 0.98,
        "expected_minutes_distribution": {"mean": 82},
        "team_pass_attempt_distribution": {"mean": 550},
        "player_pass_share_distribution": {"mean": 0.09},
        "opponent_environment": {"press": 0.4},
        "score_state_model": {"ready": True},
        "substitution_model": {"ready": True},
    }
    result = validate_soccer_pass_attempts_inputs(payload)
    assert result.model_input_ready is True
    assert result.terminal_status == "MODEL_READY"
    assert not hasattr(result, "model_probability")


SCORING = {
    "scoring_profile_id": "TEST_PPR",
    "passing_yards_points": 0.04,
    "passing_td_points": 4.0,
    "interception_points": -1.0,
    "rushing_yards_points": 0.1,
    "rushing_td_points": 6.0,
    "receiving_yards_points": 0.1,
    "reception_points": 1.0,
    "receiving_td_points": 6.0,
    "fumble_lost_points": -2.0,
    "two_point_conversion_points": 2.0,
}


def test_nfl_missing_exact_scoring_adapter_fails_closed():
    result = validate_nfl_dfs_inputs({
        "player": "Example WR",
        "event_id": "evt",
        "event_date": "2026-09-13",
        "team": "A",
        "opponent": "B",
        "position": "WR",
        "prop_type": "Fantasy Score",
        "exact_line": 18.5,
        "side": "MORE",
        "settlement_rule": "exact",
        "team_play_distribution": {"mean": 64},
        "game_state_model": {"ready": True},
        "player_opportunity_model": {"routes": 35},
    })
    assert result.model_input_ready is False
    assert result.terminal_status == "MODEL_INPUTS_INSUFFICIENT"
    assert "scoring_profile" in result.missing_fields


def test_external_dfs_projection_is_evidence_only_even_when_real_inputs_exist():
    result = validate_nfl_dfs_inputs({
        "player": "Example WR",
        "event_id": "evt",
        "event_date": "2026-09-13",
        "team": "A",
        "opponent": "B",
        "position": "WR",
        "prop_type": "Fantasy Score",
        "exact_line": 18.5,
        "side": "MORE",
        "settlement_rule": "exact",
        "scoring_profile": SCORING,
        "team_play_distribution": {"mean": 64},
        "game_state_model": {"ready": True},
        "player_opportunity_model": {"routes": 35},
        "external_dfs_projection": 21.4,
    })
    assert result.model_input_ready is True
    assert "EXTERNAL_DFS_PROJECTION_EVIDENCE_ONLY" in result.blockers


def test_nfl_component_scoring_uses_exact_profile_arithmetic():
    score = score_nfl_fantasy_components(
        {
            "receptions": 6,
            "receiving_yards": 80,
            "receiving_td": 1,
        },
        SCORING,
    )
    assert score == 20.0


def test_incomplete_scoring_profile_raises():
    broken = dict(SCORING)
    del broken["reception_points"]
    try:
        score_nfl_fantasy_components({"receptions": 3}, broken)
    except ValueError as exc:
        assert "reception_points" in str(exc)
    else:
        raise AssertionError("incomplete scoring profile must fail closed")


def test_research_proxies_are_never_governed_probability_sources():
    for source in (
        "accurate passes per90",
        "reconstructed attempts per90",
        "raw L10 hit rate",
        "external DFS projection",
        "generic PPR projection",
        "sportsbook implied probability",
    ):
        assert research_proxy_can_publish_governed_probability(source) is False
