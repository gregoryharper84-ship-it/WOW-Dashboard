from __future__ import annotations

import pytest

from v17.experiments.lower_bound_v2_adapters import (
    adapt_moneyline_shadow,
    adapt_prop_prediction,
    adapt_spread_shadow,
)
from v17.experiments.lower_bound_v2_multilane import (
    LowerBoundExperimentError,
    evaluate_bound_reliability,
    evaluate_research_eligibility,
    ResearchEligibilityPolicy,
)


def test_moneyline_adapter_preserves_challenger_bound_and_binary_semantics():
    row = adapt_moneyline_shadow(
        {
            "official_event_id": "NFL_2026_ATL_NO",
            "selected_participant": "ATL",
            "calibrated_probability": 0.62,
            "composite_lower_bound": 0.56,
            "lower_bound_method": "LOCAL_WILSON90_K50_MIN_BLOCK_BOOTSTRAP_Q10_V1",
            "market_probability_substitution_used": False,
        },
        lane_key="NFL_ML_SELECTED_SIDE",
        settlement="WIN",
        ood_state="IN_DISTRIBUTION",
        support_n=50,
        lane_specific_gate_pass=True,
    )

    assert row["family"] == "MONEYLINE"
    assert row["probability_semantics"] == "BINARY_OUTCOME"
    assert row["calibrated_probability"] == pytest.approx(0.62)
    assert row["calibrated_lower_bound"] == pytest.approx(0.56)
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_spread_adapter_uses_no_push_probability_and_excludes_push_on_binary_score():
    base = {
        "sport": "NFL",
        "official_event_id": "NFL_2026_A_B",
        "spread_line": -3.0,
        "p_cover": 0.54,
        "p_push": 0.10,
        "p_not_cover": 0.36,
        "p_cover_given_no_push": 0.60,
        "research_lower_bound_cover": 0.53,
        "distribution_sample_n": 240,
        "market_probability_substitution_used": False,
    }
    win = adapt_spread_shadow(
        base,
        lane_key="NFL_SPREAD_HOME",
        settlement="COVER",
        ood_state="IN_DISTRIBUTION",
    )
    push = adapt_spread_shadow(
        base,
        lane_key="NFL_SPREAD_HOME",
        settlement="PUSH",
        ood_state="IN_DISTRIBUTION",
    )

    assert win["probability_semantics"] == "CONDITIONAL_ON_NO_PUSH"
    assert win["calibrated_probability"] == pytest.approx(0.60)
    assert win["push_probability"] == pytest.approx(0.10)
    assert win["support_n"] is None
    assert win["distribution_sample_n_source_only"] == 240

    report = evaluate_bound_reliability([win, push])
    assert report["binary_settled_n"] == 1
    assert report["push_n"] == 1
    assert report["conditional_pushes_excluded_n"] == 1


def test_prop_adapter_preserves_unconditional_push_mass_and_does_not_fake_local_support():
    record = {
        "prediction_id": "pred-1",
        "event_id": "evt-1",
        "sport": "MLB",
        "player": "Pitcher A",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 6.0,
        "direction": "MORE",
        "controlling_specialist": "MLB_PITCHER_K_SPECIALIST",
        "model_family": "MLB_K_V17",
        "model_artifact_version": "artifact-v1",
        "calibration_status": "PLATT_TIME_SPLIT_V1",
        "bounds_method_version": "PREDICTIVE_BOUNDS_V1",
        "calibrated_probability": 0.57,
        "calibrated_probability_lower_bound": 0.51,
        "calibrated_probability_upper_bound": 0.63,
        "push_probability": 0.08,
        "effective_sample_size": 50000,
        "calibration_training_n": 800,
        "market_prior_weight": 0.0,
    }
    row = adapt_prop_prediction(
        record,
        lane_key="MLB_PITCHER_STRIKEOUTS_MORE",
        settlement="PUSH",
        ood_state="IN_DISTRIBUTION",
        local_support_n=None,
        lane_specific_gate_pass=True,
    )

    assert row["probability_semantics"] == "UNCONDITIONAL_WITH_PUSH_MASS"
    assert row["push_probability"] == pytest.approx(0.08)
    assert row["support_n"] is None
    assert row["effective_sample_size_source_only"] == 50000
    assert row["calibration_training_n_source_only"] == 800

    policy = ResearchEligibilityPolicy(
        name="REQUIRES_LOCAL_SUPPORT",
        min_lower_bound=0.50,
        min_support_n=30,
    )
    decision = evaluate_research_eligibility(row, policy)
    assert decision["eligible"] is False
    assert "SUPPORT_N_MISSING" in decision["blockers"]

    report = evaluate_bound_reliability([row])
    assert report["binary_settled_n"] == 1
    assert report["push_n"] == 1
    assert report["unconditional_three_way_n"] == 1
    assert report["observed_hit_rate"] == 0.0


def test_prop_adapter_rejects_market_prior_weight_as_probability_substitution():
    row = adapt_prop_prediction(
        {
            "calibrated_probability": 0.62,
            "calibrated_probability_lower_bound": 0.56,
            "calibrated_probability_upper_bound": 0.68,
            "push_probability": 0.0,
            "market_prior_weight": 0.10,
        },
        lane_key="NFL_RECEIVING_YARDS_MORE",
        settlement="WIN",
        ood_state="IN_DISTRIBUTION",
        local_support_n=50,
        lane_specific_gate_pass=True,
    )

    policy = ResearchEligibilityPolicy(name="LB55", min_lower_bound=0.55)
    with pytest.raises(LowerBoundExperimentError, match="MARKET_PROBABILITY_SUBSTITUTION"):
        evaluate_research_eligibility(row, policy)
