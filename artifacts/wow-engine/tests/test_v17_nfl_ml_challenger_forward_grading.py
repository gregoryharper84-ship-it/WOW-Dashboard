from __future__ import annotations

from v17.nfl_ml_challenger_forward_grading import (
    challenger_grade_metrics,
    gradeable_updates,
)


def _shadow(**override):
    row = {
        "shadow_id": "11111111-1111-1111-1111-111111111111",
        "challenger_id": "NFL_ML_STATIONARY_DECAY_PLATT_COMPOSITE_LB_V1",
        "official_event_id": "2026_04_ATL_NO",
        "home_team": "New Orleans Saints",
        "away_team": "Atlanta Falcons",
        "selected_participant": "Atlanta Falcons",
        "calibrated_probability": 0.581583343499892,
        "lifecycle_state": "FORWARD_SHADOW",
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    row.update(override)
    return row


def _outcome(**override):
    row = {
        "game_id": "2026_04_ATL_NO",
        "home_score": 20,
        "away_score": 27,
        "home_win": False,
        "tie": False,
        "can_execute": False,
    }
    row.update(override)
    return row


def test_challenger_settlement_grades_exact_selected_side_without_rewriting_probability():
    updates = gradeable_updates(
        [_shadow()],
        [_outcome()],
        graded_at="2026-10-07T00:00:00+00:00",
    )
    assert len(updates) == 1
    grade = updates[0]
    assert grade["outcome"] == 1
    assert grade["hit"] is True
    assert grade["lifecycle_state"] == "GRADED"
    assert grade["probability_publishable"] is False
    assert grade["promotion_authorized"] is False
    assert grade["can_execute"] is False
    # The update payload contains no model probability or bound fields.
    assert "calibrated_probability" not in grade
    assert "composite_lower_bound" not in grade


def test_challenger_grade_is_idempotent_and_skips_already_graded_rows():
    assert gradeable_updates(
        [_shadow(lifecycle_state="GRADED")],
        [_outcome()],
    ) == []


def test_challenger_grade_excludes_ties_from_binary_outcome():
    assert gradeable_updates(
        [_shadow()],
        [_outcome(tie=True, home_score=20, away_score=20)],
    ) == []


def test_challenger_metrics_remain_fail_closed_before_forward_floor():
    rows = [
        {
            "lifecycle_state": "GRADED",
            "brier": 0.17,
            "log_loss": 0.55,
            "hit": True,
        }
    ]
    metrics = challenger_grade_metrics(rows)
    assert metrics["status"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert metrics["promotion_authorized"] is False
    assert metrics["probability_publishable"] is False
    assert metrics["can_execute"] is False
    assert "CHALLENGER_FORWARD_GRADED_N_1_LT_100" in metrics["blockers"]
