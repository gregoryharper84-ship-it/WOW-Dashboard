from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.experiments.prop_push_calibration_target import (
    PropPushCalibrationTargetError,
    TARGET_CONDITIONAL,
    TARGET_CURRENT,
    ingest_rows,
    run_tournament,
)


BASE = datetime(2025, 1, 1, 18, tzinfo=timezone.utc)


def _row(
    i: int,
    *,
    p_win: float | None = None,
    p_push: float | None = None,
    settlement: str | None = None,
    artifact: str = "MLB_K_ARTIFACT_V1",
    direction: str = "MORE",
    market_prior_weight: float = 0.0,
):
    event_start = BASE + timedelta(days=i)
    prediction = event_start - timedelta(hours=6)
    outcome_at = event_start + timedelta(hours=5)
    push = (0.02 + (i % 5) * 0.01) if p_push is None else p_push
    win = (0.40 + (i % 20) * 0.01) if p_win is None else p_win
    if settlement is None:
        # Deterministic, non-perfect outcomes with periodic pushes.
        settlement = "PUSH" if i % 17 == 0 else "WIN" if i % 3 != 0 else "LOSS"
    return {
        "prediction_id": f"pred-{i}",
        "event_id": f"MLB:{100000 + i}",
        "player": f"Pitcher {i % 35}",
        "direction": direction,
        "line": 3.5 + (i % 5),
        "selected_side_probability": win,
        "push_probability": push,
        "settlement": settlement,
        "prediction_timestamp": prediction.isoformat(),
        "event_start_timestamp": event_start.isoformat(),
        "outcome_available_at": outcome_at.isoformat(),
        "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
        "model_artifact_version": artifact,
        "market_prior_weight": market_prior_weight,
        "market_probability_substitution_used": False,
        "void": False,
    }


def test_ingest_preserves_push_and_binary_contract_without_renormalizing_current():
    rows = ingest_rows(
        [
            _row(1, p_win=0.50, p_push=0.10, settlement="PUSH"),
            _row(2, p_win=0.50, p_push=0.10, settlement="WIN"),
        ]
    )

    assert rows[0].current_probability == pytest.approx(0.50)
    assert rows[0].conditional_probability == pytest.approx(0.50 / 0.90)
    assert rows[0].binary_outcome is None
    assert rows[1].binary_outcome == 1


def test_tournament_reuses_governed_binary_push_exclusion_and_is_research_only():
    result = run_tournament([_row(i) for i in range(360)])

    assert result.total_rows == 360
    assert result.push_rows > 0
    assert result.binary_rows == result.total_rows - result.push_rows
    assert result.training_rows >= 200
    assert result.holdout_rows >= 30

    assert result.current_contract.target == TARGET_CURRENT
    assert result.conditional_no_push.target == TARGET_CONDITIONAL
    assert result.current_contract.training_n == result.conditional_no_push.training_n
    assert result.current_contract.holdout_n == result.conditional_no_push.holdout_n
    assert result.current_contract.selected_method
    assert result.conditional_no_push.selected_method

    assert result.current_contract.raw_holdout_metrics["brier"] >= 0.0
    assert result.current_contract.calibrated_holdout_metrics["brier"] >= 0.0
    assert result.conditional_no_push.raw_holdout_metrics["brier"] >= 0.0
    assert result.conditional_no_push.calibrated_holdout_metrics["brier"] >= 0.0

    assert "HALF" in result.current_contract.holdout_line_type_metrics
    assert "WHOLE" in result.current_contract.holdout_line_type_metrics
    assert "HALF" in result.conditional_no_push.holdout_line_type_metrics
    assert "WHOLE" in result.conditional_no_push.holdout_line_type_metrics
    assert (
        result.current_contract.holdout_line_type_metrics["HALF"]["mean_push_probability"]
        == pytest.approx(0.0)
    )
    assert (
        result.current_contract.holdout_line_type_metrics["WHOLE"]["mean_push_probability"]
        > 0.0
    )

    assert result.three_way_metrics["n"] >= result.holdout_rows
    assert 0.0 <= result.three_way_metrics["push_rate"] <= 1.0
    assert len(result.evidence_sha256) == 64

    assert result.probability_publishable is False
    assert result.promotion_authorized is False
    assert result.rank_eligible is False
    assert result.can_execute is False


