from __future__ import annotations

import pytest

from v17.experiments.lower_bound_v2_multilane import (
    CAN_EXECUTE,
    MARKET_PROBABILITY_SUBSTITUTION_ALLOWED,
    PROBABILITY_PUBLISHABLE,
    PROMOTION_AUTHORIZED,
    RANK_ELIGIBLE,
    LowerBoundExperimentError,
    ResearchEligibilityPolicy,
    composite_lower_bound,
    evaluate_bound_reliability,
    evaluate_research_eligibility,
    local_reliability_bound,
    required_evidence_for_family,
    research_manifest,
    run_policy_ablation,
)


def _row(
    *,
    family="PLAYER_PROP",
    lane_key="NFL_RECEIVING_YARDS_MORE",
    p=0.62,
    lb=0.56,
    ub=0.68,
    outcome=1,
    ood_state="IN_DISTRIBUTION",
    support_n=80,
    support_distance=0.2,
    push_possible=False,
    probability_semantics="BINARY_OUTCOME",
    push_probability=None,
    lane_specific_gate_pass=True,
):
    return {
        "family": family,
        "lane_key": lane_key,
        "calibrated_probability": p,
        "calibrated_lower_bound": lb,
        "calibrated_upper_bound": ub,
        "outcome": outcome,
        "ood_state": ood_state,
        "support_n": support_n,
        "support_distance": support_distance,
        "push_possible": push_possible,
        "probability_semantics": probability_semantics,
        "push_probability": push_probability,
        "lane_specific_gate_pass": lane_specific_gate_pass,
        "upstream_hard_blockers": [],
        "market_probability_substitution_used": False,
    }


def test_manifest_is_multilane_and_fail_closed():
    manifest = research_manifest()

    assert set(manifest["supported_families"]) == {"MONEYLINE", "SPREAD", "PLAYER_PROP"}
    assert manifest["lane_specific_probability_math_required"] is True
    assert manifest["market_probability_substitution_allowed"] is False
    assert manifest["probability_publishable"] is False
    assert manifest["promotion_authorized"] is False
    assert manifest["rank_eligible"] is False
    assert manifest["can_execute"] is False

    assert PROBABILITY_PUBLISHABLE is False
    assert PROMOTION_AUTHORIZED is False
    assert RANK_ELIGIBLE is False
    assert CAN_EXECUTE is False
    assert MARKET_PROBABILITY_SUBSTITUTION_ALLOWED is False


def test_spread_and_prop_contracts_require_exact_line_and_push_semantics():
    spread = required_evidence_for_family("spread")
    prop = required_evidence_for_family("player_prop")

    assert "exact_line_identity" in spread
    assert "margin_distribution_support" in spread
    assert "push_semantics" in spread
    assert "material_line_change_rescore" in spread

    assert "player_identity" in prop
    assert "stat_family_identity" in prop
    assert "exact_line_identity" in prop
    assert "role_status" in prop
    assert "push_semantics" in prop


def test_push_capable_binary_row_requires_conditional_no_push_semantics():
    row = _row(
        family="SPREAD",
        lane_key="NFL_SPREAD_HOME",
        push_possible=True,
        probability_semantics="BINARY_OUTCOME",
    )
    with pytest.raises(LowerBoundExperimentError, match="PUSH_CAPABLE"):
        evaluate_bound_reliability([row])


