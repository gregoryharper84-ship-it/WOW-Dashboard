from copy import deepcopy

import pytest

from v17.llp_governed_package_scoring import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
    STALE_MODEL_OUTPUT,
    GovernedPackageError,
    assert_immutable_model_timestamp,
    classify_model_failure,
    edge_leaderboard_score,
    probability_leaderboard_score,
    validate_governed_scoring_package,
)
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
        "market_probability": 0.51,
        "unconditional_probability": p,
        "calibrated_probability": p,
        "calibrated_lower_bound": p - 0.03 if lower is None else lower,
        "calibrated_upper_bound": p + 0.03 if upper is None else upper,
        "immutable_model_timestamp": "2026-09-07T18:00:00Z",
        "model_version": "llp-test-v17",
        "calibration_method": "isotonic",
        "calibration_version": "cal-test-v1",
        "source_snapshot_id": f"snapshot-{prediction_id}",
        "source_snapshot_timestamp": "2026-09-07T17:59:00Z",
        "latest_material_update_at": "2026-09-07T17:58:00Z",
        "outcome_space": "BINARY_OUTRIGHT_WINNER",
        "failure_tags": failure_tags or [],
        "realized_failure_tags": realized_failure_tags or [],
        "final_refresh_status": "PASS",
        "snapshot_stage": snapshot_stage,
        "immutable_pregame": immutable,
        "result": result,
    }


def test_binary_scores_are_exact_and_non_mutating():
    row = _row("a", p=0.70, result="WIN")
    before = deepcopy(row)
    scored = score_prediction(row)
    assert scored is not None
    assert scored.brier_score == brier_score(0.70, 1.0)
    assert scored.log_loss == log_loss(0.70, 1.0)
    assert scored.calibrated_probability == 0.70
    assert scored.rank_eligible is True
    assert scored.scoring_allowed is True
    assert row == before
    assert scored.can_execute is False


def test_brier_and_log_loss_ignore_market_probability_and_use_governed_calibrated_probability():
    row = _row("governed-only", p=0.80, result="LOSS")
    row["market_probability"] = 0.05
    row["probability"] = 0.10
    scored = score_prediction(row)
    assert scored is not None
    assert scored.brier_score == brier_score(0.80, 0.0)
    assert scored.log_loss == log_loss(0.80, 0.0)


def test_legacy_probability_alias_cannot_substitute_for_calibrated_probability():
    row = _row("legacy-p", p=0.70, result="WIN")
    row.pop("calibrated_probability")
    row["probability"] = 0.99
    audit = validate_governed_scoring_package(row)
    assert audit.status == MODEL_OUTPUT_INVALID
    assert audit.rank_eligible is False
    assert audit.scoring_allowed is False
    with pytest.raises(GovernedPackageError) as exc:
        score_prediction(row)
    assert exc.value.code == MODEL_OUTPUT_INVALID


def test_legacy_lower_bound_alias_cannot_substitute_for_governed_lower_bound():
    row = _row("legacy-lb", p=0.70, result="WIN")
    row.pop("calibrated_lower_bound")
    row["lower_bound"] = 0.69
    audit = validate_governed_scoring_package(row)
    assert audit.status == MODEL_OUTPUT_INVALID
    assert "CALIBRATED_LOWER_BOUND_REQUIRED" in audit.blockers


def test_created_at_or_model_timestamp_cannot_substitute_for_immutable_model_timestamp():
    row = _row("mutable-time", p=0.70, result="WIN")
    row.pop("immutable_model_timestamp")
    row["model_timestamp"] = "2026-09-07T18:00:00Z"
    row["created_at"] = "2026-09-07T18:01:00Z"
    audit = validate_governed_scoring_package(row)
    assert audit.status == MODEL_OUTPUT_INVALID
    assert "IMMUTABLE_MODEL_TIMESTAMP_REQUIRED" in audit.blockers


def test_stale_immutable_model_timestamp_blocks_scoring_and_ranking():
    row = _row("stale", p=0.70, result="WIN")
    row["latest_material_update_at"] = "2026-09-07T18:00:01Z"
    audit = validate_governed_scoring_package(row)
    assert audit.status == STALE_MODEL_OUTPUT
    assert audit.rank_eligible is False
    assert audit.scoring_allowed is False
    with pytest.raises(GovernedPackageError) as exc:
        score_prediction(row)
    assert exc.value.code == STALE_MODEL_OUTPUT


def test_probability_bounds_must_be_monotonic_and_in_domain():
    row = _row("bad-bounds", p=0.70, result="WIN")
    row["calibrated_lower_bound"] = 0.75
    audit = validate_governed_scoring_package(row)
    assert audit.status == MODEL_OUTPUT_INVALID
    assert "CALIBRATED_PROBABILITY_BOUNDS_INVALID" in audit.blockers


def test_candidate_id_can_satisfy_identifier_contract():
    row = _row("candidate-only", p=0.70, result="WIN")
    row["candidate_id"] = row.pop("prediction_id")
    scored = score_prediction(row)
    assert scored is not None
    assert scored.prediction_id == "candidate-only"


def test_model_artifact_version_can_satisfy_model_version_contract():
    row = _row("artifact-version", p=0.70, result="WIN")
    row.pop("model_version")
    row["model_artifact_version"] = "llp-artifact-20260909"
    audit = validate_governed_scoring_package(row)
    assert audit.status == "PASS"


def test_probability_leaderboard_uses_calibrated_lower_bound_only():
    high_point_low_bound = _row("a", p=0.90, lower=0.60, result="WIN")
    lower_point_high_bound = _row("b", p=0.75, lower=0.70, result="WIN")
    assert probability_leaderboard_score(lower_point_high_bound) > probability_leaderboard_score(high_point_low_bound)


def test_edge_leaderboard_uses_lower_bound_minus_no_vig_minus_friction():
    row = _row("edge", p=0.75, lower=0.70, result="WIN")
    assert edge_leaderboard_score(row, no_vig_probability=0.62, friction_buffer=0.02) == pytest.approx(0.06)


def test_immutable_model_timestamp_cannot_be_overwritten():
    original = _row("immutable", p=0.70, result="WIN")
    candidate = deepcopy(original)
    candidate["immutable_model_timestamp"] = "2026-09-07T18:05:00Z"
    with pytest.raises(GovernedPackageError) as exc:
        assert_immutable_model_timestamp(original, candidate)
    assert exc.value.code == MODEL_OUTPUT_INVALID


def test_failure_taxonomy_does_not_collapse_scorer_failure_to_model_unavailable():
    assert classify_model_failure(
        capability_available=False,
        inputs_ready=False,
        model_invoked=False,
    ) == MODEL_UNAVAILABLE
    assert classify_model_failure(
        capability_available=True,
        inputs_ready=False,
        model_invoked=False,
    ) == MODEL_INPUTS_INSUFFICIENT
    assert classify_model_failure(
        capability_available=True,
        inputs_ready=True,
        model_invoked=True,
        scorer_state="TIMEOUT",
    ) == MODEL_SCORER_FAILED
    assert classify_model_failure(
        capability_available=True,
        inputs_ready=True,
        model_invoked=True,
        scorer_state="MALFORMED",
    ) == MODEL_OUTPUT_INVALID


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
