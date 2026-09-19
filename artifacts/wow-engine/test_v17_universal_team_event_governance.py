from pathlib import Path

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.team_event_governance_profiles import (
    TEAM_EVENT_GOVERNANCE_PROFILES,
    TERMINAL_AUTHORITY,
    governance_health,
    governance_profile,
)
from v17.team_event_model_development_manifest import TEAM_EVENT_MODEL_DEVELOPMENT


def test_every_cataloged_sport_has_exact_governance_profile():
    assert set(TEAM_EVENT_GOVERNANCE_PROFILES) == set(EXPECTED_TEAM_EVENT_SPORTS)
    health = governance_health()
    assert set(health) == set(EXPECTED_TEAM_EVENT_SPORTS)
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        row = health[sport]
        assert row["status"] == "INSTALLED"
        assert row["terminal_authority"] == TERMINAL_AUTHORITY
        assert row["probability_market_independent"] is True
        assert row["can_execute"] is False
        assert row["evidence_families"]


def test_profiles_preserve_sport_specific_outcome_spaces():
    assert governance_profile("MLB").outcome_space == "HOME_AWAY"
    assert governance_profile("MLS").outcome_space == "HOME_DRAW_AWAY"
    assert governance_profile("TENNIS").outcome_space == "PLAYER_A_PLAYER_B"
    assert governance_profile("UFC").outcome_space == "FIGHTER_A_FIGHTER_B_DRAW_NC"
    assert governance_profile("GOLF").outcome_space == "FIELD_OR_HEAD_TO_HEAD"


def test_model_development_manifest_covers_every_governed_sport_without_fake_promotion():
    assert set(TEAM_EVENT_MODEL_DEVELOPMENT) == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert TEAM_EVENT_MODEL_DEVELOPMENT["MLB"].status == "PRODUCTION_MODEL_PRESENT"
    assert TEAM_EVENT_MODEL_DEVELOPMENT["NFL"].status == "PRODUCTION_MODEL_PRESENT"
    for sport in ("NBA", "WNBA", "NCAAF", "NCAAB", "NHL", "SOCCER", "TENNIS"):
        assert TEAM_EVENT_MODEL_DEVELOPMENT[sport].status == "CANDIDATE_PIPELINE_PRESENT"
        assert TEAM_EVENT_MODEL_DEVELOPMENT[sport].maintenance_lane
        assert TEAM_EVENT_MODEL_DEVELOPMENT[sport].can_execute is False
    for sport in ("PGA", "MMA", "BOXING"):
        assert TEAM_EVENT_MODEL_DEVELOPMENT[sport].status == "BUILD_REQUIRED"
        assert TEAM_EVENT_MODEL_DEVELOPMENT[sport].maintenance_lane is None
        assert TEAM_EVENT_MODEL_DEVELOPMENT[sport].can_execute is False


def test_universal_wrapper_is_installed_after_bridge_registry():
    root = Path(__file__).parent
    source = (root / "v17_observability.py").read_text()
    assert "install_team_event_bridge_runtime()" in source
    assert "install_universal_team_event_governance()" in source
    assert source.index("install_team_event_bridge_runtime()") < source.index("install_universal_team_event_governance()")


def test_universal_wrapper_does_not_register_or_promote_missing_models():
    root = Path(__file__).parent
    source = (root / "v17" / "universal_team_event_governance.py").read_text()
    assert "register_team_event_bridge" not in source
    assert 'out["can_execute"] = False' in source
    assert 'out["global_terminal_reducer"] = TERMINAL_AUTHORITY' in source
    assert "rank_eligible\"] = True" not in source
    assert "probability_publishable\"] = True" not in source
