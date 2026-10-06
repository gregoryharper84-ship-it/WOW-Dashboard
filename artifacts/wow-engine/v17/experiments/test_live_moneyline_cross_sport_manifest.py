from v17.experiments.live_moneyline_cross_sport_manifest import (
    LANES,
    MANIFEST_RECEIPT,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


def test_live_research_manifest_covers_every_canonical_sport_exactly_once():
    assert set(LANES) == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert MANIFEST_RECEIPT["sport_n"] == 13
    assert MANIFEST_RECEIPT["status"] == "EXPERIMENT_CREATED"


def test_no_live_research_lane_can_publish_promote_execute_or_use_provider_probability():
    for lane in LANES.values():
        assert lane.provider_probability_allowed is False
        assert lane.probability_publishable is False
        assert lane.automatic_promotion is False
        assert lane.can_execute is False


def test_multiclass_sports_preserve_non_binary_outcomes():
    assert LANES["SOCCER"].outcome_space == ("HOME_WIN", "DRAW", "AWAY_WIN")
    assert "DRAW_NO_CONTEST" in LANES["MMA"].outcome_space
    assert "DRAW_NO_CONTEST" in LANES["BOXING"].outcome_space
    assert "TIE_NO_RESULT" in LANES["CRICKET"].outcome_space


def test_team_sports_do_not_share_one_generic_live_state_contract():
    assert LANES["MLB"].state_families != LANES["NFL"].state_families
    assert LANES["NHL"].state_families != LANES["NBA"].state_families
    assert LANES["SOCCER"].calibration_family == "TIME_SPLIT_MULTICLASS_CALIBRATION"
