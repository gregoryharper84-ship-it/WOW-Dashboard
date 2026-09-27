from types import SimpleNamespace

from v17.team_event_probability_preservation import (
    _annotate_probability_visibility,
    _apply_official_publication_guard,
)


def _governed_home_away_result(*, home_probability: float, home_lower: float) -> dict:
    return {
        "candidate_family": "TEAM_EVENT",
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "calibrated_home_probability": home_probability,
        "calibrated_home_lower_bound": home_lower,
        "calibrated_home_upper_bound": min(home_probability + 0.08, 1.0),
        "calibrated_away_probability": 1.0 - home_probability,
        "calibrated_away_lower_bound": max(0.0, min(home_lower - 0.02, 1.0 - home_probability)),
        "calibrated_away_upper_bound": min(1.0, (1.0 - home_probability) + 0.08),
        "llp_governance": {
            "probability_publishable": True,
            "rank_eligible": True,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
            "probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_mutex_status": "PASS",
            "postmodel_gates_status": "PASS",
            "final_gates_status": "PASS",
            "terminal_label": "FINAL_APPROVED",
        },
    }


def test_low_bound_winner_is_depublished_but_probability_remains_visible():
    req = SimpleNamespace(decision_intent="WINNER")
    result = _governed_home_away_result(home_probability=0.5239, home_lower=0.4291)

    guarded = _apply_official_publication_guard(req, result)
    visible = _annotate_probability_visibility(guarded)

    assert visible["calibrated_home_probability"] == 0.5239
    assert visible["calibrated_home_lower_bound"] == 0.4291
    assert visible["probability_publishable"] is False
    assert visible["rank_eligible"] is False
    assert visible["probability_visibility_status"] == "MODELED_HELD"
    assert visible["official_leaderboard_eligible"] is False
    assert "TEAM_EVENT_WINNER_LOWER_BOUND_BELOW_WATCH_FLOOR" in visible["blockers"]
    assert visible["can_execute"] is False


def test_winner_watch_floor_remains_publishable_when_other_governance_passes():
    req = SimpleNamespace(decision_intent="WINNER")
    result = _governed_home_away_result(home_probability=0.61, home_lower=0.56)

    guarded = _apply_official_publication_guard(req, result)
    visible = _annotate_probability_visibility(guarded)

    assert visible["probability_publishable"] is True
    assert visible["rank_eligible"] is True
    assert visible["probability_visibility_status"] == "OFFICIAL_QUALIFIED"
    assert visible["probability_tier"] == "WINNER_WATCH"
    assert visible["can_execute"] is False
