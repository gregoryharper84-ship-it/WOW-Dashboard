import random

from mlb_1ip_specialist import (
    classify_lineup_evidence,
    score_mlb_1ip,
    simulate_1ip_event_tree,
    starter_changed,
)


def _top_four(n: int) -> list[dict]:
    return [
        {"player": f"Batter {i}", "handedness": "R", "p_pa_vs_pitcher_profile": 4.0 + 0.1 * i}
        for i in range(n)
    ]


BF_DIST = {"p_bf_3": 0.35, "p_bf_4": 0.35, "p_bf_gte5": 0.30}
BASELINE_PPB = {"mean": 4.2, "std": 1.1}


def test_official_confirmed_lineup_is_standard_uncertainty():
    state, completeness, reasons = classify_lineup_evidence(
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=None,
    )
    assert state == "OFFICIAL_CONFIRMED"
    assert completeness == "FULL"
    assert reasons == []


def test_lineup_tbd_with_full_projected_top_four_is_reconstructed():
    state, completeness, reasons = classify_lineup_evidence(
        starter_status="CONFIRMED",
        official_lineup_status="TBD",
        projected_top_four=_top_four(4),
    )
    assert state == "PROJECTED_OR_RECONSTRUCTED"
    assert completeness == "FULL"


def test_lineup_tbd_with_partial_but_sufficient_top_four():
    state, completeness, reasons = classify_lineup_evidence(
        starter_status="CONFIRMED",
        official_lineup_status="TBD",
        projected_top_four=_top_four(3),
    )
    assert state == "PROJECTED_OR_RECONSTRUCTED"
    assert completeness == "PARTIAL_SUFFICIENT"


def test_lineup_tbd_with_unreconstructable_batters_is_insufficient():
    state, completeness, reasons = classify_lineup_evidence(
        starter_status="CONFIRMED",
        official_lineup_status="TBD",
        projected_top_four=_top_four(2),
    )
    assert state == "INSUFFICIENT_TO_RECONSTRUCT"
    assert "PROJECTED_TOP_FOUR_UNOBTAINABLE" in reasons


def test_lineup_tbd_alone_with_no_reconstruction_attempt_is_insufficient_not_unavailable():
    """LINEUP_TBD alone must never resolve to MODEL_UNAVAILABLE -- it resolves
    to INSUFFICIENT_TO_RECONSTRUCT (-> REJECT_DATA_QUALITY at the score_mlb_1ip
    layer), a distinct, honest code."""
    state, _, reasons = classify_lineup_evidence(
        starter_status="CONFIRMED",
        official_lineup_status="TBD",
        projected_top_four=None,
    )
    assert state == "INSUFFICIENT_TO_RECONSTRUCT"
    assert state != "MODEL_UNAVAILABLE"


def test_starter_not_confirmed_with_lineup_tbd_is_insufficient():
    state, _, reasons = classify_lineup_evidence(
        starter_status="PROBABLE",
        official_lineup_status="TBD",
        projected_top_four=_top_four(4),
    )
    assert state == "INSUFFICIENT_TO_RECONSTRUCT"
    assert "STARTER_NOT_CONFIRMED" in reasons


def test_starter_changed_detects_a_stale_starter():
    assert starter_changed("Jane Doe", "John Roe") is True
    assert starter_changed("Jane Doe", "jane   doe") is False
    assert starter_changed(None, "John Roe") is False


def test_simulate_1ip_event_tree_reports_full_skill_contract_fields():
    random.seed(1234)
    result = simulate_1ip_event_tree(
        bf_distribution=BF_DIST,
        pitches_per_batter_dist=BASELINE_PPB,
        line_value=13.5,
        side="MORE",
        n_trials=25000,
    )
    required = {
        "P_BF_3", "P_BF_4", "P_BF_GE_5", "P_MORE_GIVEN_BF_3", "P_MORE_GIVEN_BF_GE_4",
        "P_MORE", "P_LESS", "prob_push", "fourth_batter_dependence_share",
        "projection_mean", "projection_median", "projection_std",
        "lower_bound", "upper_bound", "simulation_count",
    }
    assert required.issubset(result.keys())
    assert result["simulation_count"] >= 25000
    assert 0.0 <= result["P_MORE"] <= 1.0
    assert 0.0 <= result["P_LESS"] <= 1.0
    assert result["can_execute"] is False


def test_lineup_tbd_plus_projected_top4_available_reaches_specialist():
    random.seed(1)
    result = score_mlb_1ip(
        starter_status="CONFIRMED",
        official_lineup_status="TBD",
        projected_top_four=_top_four(4),
        pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB,
        line_value=13.5,
        side="MORE",
    )
    assert result["model_evaluated"] is True
    assert result["lineup_evidence_state"] == "PROJECTED_OR_RECONSTRUCTED"
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["uncertainty_widening_applied"] is True
    assert result["final_refresh_required"] is True
    assert result["terminal_label"] != "MODEL_UNAVAILABLE"
    assert result["can_execute"] is False


