import pytest

from mlb_1ip_batters_faced_shadow import (
    CAN_EXECUTE,
    grade_shadow_records,
    score_batters_faced_shadow,
)


BF_PACKAGE = {
    "model_evaluated": True,
    "model_used": "1ip_monte_carlo_event_tree_v1",
    "P_BF_3": 0.35,
    "P_BF_4": 0.35,
    "P_BF_GE_5": 0.30,
}


def test_35_more_maps_exactly_to_bf_four_or_more():
    result = score_batters_faced_shadow(
        upstream_1ip_package=BF_PACKAGE,
        line_value=3.5,
        side="MORE",
    )
    assert result["P_MORE"] == pytest.approx(0.65)
    assert result["P_LESS"] == pytest.approx(0.35)
    assert result["prob_push"] == 0.0
    assert result["selected_probability"] == pytest.approx(0.65)


def test_45_less_maps_exactly_to_bf_four_or_fewer():
    result = score_batters_faced_shadow(
        upstream_1ip_package=BF_PACKAGE,
        line_value=4.5,
        side="LESS",
    )
    assert result["P_MORE"] == pytest.approx(0.30)
    assert result["P_LESS"] == pytest.approx(0.70)
    assert result["selected_probability"] == pytest.approx(0.70)


def test_integer_four_preserves_push_mass():
    result = score_batters_faced_shadow(
        upstream_1ip_package=BF_PACKAGE,
        line_value=4.0,
        side="MORE",
    )
    assert result["P_MORE"] == pytest.approx(0.30)
    assert result["P_LESS"] == pytest.approx(0.35)
    assert result["prob_push"] == pytest.approx(0.35)


def test_55_fails_closed_because_five_plus_is_coarse_bucket():
    with pytest.raises(ValueError, match="BF_LINE_UNSUPPORTED_BY_COARSE_BUCKETS"):
        score_batters_faced_shadow(
            upstream_1ip_package=BF_PACKAGE,
            line_value=5.5,
            side="MORE",
        )


def test_missing_upstream_distribution_fails_closed():
    with pytest.raises(ValueError, match="UPSTREAM_BF_DISTRIBUTION_MISSING"):
        score_batters_faced_shadow(
            upstream_1ip_package={"model_evaluated": True},
            line_value=3.5,
            side="MORE",
        )


def test_upstream_model_not_evaluated_fails_closed():
    with pytest.raises(ValueError, match="UPSTREAM_1IP_MODEL_NOT_EVALUATED"):
        score_batters_faced_shadow(
            upstream_1ip_package={
                "model_evaluated": False,
                "P_BF_3": 0.4,
                "P_BF_4": 0.3,
                "P_BF_GE_5": 0.3,
            },
            line_value=3.5,
            side="MORE",
        )


def test_material_normalization_error_fails_closed():
    with pytest.raises(ValueError, match="UPSTREAM_BF_DISTRIBUTION_NOT_NORMALIZED"):
        score_batters_faced_shadow(
            upstream_1ip_package={
                "model_evaluated": True,
                "P_BF_3": 0.2,
                "P_BF_4": 0.2,
                "P_BF_GE_5": 0.2,
            },
            line_value=3.5,
            side="MORE",
        )


def test_transport_rounding_is_normalized_within_tolerance_only():
    result = score_batters_faced_shadow(
        upstream_1ip_package={
            "model_evaluated": True,
            "P_BF_3": 0.3333,
            "P_BF_4": 0.3333,
            "P_BF_GE_5": 0.3333,
        },
        line_value=4.5,
        side="MORE",
    )
    assert result["P_BF_3"] + result["P_BF_4"] + result["P_BF_GE_5"] == pytest.approx(1.0, abs=2e-6)


def test_shadow_output_cannot_claim_calibration_or_rank_eligibility():
    result = score_batters_faced_shadow(
        upstream_1ip_package=BF_PACKAGE,
        line_value=3.5,
        side="LESS",
    )
    assert result["calibrated_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["calibration_status"] == "SHADOW_UNCALIBRATED"
    assert result["terminal_label"] == "RESEARCH_INTEREST"
    assert result["rank_eligible"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_shadow_grading_reports_brier_and_log_loss_without_promotion_claim():
    result = grade_shadow_records(
        [
            {"selected_probability": 0.70, "outcome": 1},
            {"selected_probability": 0.60, "outcome": 0},
            {"selected_probability": 0.80, "outcome": 1},
        ]
    )
    assert result["n"] == 3
    assert result["observed_hit_rate"] == pytest.approx(2 / 3)
    assert result["brier"] > 0.0
    assert result["log_loss"] > 0.0
    assert result["calibration_claim_allowed"] is False
    assert result["promotion_status"] == "VALIDATION_REQUIRED"
    assert result["can_execute"] is False


def test_non_binary_shadow_outcome_is_rejected():
    with pytest.raises(ValueError, match="BF_SHADOW_OUTCOME_MUST_BE_BINARY"):
        grade_shadow_records([{"selected_probability": 0.60, "outcome": "PUSH"}])


def test_can_execute_false_is_global_invariant():
    assert CAN_EXECUTE is False