def test_conditional_target_differs_from_current_on_push_capable_nonpush_rows():
    rows = [_row(i, p_win=0.45 + (i % 10) * 0.01, p_push=0.10) for i in range(360)]
    result = run_tournament(rows)

    current_raw = result.current_contract.raw_holdout_metrics
    conditional_raw = result.conditional_no_push.raw_holdout_metrics
    assert current_raw != conditional_raw


def test_three_way_holdout_includes_pushes_but_binary_fits_exclude_them():
    rows = []
    for i in range(360):
        settlement = "PUSH" if i % 10 == 0 else "WIN" if i % 2 == 0 else "LOSS"
        rows.append(_row(i, p_win=0.48, p_push=0.10, settlement=settlement))

    result = run_tournament(rows)

    assert result.push_rows == 36
    assert result.binary_rows == 324
    assert result.three_way_metrics["push_rate"] > 0.0
    assert result.current_contract.training_n + result.current_contract.holdout_n <= result.binary_rows


def test_market_probability_substitution_is_hard_rejected():
    rows = [_row(i) for i in range(360)]
    rows[5]["market_prior_weight"] = 0.05

    with pytest.raises(
        PropPushCalibrationTargetError,
        match="MARKET_PROBABILITY_SUBSTITUTION_FORBIDDEN",
    ):
        run_tournament(rows)


def test_model_artifact_identity_cannot_mix():
    rows = [_row(i) for i in range(360)]
    rows[-1]["model_artifact_version"] = "DIFFERENT_ARTIFACT"

    with pytest.raises(
        PropPushCalibrationTargetError,
        match="MODEL_ARTIFACT_OR_DIRECTION_IDENTITY_MIXED",
    ):
        run_tournament(rows)


def test_direction_identity_cannot_mix():
    rows = [_row(i) for i in range(360)]
    rows[-1]["direction"] = "LESS"

    with pytest.raises(
        PropPushCalibrationTargetError,
        match="MODEL_ARTIFACT_OR_DIRECTION_IDENTITY_MIXED",
    ):
        run_tournament(rows)


def test_duplicate_exact_line_thesis_fails_closed():
    rows = [_row(i) for i in range(360)]
    duplicate = dict(rows[10])
    duplicate["prediction_id"] = "different-prediction-id"
    rows.append(duplicate)

    with pytest.raises(PropPushCalibrationTargetError, match="DUPLICATE_EXACT_LINE_THESIS"):
        run_tournament(rows)


def test_prediction_must_be_pregame_and_outcome_post_event():
    bad_prediction = _row(1)
    bad_prediction["prediction_timestamp"] = bad_prediction["event_start_timestamp"]
    with pytest.raises(PropPushCalibrationTargetError, match="PREDICTION_NOT_PREGAME"):
        ingest_rows([bad_prediction])

    bad_outcome = _row(2)
    bad_outcome["outcome_available_at"] = bad_outcome["prediction_timestamp"]
    with pytest.raises(PropPushCalibrationTargetError, match="OUTCOME_AVAILABLE_BEFORE_EVENT"):
        ingest_rows([bad_outcome])


def test_conditional_target_fails_closed_when_nonpush_mass_is_degenerate():
    rows = [_row(i) for i in range(360)]
    # A mathematically complete WIN+PUSH mass leaves no LOSS mass and produces
    # a conditional probability of exactly 1, which is not a valid Platt input
    # for this governed experiment.
    rows[1]["selected_side_probability"] = 0.90
    rows[1]["push_probability"] = 0.10

    with pytest.raises(
        PropPushCalibrationTargetError,
        match="CONDITIONAL_PROBABILITY_OUT_OF_RANGE",
    ):
        run_tournament(rows)
