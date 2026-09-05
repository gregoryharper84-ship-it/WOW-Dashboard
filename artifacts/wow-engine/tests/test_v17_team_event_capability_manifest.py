from pathlib import Path

from v17.team_event_capability_manifest import team_event_capability


def test_mlb_team_event_model_is_certified():
    result = team_event_capability("MLB")
    assert result.status == "AVAILABLE"
    assert result.controlling_specialist == "MLB_GAME_WIN_PROBABILITY_EXPERT"
    assert result.blocker is None
    assert result.can_execute is False


def test_soccer_and_mls_are_not_silently_promoted_to_generic_model():
    for sport in ("SOCCER", "MLS", "Major League Soccer"):
        result = team_event_capability(sport)
        assert result.status == "MODEL_UNAVAILABLE"
        assert result.controlling_specialist is None
        assert result.blocker == "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"
        assert result.can_execute is False


def test_active_team_event_runtime_remains_fail_closed_for_unregistered_sports():
    source = (Path(__file__).parents[1] / "v17" / "team_event_request_runtime.py").read_text()
    assert 'if sport == "MLB"' in source
    assert '"code": "MODEL_UNAVAILABLE"' in source
    assert '"backend_route_status": "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED"' in source
    assert '"market_probability_substitution_allowed": False' in source
    assert '"generic_reasoning_substitution_allowed": False' in source


def test_llp_instructions_do_not_claim_universal_certified_sport_coverage():
    source = (Path(__file__).parents[1] / "LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt").read_text()
    assert "The shared numerical execution envelope is sport-agnostic; certified sporting-model coverage is not." in source
    assert "soccer/MLS is not currently certified" in source
    assert "Never substitute public xG models, Elo, sportsbook implied probability" in source
