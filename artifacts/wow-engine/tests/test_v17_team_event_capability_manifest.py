from pathlib import Path

from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
    team_event_capability,
)


def test_mlb_team_event_model_is_certified():
    result = team_event_capability("MLB")
    assert result.status == "AVAILABLE"
    assert result.controlling_specialist == "MLB_GAME_WIN_PROBABILITY_EXPERT"
    assert result.blocker is None
    assert result.required_inputs == TEAM_EVENT_INPUT_CONTRACTS["MLB"]
    assert result.can_execute is False


def test_cataloged_cross_sport_routes_are_not_silently_certified():
    for sport in ("NFL", "NBA", "NCAAF", "NCAAB", "SOCCER", "TENNIS", "PGA"):
        result = team_event_capability(sport)
        assert sport in EXPECTED_TEAM_EVENT_SPORTS
        assert result.status == "MODEL_UNAVAILABLE"
        assert result.controlling_specialist is None
        assert result.blocker == "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"
        assert result.required_inputs == TEAM_EVENT_INPUT_CONTRACTS[sport]
        assert result.can_execute is False


def test_common_aliases_normalize_to_the_governed_sport_contract():
    assert team_event_capability("MLS").sport == "SOCCER"
    assert team_event_capability("College Football").sport == "NCAAF"
    assert team_event_capability("CBB").sport == "NCAAB"
    assert team_event_capability("Golf").sport == "PGA"


def test_production_bootstrap_installs_registry_and_bridge_visible_health():
    source = (Path(__file__).parents[1] / "v17" / "team_event_bridge_runtime.py").read_text()
    observability = (Path(__file__).parents[1] / "v17_observability.py").read_text()
    assert "TEAM_EVENT_BRIDGES" in source
    assert "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED" in source
    assert "team_event_bridge_health" in source
    assert 'register_team_event_bridge(\n        "MLB"' in source
    assert "install_team_event_bridge_runtime" in observability
    assert "market_probability_substitution_allowed" in source
    assert "generic_reasoning_substitution_allowed" in source


def test_llp_instructions_do_not_claim_universal_certified_sport_coverage():
    source = (Path(__file__).parents[1] / "LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt").read_text()
    assert "The shared numerical execution envelope is sport-agnostic; certified sporting-model coverage is not." in source
    assert "soccer/MLS is not currently certified" in source
    assert "Never substitute public xG models, Elo, sportsbook implied probability" in source
