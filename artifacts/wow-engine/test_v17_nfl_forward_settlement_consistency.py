"""Regression: only consistent, non-tied official NFL results can be binary graded.

Uses the real prospective grading function and synthetic settlements. No
historical forecast is invented, persisted or retroactively certified.
"""
from datetime import datetime, timedelta, timezone

import pytest

from v17.nfl_forward_shadow import ForwardPrediction, grade_forward_predictions, calibration_health


START = datetime(2026, 11, 22, 18, tzinfo=timezone.utc)


def prediction(selected_home=True, index=1):
    return ForwardPrediction(
        event_prediction_id=f"synthetic-settlement-{index}",
        official_event_id=f"2026_12_AAA_BBB_{index}",
        event_start_time_utc=START + timedelta(days=index),
        prediction_created_at=START + timedelta(days=index, hours=-3),
        model_version="synthetic-no-model-promotion",
        home_team="BBB",
        away_team="AAA",
        selected_participant="BBB" if selected_home else "AAA",
        calibrated_probability=0.65,
        calibrated_lower_bound=0.55,
        calibrated_upper_bound=0.73,
        model_package_valid=True,
        provenance_complete=True,
        model_probability_publishable=True,
        can_execute=False,
    )


def settlement(p, home=27, away=17, home_win=True, tie=False):
    return {
        "game_id": p.official_event_id,
        "home_score": home,
        "away_score": away,
        "home_win": home_win,
        "tie": tie,
    }


@pytest.mark.parametrize("tie,home_win", [
    (True, False), (False, False), (False, True), (None, False),
])
def test_equal_scores_never_create_binary_forward_grade(tie, home_win):
    p = prediction()
    assert grade_forward_predictions([p], [settlement(
        p, home=27, away=27, home_win=home_win, tie=tie,
    )]) == []


@pytest.mark.parametrize("home,away,home_win,tie", [
    (27, 17, False, False),  # Home win flag contradicts final score
    (17, 27, True, False),
    (27, 17, True, True),  # Tie flag contradicts final score
    (27, 17, "False", False),  # A nonempty string cannot be truthy-coerced
    (27, 17, 1, False),  # Numeric approximation is not a verified boolean
    (-1, 17, False, False),
    (True, 17, True, False),
    ("27", 17, True, False),
    (27, None, True, False),
])
def test_inconsistent_or_untyped_settlement_is_not_binary_grade(
    home, away, home_win, tie,
):
    p = prediction()
    assert grade_forward_predictions([p], [settlement(
        p, home=home, away=away, home_win=home_win, tie=tie,
    )]) == []


@pytest.mark.parametrize("selected_home,home,away,home_win,expected", [
    (True, 27, 17, True, 1),
    (False, 27, 17, True, 0),
    (True, 17, 27, False, 0),
    (False, 17, 27, False, 1),
])
def test_consistent_settlement_preserves_home_away_grading(
    selected_home, home, away, home_win, expected,
):
    p = prediction(selected_home=selected_home)
    grades = grade_forward_predictions([p], [settlement(
        p, home=home, away=away, home_win=home_win,
    )])
    assert len(grades) == 1
    assert grades[0].outcome == expected
    assert grades[0].brier == pytest.approx((p.calibrated_probability - expected) ** 2)
    assert grades[0].can_execute is False


def test_corrupt_tie_cannot_supply_hundredth_required_forward_grade():
    predictions = [prediction(index=i) for i in range(100)]
    outcomes = [
        settlement(p, home=27, away=27, home_win=False, tie=False)
        if i == 99 else settlement(p)
        for i, p in enumerate(predictions)
    ]
    grades = grade_forward_predictions(predictions, outcomes)
    assert len(grades) == 99
    health = calibration_health(grades, min_forward=100)
    assert health["graded_n"] == 99
    assert health["status"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert health["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert health["can_execute"] is False
