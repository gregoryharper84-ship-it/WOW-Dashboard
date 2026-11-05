"""Regression: a settled NFL tie cannot inflate the binary forward cohort.

Historical example: 2026_05_NYG_WAS 27-27. This is a synthetic
fixture testing the real grader, not a new production grade.
"""
from datetime import datetime, timezone

from v17.nfl_forward_shadow import ForwardPrediction, grade_forward_predictions, calibration_health


def _prediction():
    return ForwardPrediction(
        event_prediction_id="synthetic-pregame-tie-prediction",
        official_event_id="2026_05_NYG_WAS",
        event_start_time_utc=datetime(2026, 10, 11, 17, tzinfo=timezone.utc),
        prediction_created_at=datetime(2026, 10, 10, 17, tzinfo=timezone.utc),
        model_version="NFL_EVENT_LOGREG_PLATT_V1",
        home_team="Washington Commanders",
        away_team="New York Giants",
        selected_participant="Washington Commanders",
        calibrated_probability=0.60,
        calibrated_lower_bound=0.55,
        calibrated_upper_bound=0.65,
        model_package_valid=True,
        provenance_complete=True,
        model_probability_publishable=True,
        can_execute=False,
    )


def test_tie_with_home_win_false_is_never_a_binary_loss():
    outcome = {
        "game_id": "2026_05_NYG_WAS",
        "home_score": 27,
        "away_score": 27,
        "home_win": False,
        "tie": True,
        "locked_at": "2026-10-12T01:00:00Z",
        "can_execute": False,
    }
    grades = grade_forward_predictions([_prediction()], [outcome])
    assert grades == []
    health = calibration_health(grades, min_forward=100)
    assert health["graded_n"] == 0
    assert health["status"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert health["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert health["can_execute"] is False


def test_tie_excluded_regardless_of_home_win_boolean():
    for home_win in (True, False, None):
        outcome = {
            "game_id": "2026_05_NYG_WAS",
            "home_score": 27,
            "away_score": 27,
            "home_win": home_win,
            "tie": True,
        }
        assert grade_forward_predictions([_prediction()], [outcome]) == []