def test_lineup_tbd_alone_cannot_produce_model_unavailable():
    random.seed(2)
    result = score_mlb_1ip(
        starter_status="CONFIRMED",
        official_lineup_status="TBD",
        projected_top_four=None,
        pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB,
        line_value=13.5,
        side="MORE",
    )
    assert result["terminal_label"] != "MODEL_UNAVAILABLE"
    assert result["terminal_label"] == "REJECT_DATA_QUALITY"
    assert result["code"] == "MANDATORY_EVENT_TREE_INPUTS_UNOBTAINABLE_AFTER_APPROVED_ATTEMPTS"
    assert result["model_evaluated"] is False


def test_partial_top_four_widens_uncertainty_more_than_full_top_four():
    full = score_mlb_1ip(
        starter_status="CONFIRMED", official_lineup_status="TBD",
        projected_top_four=_top_four(4), pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB, line_value=13.5, side="MORE",
    )
    partial = score_mlb_1ip(
        starter_status="CONFIRMED", official_lineup_status="TBD",
        projected_top_four=_top_four(3), pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB, line_value=13.5, side="MORE",
    )
    assert partial["uncertainty_widening_factor"] > full["uncertainty_widening_factor"]
    confirmed = score_mlb_1ip(
        starter_status="CONFIRMED", official_lineup_status="CONFIRMED",
        projected_top_four=None, pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB, line_value=13.5, side="MORE",
    )
    assert confirmed["uncertainty_widening_factor"] < full["uncertainty_widening_factor"]


def test_truly_unreconstructable_inputs_return_data_quality_blocker_not_fake_model_failure():
    random.seed(3)
    result = score_mlb_1ip(
        starter_status="PROBABLE",
        official_lineup_status="TBD",
        projected_top_four=_top_four(1),
        pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB,
        line_value=13.5,
        side="MORE",
    )
    assert result["terminal_label"] == "REJECT_DATA_QUALITY"
    assert result["model_evaluated"] is False
    assert "STARTER_NOT_CONFIRMED" in result["blockers"]


def test_missing_market_evidence_cannot_erase_completed_sporting_probability():
    random.seed(4)
    result = score_mlb_1ip(
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=None,
        pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB,
        line_value=13.5,
        side="MORE",
        market_evidence_present=False,
    )
    assert result["model_evaluated"] is True
    assert "P_MORE" in result and result["P_MORE"] is not None
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert "MARKET_DATA_UNAVAILABLE" in result["blockers"]


def test_material_unresolved_failure_path_prior_is_reported_and_widens_uncertainty():
    random.seed(5)
    baseline = score_mlb_1ip(
        starter_status="CONFIRMED", official_lineup_status="CONFIRMED",
        projected_top_four=None, pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB, line_value=13.5, side="MORE",
    )
    with_unresolved_failure_path = score_mlb_1ip(
        starter_status="CONFIRMED", official_lineup_status="CONFIRMED",
        projected_top_four=None, pitcher_bf_distribution=BF_DIST,
        baseline_pitches_per_batter=BASELINE_PPB, line_value=13.5, side="MORE",
        failure_path_prior={"status": "MATERIAL_UNRESOLVED"},
    )
    assert "PITCHER_FAILURE_PATH_PRIOR_UNRESOLVED" in with_unresolved_failure_path["blockers"]
    assert with_unresolved_failure_path["model_evaluated"] is True
    assert baseline["terminal_label"] == with_unresolved_failure_path["terminal_label"] == "MODEL_QUALIFIED_HOLD"


def test_can_execute_false_is_invariant_across_every_path():
    paths = [
        score_mlb_1ip(
            starter_status="CONFIRMED", official_lineup_status="CONFIRMED",
            projected_top_four=None, pitcher_bf_distribution=BF_DIST,
            baseline_pitches_per_batter=BASELINE_PPB, line_value=13.5, side="MORE",
        ),
        score_mlb_1ip(
            starter_status="CONFIRMED", official_lineup_status="TBD",
            projected_top_four=_top_four(4), pitcher_bf_distribution=BF_DIST,
            baseline_pitches_per_batter=BASELINE_PPB, line_value=13.5, side="LESS",
        ),
        score_mlb_1ip(
            starter_status="PROBABLE", official_lineup_status="TBD",
            projected_top_four=None, pitcher_bf_distribution=BF_DIST,
            baseline_pitches_per_batter=BASELINE_PPB, line_value=13.5, side="MORE",
        ),
    ]
    for result in paths:
        assert result["can_execute"] is False
        assert result["probability_publishable"] is False
