"""Balanced forward-cohort reconciliation receipts.

These tests pin the distinction the restoration audit depends on: a settled
tie and an unsettled game are *accounted for* events, not missing eligible
grades. A contradictory or incomplete settlement is never treated as a tie,
and a present outcome with missing scores is not treated as un-ingested.
Reconciliation is diagnostic only; it cannot create a grade.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math

import pytest

from v17.nfl_forward_shadow import (
    ForwardGrade,
    ForwardPrediction,
    NFLForwardShadowError,
    calibration_health,
    grade_forward_predictions,
    reconcile_forward_cohort,
)


START = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)


def _prediction(
    event_id: str,
    *,
    index: int = 0,
    p: float = 0.60,
    lower: float = 0.55,
    upper: float = 0.65,
) -> ForwardPrediction:
    return ForwardPrediction(
        event_prediction_id=f"00000000-0000-0000-0000-{index:012d}",
        official_event_id=event_id,
        event_start_time_utc=START + timedelta(days=index),
        prediction_created_at=START + timedelta(days=index, hours=-2),
        model_version="NFL_EVENT_LOGREG_PLATT_V1_test",
        home_team="BBB",
        away_team="AAA",
        selected_participant="BBB",
        calibrated_probability=p,
        calibrated_lower_bound=lower,
        calibrated_upper_bound=upper,
        model_package_valid=True,
        provenance_complete=True,
        model_probability_publishable=True,
        can_execute=False,
    )


def _outcome(event_id: str, *, home_win: bool | None, tie: bool = False) -> dict:
    home_score = 27 if tie else (24 if home_win else 17)
    away_score = 27 if tie else (17 if home_win else 24)
    return {
        "game_id": event_id,
        "home_score": home_score,
        "away_score": away_score,
        "home_win": home_win,
        "tie": tie,
    }


def _grade(
    prediction: ForwardPrediction,
    *,
    outcome: int = 1,
    event_prediction_id: str | None = None,
    model_version: str | None = None,
    selected_participant: str | None = None,
) -> ForwardGrade:
    p = prediction.calibrated_probability
    return ForwardGrade(
        event_prediction_id=event_prediction_id or prediction.event_prediction_id,
        official_event_id=prediction.official_event_id,
        event_start_time_utc=prediction.event_start_time_utc.isoformat(),
        prediction_created_at=prediction.prediction_created_at.isoformat(),
        model_version=model_version or prediction.model_version,
        selected_participant=selected_participant or prediction.selected_participant,
        calibrated_probability=p,
        calibrated_lower_bound=prediction.calibrated_lower_bound,
        calibrated_upper_bound=prediction.calibrated_upper_bound,
        outcome=outcome,
        brier=(p - outcome) ** 2,
        log_loss=-math.log(p) if outcome == 1 else -math.log(1.0 - p),
        source_game_id=prediction.official_event_id,
    )


def test_tie_and_pending_events_are_accounted_for_without_becoming_grades():
    graded_event = _prediction("2026_06_AAA_BBB", index=0)
    tied_event = _prediction("2026_05_NYG_WAS", index=1)
    pending_event = _prediction("2026_09_JAX_BAL", index=2)
    predictions = [graded_event, tied_event, pending_event]
    outcomes = [
        _outcome(graded_event.official_event_id, home_win=True),
        _outcome(tied_event.official_event_id, home_win=False, tie=True),
        # No outcome row at all for the pending event.
    ]

    grades = grade_forward_predictions(predictions, outcomes)
    assert [grade.official_event_id for grade in grades] == [
        graded_event.official_event_id
    ]

    receipt = reconcile_forward_cohort(predictions, outcomes, grades)
    assert receipt["schema_version"] == "NFL_FORWARD_COHORT_RECONCILIATION_V1"
    assert receipt["canonical_events"] == 3
    assert receipt["graded"] == 1
    assert receipt["outcome_tie_excluded"] == 1
    assert receipt["outcome_pending"] == 1
    assert receipt["outcome_incomplete"] == 0
    assert receipt["balanced"] is True
    assert receipt["can_execute"] is False
    assert receipt["ungraded_events"] == [
        {"official_event_id": "2026_05_NYG_WAS", "reason": "OUTCOME_TIE"},
        {"official_event_id": "2026_09_JAX_BAL", "reason": "OUTCOME_PENDING"},
    ]


def test_outcome_without_usable_winner_is_incomplete_not_graded():
    prediction = _prediction("2026_06_AAA_BBB")
    outcome = _outcome("2026_06_AAA_BBB", home_win=None, tie=False)
    outcome["home_score"] = 24
    outcome["away_score"] = 17

    assert grade_forward_predictions([prediction], [outcome]) == []
    receipt = reconcile_forward_cohort([prediction], [outcome], [])
    assert receipt["outcome_incomplete"] == 1
    assert receipt["outcome_pending"] == 0
    assert receipt["ungraded_events"] == [
        {"official_event_id": "2026_06_AAA_BBB", "reason": "OUTCOME_INCOMPLETE"}
    ]


@pytest.mark.parametrize(
    ("home_score", "away_score", "home_win", "tie"),
    [
        pytest.param(24, 17, True, True, id="contradictory-tie-flag"),
        pytest.param(24, 17, False, False, id="winner-flag-contradicts-score"),
        pytest.param(27, 27, False, False, id="equal-score-tie-flag-false"),
        pytest.param(27, 27, True, True, id="equal-score-winner-flag-true"),
        pytest.param(None, 17, None, False, id="missing-score"),
    ],
)
def test_inconsistent_settlement_is_incomplete_not_tie_or_pending(
    home_score, away_score, home_win, tie
):
    prediction = _prediction("2026_06_AAA_BBB")
    outcome = {
        "game_id": prediction.official_event_id,
        "home_score": home_score,
        "away_score": away_score,
        "home_win": home_win,
        "tie": tie,
    }

    assert grade_forward_predictions([prediction], [outcome]) == []
    receipt = reconcile_forward_cohort([prediction], [outcome], [])
    assert receipt["outcome_tie_excluded"] == 0
    assert receipt["outcome_pending"] == 0
    assert receipt["outcome_incomplete"] == 1
    assert receipt["ungraded_events"] == [
        {
            "official_event_id": prediction.official_event_id,
            "reason": "OUTCOME_INCOMPLETE",
        }
    ]


def test_reversed_binary_outcomes_grade_the_selected_side_correctly():
    predictions = [
        _prediction("2026_06_home_wins", index=0),
        _prediction("2026_06_away_wins", index=1),
        replace(
            _prediction("2026_06_away_selected_home_wins", index=2),
            selected_participant="AAA",
        ),
        replace(
            _prediction("2026_06_away_selected_away_wins", index=3),
            selected_participant="AAA",
        ),
    ]
    outcomes = [
        _outcome("2026_06_home_wins", home_win=True),
        _outcome("2026_06_away_wins", home_win=False),
        _outcome("2026_06_away_selected_home_wins", home_win=True),
        _outcome("2026_06_away_selected_away_wins", home_win=False),
    ]

    grades = grade_forward_predictions(predictions, outcomes)
    assert [(grade.official_event_id, grade.outcome) for grade in grades] == [
        ("2026_06_home_wins", 1),
        ("2026_06_away_wins", 0),
        ("2026_06_away_selected_home_wins", 0),
        ("2026_06_away_selected_away_wins", 1),
    ]


def test_grade_for_validated_tie_fails_closed():
    prediction = _prediction("2026_05_NYG_WAS")
    outcome = _outcome(prediction.official_event_id, home_win=False, tie=True)
    grade = _grade(prediction, outcome=0)
    with pytest.raises(
        NFLForwardShadowError, match="RECONCILIATION_GRADE_OUTCOME_MISMATCH"
    ):
        reconcile_forward_cohort([prediction], [outcome], [grade])


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (
            "event_prediction_id",
            "00000000-0000-0000-0000-999999999999",
            "RECONCILIATION_GRADE_PREDICTION_MISMATCH",
        ),
        ("model_version", "OTHER_MODEL", "RECONCILIATION_GRADE_MODEL_MISMATCH"),
        ("selected_participant", "AAA", "RECONCILIATION_GRADE_SELECTION_MISMATCH"),
    ],
)
def test_grade_identity_mismatch_fails_closed(field, value, error):
    prediction = _prediction("2026_06_AAA_BBB")
    outcome = _outcome(prediction.official_event_id, home_win=True)
    grade = _grade(prediction, **{field: value})
    with pytest.raises(NFLForwardShadowError, match=error):
        reconcile_forward_cohort([prediction], [outcome], [grade])


def test_disposition_buckets_sum_to_canonical_events():
    predictions = [
        _prediction("2026_06_graded", index=0),
        _prediction("2026_05_tie", index=1),
        _prediction("2026_09_pending", index=2),
        _prediction("2026_09_incomplete", index=3),
    ]
    outcomes = [
        _outcome("2026_06_graded", home_win=True),
        _outcome("2026_05_tie", home_win=False, tie=True),
        {
            "game_id": "2026_09_incomplete",
            "home_score": None,
            "away_score": 17,
            "home_win": None,
            "tie": False,
        },
    ]

    grades = grade_forward_predictions(predictions, outcomes)
    receipt = reconcile_forward_cohort(predictions, outcomes, grades)
    assert receipt["canonical_events"] == 4
    assert receipt["graded"] == 1
    assert receipt["outcome_tie_excluded"] == 1
    assert receipt["outcome_pending"] == 1
    assert receipt["outcome_incomplete"] == 1
    assert receipt["balanced"] is True
    assert (
        receipt["graded"]
        + receipt["outcome_tie_excluded"]
        + receipt["outcome_pending"]
        + receipt["outcome_incomplete"]
    ) == receipt["canonical_events"]


def test_reconciliation_does_not_change_health_denominator():
    predictions = [
        _prediction(f"2026_06_game_{i}", index=i, p=0.70, lower=0.60, upper=0.78)
        for i in range(100)
    ]
    outcomes = [
        _outcome(prediction.official_event_id, home_win=(i < 70))
        for i, prediction in enumerate(predictions)
    ]
    outcomes.append(
        _outcome("2026_05_NYG_WAS", home_win=False, tie=True)
    )
    predictions.append(
        _prediction("2026_05_NYG_WAS", index=100, p=0.70, lower=0.60, upper=0.78)
    )

    grades = grade_forward_predictions(predictions, outcomes)
    receipt = reconcile_forward_cohort(predictions, outcomes, grades)
    health = calibration_health(grades, min_forward=100)

    assert receipt["graded"] == 100
    assert receipt["outcome_tie_excluded"] == 1
    assert receipt["canonical_events"] == 101
    assert health["graded_n"] == receipt["graded"] == len(grades)
    assert health["status"] == "PASS"
    assert health["can_execute"] is False


def test_duplicate_prediction_event_fails_closed():
    prediction = _prediction("2026_06_AAA_BBB")
    with pytest.raises(NFLForwardShadowError, match="RECONCILIATION_DUPLICATE_EVENT"):
        reconcile_forward_cohort([prediction, prediction], [], [])


def test_duplicate_grade_event_fails_closed():
    prediction = _prediction("2026_06_AAA_BBB")
    grade = ForwardGrade(
        event_prediction_id=prediction.event_prediction_id,
        official_event_id=prediction.official_event_id,
        event_start_time_utc=prediction.event_start_time_utc.isoformat(),
        prediction_created_at=prediction.prediction_created_at.isoformat(),
        model_version=prediction.model_version,
        selected_participant=prediction.selected_participant,
        calibrated_probability=0.60,
        calibrated_lower_bound=0.55,
        calibrated_upper_bound=0.65,
        outcome=1,
        brier=(0.60 - 1.0) ** 2,
        log_loss=-math.log(0.60),
        source_game_id=prediction.official_event_id,
    )
    with pytest.raises(NFLForwardShadowError, match="RECONCILIATION_DUPLICATE_GRADE"):
        reconcile_forward_cohort([prediction], [], [grade, grade])


def test_grade_without_its_canonical_prediction_fails_closed():
    prediction = _prediction("2026_06_AAA_BBB")
    grade = ForwardGrade(
        event_prediction_id="00000000-0000-0000-0000-999999999999",
        official_event_id="2026_99_ORPHAN",
        event_start_time_utc=prediction.event_start_time_utc.isoformat(),
        prediction_created_at=prediction.prediction_created_at.isoformat(),
        model_version=prediction.model_version,
        selected_participant=prediction.selected_participant,
        calibrated_probability=0.60,
        calibrated_lower_bound=0.55,
        calibrated_upper_bound=0.65,
        outcome=1,
        brier=(0.60 - 1.0) ** 2,
        log_loss=-math.log(0.60),
        source_game_id="2026_99_ORPHAN",
    )
    with pytest.raises(
        NFLForwardShadowError, match="RECONCILIATION_GRADE_WITHOUT_PREDICTION"
    ):
        reconcile_forward_cohort([prediction], [], [grade])


def test_run_forward_shadow_emits_reconciliation_and_persists_only_real_grades(
    monkeypatch,
):
    from v17 import nfl_forward_shadow as shadow

    graded_event = _prediction("2026_06_AAA_BBB", index=0)
    tied_event = _prediction("2026_05_NYG_WAS", index=1)
    pending_event = _prediction("2026_09_JAX_BAL", index=2)
    predictions = [graded_event, tied_event, pending_event]
    outcomes = [
        _outcome(graded_event.official_event_id, home_win=True),
        _outcome(tied_event.official_event_id, home_win=False, tie=True),
    ]

    persisted: list = []
    health_rows: list = []
    monkeypatch.setattr(shadow, "load_prediction_rows", lambda _: [])
    monkeypatch.setattr(
        shadow, "select_canonical_forward_predictions", lambda _: predictions
    )
    monkeypatch.setattr(shadow, "load_outcome_rows", lambda _: outcomes)
    monkeypatch.setattr(
        shadow,
        "persist_new_grades",
        lambda _, grades: persisted.extend(grades) or len(grades),
    )
    monkeypatch.setattr(
        shadow, "persist_health", lambda _, health: health_rows.append(health)
    )

    result = shadow.run_forward_shadow(
        object(),
        settlement_refresh_fn=lambda _: {
            "status": "COMPLETED",
            "seasons": [],
            "can_execute": False,
        },
    )

    assert [grade.official_event_id for grade in persisted] == [
        graded_event.official_event_id
    ]
    assert result["graded_events"] == 1
    assert result["cohort_reconciliation"]["graded"] == 1
    assert result["cohort_reconciliation"]["outcome_tie_excluded"] == 1
    assert result["cohort_reconciliation"]["outcome_pending"] == 1
    assert result["cohort_reconciliation"]["balanced"] is True
    assert result["calibration_health"]["graded_n"] == 1
    assert result["calibration_health"]["can_execute"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert len(health_rows) == 1
    assert health_rows[0]["can_execute"] is False