def test_local_reliability_bound_uses_exact_lane_locality_and_ood_policy():
    rows = []
    for i in range(60):
        rows.append(
            _row(
                p=0.58 + (i % 7) / 100.0,
                outcome=1 if i < 42 else 0,
                support_distance=0.1 + (i % 5) / 100.0,
            )
        )

    # These must not contaminate the local cohort.
    rows.extend(
        [
            _row(lane_key="NFL_RUSHING_YARDS_MORE", p=0.62, outcome=1),
            _row(p=0.62, outcome=1, ood_state="OOD_BLOCKED"),
            _row(p=0.62, outcome=1, ood_state="NEAR_OOD"),
            _row(p=0.90, outcome=1),
        ]
    )

    evidence = local_reliability_bound(
        lane_key="NFL_RECEIVING_YARDS_MORE",
        point_probability=0.62,
        historical_rows=rows,
        min_effective_n=30,
        max_neighbors=50,
        max_probability_distance=0.08,
        max_support_distance=0.20,
    )

    assert evidence.sample_n == 50
    assert evidence.successes <= evidence.sample_n
    assert 0.0 <= evidence.lower_bound <= 0.62
    assert evidence.included_ood_states == ("IN_DISTRIBUTION",)
    assert evidence.max_probability_distance_used <= 0.08
    assert evidence.max_support_distance_used <= 0.20


def test_local_reliability_bound_fails_closed_when_support_is_too_thin():
    rows = [_row(p=0.60, outcome=1) for _ in range(9)]

    with pytest.raises(LowerBoundExperimentError, match="LOCAL_SUPPORT_INSUFFICIENT"):
        local_reliability_bound(
            lane_key="NFL_RECEIVING_YARDS_MORE",
            point_probability=0.61,
            historical_rows=rows,
            min_effective_n=10,
            max_neighbors=20,
            max_probability_distance=0.05,
        )


def test_composite_bound_is_conservative_minimum_and_cannot_publish():
    result = composite_lower_bound(
        0.64,
        {
            "local_wilson": 0.57,
            "block_bootstrap_q10": 0.59,
            "conditional_residual_q10": 0.55,
        },
    )

    assert result["composite_lower_bound"] == pytest.approx(0.55)
    assert result["combination_rule"] == "MIN_PRE_REGISTERED_COMPONENTS"
    assert result["probability_publishable"] is False
    assert result["promotion_authorized"] is False
    assert result["can_execute"] is False


def test_unconditional_three_way_prop_scores_push_mass_without_dropping_pushes():
    rows = [
        _row(
            p=0.55,
            lb=0.50,
            outcome=1,
            push_possible=True,
            probability_semantics="UNCONDITIONAL_WITH_PUSH_MASS",
            push_probability=0.10,
        ),
        _row(
            p=0.55,
            lb=0.50,
            outcome="PUSH",
            push_possible=True,
            probability_semantics="UNCONDITIONAL_WITH_PUSH_MASS",
            push_probability=0.10,
        ),
        _row(
            p=0.55,
            lb=0.50,
            outcome=0,
            push_possible=True,
            probability_semantics="UNCONDITIONAL_WITH_PUSH_MASS",
            push_probability=0.10,
        ),
    ]

    report = evaluate_bound_reliability(rows)

    assert report["binary_settled_n"] == 3
    assert report["push_n"] == 1
    assert report["conditional_pushes_excluded_n"] == 0
    assert report["unconditional_three_way_n"] == 3
    assert report["observed_hit_rate"] == pytest.approx(1 / 3)
    assert report["multiclass_brier_score"] is not None
    assert report["multiclass_log_loss"] is not None


def test_reliability_report_handles_pushes_and_reports_threshold_margins():
    rows = [
        _row(
            family="SPREAD",
            lane_key="NFL_SPREAD_HOME_-3",
            p=0.63,
            lb=0.57,
            outcome=1,
            push_possible=True,
            probability_semantics="CONDITIONAL_ON_NO_PUSH",
        ),
        _row(
            family="SPREAD",
            lane_key="NFL_SPREAD_HOME_-3",
            p=0.61,
            lb=0.56,
            outcome=0,
            push_possible=True,
            probability_semantics="CONDITIONAL_ON_NO_PUSH",
        ),
        _row(
            family="SPREAD",
            lane_key="NFL_SPREAD_HOME_-3",
            p=0.60,
            lb=0.54,
            outcome="PUSH",
            push_possible=True,
            probability_semantics="CONDITIONAL_ON_NO_PUSH",
        ),
    ]

    report = evaluate_bound_reliability(rows, lane_key="NFL_SPREAD_HOME_-3")

    assert report["binary_settled_n"] == 2
    assert report["push_n"] == 1
    assert report["observed_hit_rate"] == pytest.approx(0.5)
    at_55 = next(item for item in report["threshold_reliability"] if item["threshold"] == 0.55)
    assert at_55["n"] == 2
    assert at_55["reliability_margin"] == pytest.approx(0.5 - 0.565)
    assert report["probability_publishable"] is False


