from copy import deepcopy

from v17.llp_postmortem_recalibration import (
    LearningThresholds,
    brier_score,
    build_llp_learning_report,
    calibration_report,
    failure_path_diagnostics,
    final_refresh_diagnostics,
    log_loss,
    market_prior_diagnostics,
    ranking_regret_diagnostics,
    score_prediction,
)


def _row(
    prediction_id: str,
    *,
    p: float,
    result: str,
    lower: float | None = None,
    upper: float | None = None,
    role: str = "FAVORITE",
    sport: str = "MLB",
    date: str = "2026-09-07",
    event_id: str | None = None,
    market_weight: float = 0.10,
    immutable: bool = True,
    snapshot_stage: str = "FINAL_IMMUTABLE_PREGAME",
    failure_tags=None,
    realized_failure_tags=None,
):
    return {
        "prediction_id": prediction_id,
        "date": date,
        "sport": sport,
        "league": sport,
        "event_id": event_id or prediction_id,
        "market_role": role,
        "selection": prediction_id,
        "independent_probability": p,
        "market_prior_weight": market_weight,
        "unconditional_probability": p,
        "calibrated_probability": p,
        "lower_bound": p - 0.03 if lower is None else lower,
        "upper_bound": p + 0.03 if upper is None else upper,
        "failure_tags": failure_tags or [],
        "realized_failure_tags": realized_failure_tags or [],
        "final_refresh_status": "PASS",
        "snapshot_stage": snapshot_stage,
        "immutable_pregame": immutable,
        "model_timestamp": "2026-09-07T18:00:00Z",
        "result": result,
    }


def test_binary_scores_are_exact_and_non_mutating():
    row = _row("a", p=0.70, result="WIN")
    before = deepcopy(row)
    scored = score_prediction(row)
    assert scored is not None
    assert scored.brier_score == brier_score(0.70, 1.0)
    assert scored.log_loss == log_loss(0.70, 1.0)
    assert row == before
    assert scored.can_execute is False


def test_favorite_and_upset_calibration_are_never_pooled():
    rows = []
    rows.extend(_row(f"fav-{i}", p=0.70, result="WIN" if i < 7 else "LOSS") for i in range(10))
    rows.extend(
        _row(f"dog-{i}", p=0.40, result="WIN" if i < 4 else "LOSS", role="UPSET")
        for i in range(10)
    )
    report = calibration_report(rows, config=LearningThresholds(min_sample_size=5))
    keys = {(g["sport"], g["market_role"]) for g in report["groups"]}
    assert keys == {("MLB", "FAVORITE"), ("MLB", "UPSET")}
    favorite = next(g for g in report["groups"] if g["market_role"] == "FAVORITE")
    upset = next(g for g in report["groups"] if g["market_role"] == "UPSET")
    assert abs(favorite["observed_win_rate"] - 0.70) < 1e-12
    assert abs(upset["observed_win_rate"] - 0.40) < 1e-12


def test_well_calibrated_sample_preserves_current_calibration_when_thresholds_are_supplied():
    rows = [_row(f"p-{i}", p=0.70, result="WIN" if i < 14 else "LOSS") for i in range(20)]
    config = LearningThresholds(
        min_sample_size=20,
        max_abs_calibration_bias=0.05,
        max_ece=0.05,
        max_mean_brier=0.25,
        max_lower_bound_overstatement=0.05,
    )
    group = calibration_report(rows, config=config)["groups"][0]
    assert group["review_status"] == "PRESERVE_CURRENT_CALIBRATION"
    assert group["automatic_parameter_mutation"] is False


def test_systematic_bias_recommends_review_but_never_mutates_parameters():
    rows = [_row(f"p-{i}", p=0.75, result="WIN" if i < 10 else "LOSS") for i in range(20)]
    config = LearningThresholds(min_sample_size=20, max_abs_calibration_bias=0.05, max_ece=0.05)
    group = calibration_report(rows, config=config)["groups"][0]
    assert group["review_status"] == "RECALIBRATION_REVIEW_RECOMMENDED"
    assert "ABS_CALIBRATION_BIAS" in group["review_reasons"]
    assert group["automatic_parameter_mutation"] is False


def test_small_sample_cannot_trigger_numeric_recalibration_review():
    rows = [_row("one", p=0.90, result="LOSS")]
    config = LearningThresholds(min_sample_size=20, max_abs_calibration_bias=0.01)
    group = calibration_report(rows, config=config)["groups"][0]
    assert group["review_status"] == "INSUFFICIENT_SAMPLE"


def test_market_prior_above_half_is_flagged_without_probability_penalty():
    row = _row("market-heavy", p=0.68, result="WIN", market_weight=0.60)
    before = deepcopy(row)
    report = market_prior_diagnostics([row])
    assert report["market_dependent_prediction_ids"] == ["market-heavy"]
    assert report["market_dependency_threshold"] == 0.50
    assert row == before
    assert report["probability_fields_mutated"] is False


def test_realized_failure_path_tracks_modeled_loss_regime():
    row = _row(
        "loss",
        p=0.65,
        result="LOSS",
        failure_tags=["BULLPEN_COLLAPSE", "LATE_GAME_VARIANCE"],
        realized_failure_tags=["BULLPEN_COLLAPSE"],
    )
    report = failure_path_diagnostics([row])
    assert report["losses"] == 1
    assert report["losses_with_realized_path"] == 1
    assert report["realized_path_match_rate"] == 1.0
    assert report["unanticipated_failure_prediction_ids"] == []


def test_lower_bound_ranking_regret_is_diagnostic_and_does_not_rewrite_rank():
    rows = [
        _row("top", p=0.72, lower=0.68, result="LOSS", event_id="event-a"),
        _row("second", p=0.67, lower=0.63, result="WIN", event_id="event-b"),
    ]
    report = ranking_regret_diagnostics(rows)
    assert report["top_rank_losses"] == 1
    assert report["rank_inversions"][0]["top_rank_prediction_id"] == "top"
    assert report["rank_inversions"][0]["lower_rank_winner_prediction_id"] == "second"
    assert report["historical_rank_rewritten"] is False


def test_refresh_comparison_uses_final_immutable_snapshot_for_official_grade_only():
    pre = _row(
        "refresh",
        p=0.55,
        result="WIN",
        immutable=False,
        snapshot_stage="PRE_REFRESH",
    )
    final = _row(
        "refresh",
        p=0.70,
        result="WIN",
        immutable=True,
        snapshot_stage="FINAL_IMMUTABLE_PREGAME",
    )
    report = final_refresh_diagnostics([pre, final])
    assert report["improved"] == 1
    assert report["comparisons"][0]["official_grade_uses_final_snapshot_only"] is True
    assert score_prediction(pre) is None
    assert score_prediction(final) is not None


def test_complete_report_never_mutates_predictions_or_authorizes_execution():
    rows = [
        _row("a", p=0.70, result="WIN"),
        _row("b", p=0.66, result="LOSS", failure_tags=["STARTER_FAILURE"], realized_failure_tags=["STARTER_FAILURE"]),
    ]
    before = deepcopy(rows)
    report = build_llp_learning_report(rows, config=LearningThresholds(min_sample_size=2))
    assert rows == before
    assert report["probability_or_prediction_rows_mutated"] is False
    assert report["automatic_parameter_mutation"] is False
    assert report["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert report["can_execute"] is False
