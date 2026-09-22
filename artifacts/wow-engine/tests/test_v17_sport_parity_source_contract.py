from pathlib import Path

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.team_event_sport_parity import parity_health


ROOT = Path(__file__).resolve().parents[1]


def test_all_expected_sports_are_explicitly_accounted_for():
    health = parity_health({})
    assert set(health) == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert len(health) == 12


def test_parity_contract_never_relaxes_terminal_or_market_authority():
    health = parity_health({})
    for row in health.values():
        assert row["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert row["market_probability_substitution_allowed"] is False
        assert row["generic_reasoning_substitution_allowed"] is False
        assert row["can_execute"] is False


def test_failure_registry_contains_evidence_binding_invalidity():
    text = (ROOT / "docs" / "failure_codes.md").read_text()
    assert "`RUN_INVALID_EVIDENCE_BINDING`" in text
    assert "producer→consumer evidence handoff" in text
