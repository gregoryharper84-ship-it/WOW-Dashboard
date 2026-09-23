from __future__ import annotations

import pytest

from v17.capability_build_registry import (
    TEAM_EVENT_BUILD_REGISTRY,
    capability_build_plan,
    validate_build_registry,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


def test_every_declared_team_event_sport_has_exactly_one_build_plan():
    assert set(TEAM_EVENT_BUILD_REGISTRY) == set(EXPECTED_TEAM_EVENT_SPORTS)
    validate_build_registry()


@pytest.mark.parametrize("sport", EXPECTED_TEAM_EVENT_SPORTS)
def test_controlling_probability_is_d1_not_agent_reasoning(sport):
    plan = capability_build_plan(sport)
    assert plan.controlling_probability_class == "D1"
    assert plan.fitted_model_required is True
    assert plan.agent_may_publish_probability is False
    assert plan.probability_publishable_from_registry is False
    assert plan.automatic_certification is False
    assert plan.automatic_promotion is False
    assert plan.can_execute is False


def test_current_known_build_states_are_explicit():
    assert capability_build_plan("MLB").status == "CERTIFIED"
    assert capability_build_plan("NFL").status == "FORWARD_EVIDENCE_PENDING"
    assert capability_build_plan("NBA").status == "CURRENT_DATA_PENDING"
    assert capability_build_plan("WNBA").status == "CURRENT_DATA_PENDING"
    assert capability_build_plan("NCAAF").status == "EVIDENCE_CORPUS_PENDING"
    assert capability_build_plan("NHL").status == "EVIDENCE_CORPUS_PENDING"
    for sport in ("NCAAB", "SOCCER", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET"):
        assert capability_build_plan(sport).status == "FITTED_SPECIALIST_PENDING"


def test_unknown_sport_cannot_be_silently_classified():
    with pytest.raises(KeyError, match="V17_BUILD_CLASSIFICATION_UNKNOWN"):
        capability_build_plan("LACROSSE")
