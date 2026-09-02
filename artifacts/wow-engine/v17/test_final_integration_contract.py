import pytest

from v17.final_integration_contract import (
    V17IntegrationContractError,
    validate_final_integration,
)


def _prob(model=0.66, calibrated=0.64, lower=0.60):
    return {
        "model_probability": model,
        "calibrated_probability": calibrated,
        "calibrated_lower_bound": lower,
        "can_execute": False,
    }


def test_combined_v17_contract_accepts_single_probability_adjustment_and_structural_portfolio_penalty():
    baseline = _prob(0.68, 0.66, 0.62)
    adjusted = _prob(0.61, 0.59, 0.55)
    adjusted["suppression_applied_count"] = 1
    market = {
        "evidence_class": "ADJACENT_LINE",
        "exact_line_confirmed": False,
        "line_distance": 2.0,
        "can_execute": False,
    }
    before = dict(adjusted)
    after = dict(adjusted)
    after["portfolio_governance"] = {
        "duplicate_thesis_penalty": 0.08,
        "critical_leg_score": 0.51,
    }

    result = validate_final_integration(
        baseline_probability=baseline,
        matchup_adjusted_probability=adjusted,
        market_audit=market,
        portfolio_leg_before=before,
        portfolio_leg_after=after,
    )

    assert result.passed is True
    assert result.can_execute is False
    assert "no_cross_layer_double_penalty" in result.checks


def test_adjacent_line_cannot_claim_exact_line_confirmation():
    adjusted = _prob(0.61, 0.59, 0.55)
    with pytest.raises(V17IntegrationContractError, match="ADJACENT_LINE_IMPROPERLY_GRANTED_EXACT_AUTHORITY"):
        validate_final_integration(
            baseline_probability=_prob(0.68, 0.66, 0.62),
            matchup_adjusted_probability=adjusted,
            market_audit={"evidence_class": "ADJACENT_LINE", "exact_line_confirmed": True},
            portfolio_leg_before=adjusted,
            portfolio_leg_after=adjusted,
        )


def test_portfolio_layer_cannot_reduce_probability_a_second_time():
    adjusted = _prob(0.61, 0.59, 0.55)
    double_penalized = _prob(0.58, 0.56, 0.52)
    with pytest.raises(V17IntegrationContractError, match="PORTFOLIO_LAYER_MUTATED_SPORTING_PROBABILITY"):
        validate_final_integration(
            baseline_probability=_prob(0.68, 0.66, 0.62),
            matchup_adjusted_probability=adjusted,
            market_audit={"evidence_class": "ADJACENT_LINE", "exact_line_confirmed": False},
            portfolio_leg_before=adjusted,
            portfolio_leg_after=double_penalized,
        )


def test_matchup_contradiction_must_reach_numeric_probability_when_required():
    unchanged = _prob(0.68, 0.66, 0.62)
    with pytest.raises(V17IntegrationContractError, match="MATCHUP_CONTRADICTION_NOT_NUMERICALLY_REFLECTED"):
        validate_final_integration(
            baseline_probability=unchanged,
            matchup_adjusted_probability=unchanged,
            market_audit={"evidence_class": "EXACT_LINE", "exact_line_confirmed": True},
            portfolio_leg_before=unchanged,
            portfolio_leg_after=unchanged,
        )


def test_execution_authority_is_rejected_at_any_layer():
    adjusted = _prob(0.61, 0.59, 0.55)
    market = {"evidence_class": "EXACT_LINE", "exact_line_confirmed": True, "can_execute": True}
    with pytest.raises(V17IntegrationContractError, match="EXECUTION_AUTHORITY_VIOLATION:market"):
        validate_final_integration(
            baseline_probability=_prob(0.68, 0.66, 0.62),
            matchup_adjusted_probability=adjusted,
            market_audit=market,
            portfolio_leg_before=adjusted,
            portfolio_leg_after=adjusted,
        )