def test_research_eligibility_requires_lane_gate_and_blocks_ood():
    policy = ResearchEligibilityPolicy(
        name="POINT60_LB55",
        min_point_probability=0.60,
        min_lower_bound=0.55,
        min_support_n=50,
    )

    good = evaluate_research_eligibility(_row(), policy)
    assert good["eligible"] is True
    assert good["rank_eligible"] is False

    lane_hold = evaluate_research_eligibility(_row(lane_specific_gate_pass=False), policy)
    assert lane_hold["eligible"] is False
    assert "LANE_SPECIFIC_GATE_NOT_PASS" in lane_hold["blockers"]

    ood = evaluate_research_eligibility(_row(ood_state="OOD_BLOCKED"), policy)
    assert ood["eligible"] is False
    assert "OOD_BLOCKED" in ood["blockers"]


def test_market_probability_substitution_is_rejected_before_ablation():
    row = _row()
    row["market_probability_substitution_used"] = True
    policy = ResearchEligibilityPolicy(name="LB55", min_lower_bound=0.55)

    with pytest.raises(LowerBoundExperimentError, match="MARKET_PROBABILITY_SUBSTITUTION"):
        evaluate_research_eligibility(row, policy)


def test_width_policy_is_research_only_and_requires_upper_bound():
    policy = ResearchEligibilityPolicy(
        name="POINT60_LB55_WIDTH20",
        min_point_probability=0.60,
        min_lower_bound=0.55,
        max_interval_width=0.20,
    )
    accepted = evaluate_research_eligibility(_row(lb=0.55, ub=0.70), policy)
    assert accepted["eligible"] is True

    too_wide = evaluate_research_eligibility(_row(lb=0.55, ub=0.80), policy)
    assert too_wide["eligible"] is False
    assert "INTERVAL_WIDTH_ABOVE_POLICY_MAXIMUM" in too_wide["blockers"]

    missing_upper = _row()
    missing_upper["calibrated_upper_bound"] = None
    blocked = evaluate_research_eligibility(missing_upper, policy)
    assert blocked["eligible"] is False
    assert "UPPER_BOUND_MISSING_FOR_WIDTH_POLICY" in blocked["blockers"]


def test_policy_ablation_compares_current_style_vs_lb_only_without_mutation():
    rows = [
        _row(p=0.62, lb=0.56, outcome=1),
        _row(p=0.59, lb=0.56, outcome=1),
        _row(p=0.63, lb=0.54, outcome=0),
        _row(p=0.61, lb=0.58, outcome=1),
    ]
    current_style = ResearchEligibilityPolicy(
        name="POINT60_LB55",
        min_point_probability=0.60,
        min_lower_bound=0.55,
    )
    lb_only = ResearchEligibilityPolicy(
        name="LB55_ONLY",
        min_lower_bound=0.55,
    )

    report = run_policy_ablation(rows, [current_style, lb_only])
    by_name = {item["policy"]: item for item in report["policy_results"]}

    assert by_name["POINT60_LB55"]["admitted_n"] == 2
    assert by_name["LB55_ONLY"]["admitted_n"] == 3
    assert report["production_policy_mutated"] is False
    assert report["probability_publishable"] is False
    assert report["promotion_authorized"] is False
    assert report["can_execute"] is False
