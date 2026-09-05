from qualification_policy_v2 import classify_prop_probability


def q(p, lb, ub, *, status="PLATT_TIME_SPLIT_V1", publishable=True):
    return classify_prop_probability(
        calibrated_probability=p,
        calibrated_lower_bound=lb,
        calibrated_upper_bound=ub,
        calibration_status=status,
        blockers=[],
        probability_publishable=publishable,
    )


def test_high_model_qualified_is_rankable_but_not_final_approved():
    result = q(0.69, 0.62, 0.74)
    assert result.terminal_label == "MODEL_QUALIFIED_HOLD"
    assert result.model_qualification_status == "MODEL_QUALIFIED"
    assert result.confidence_tier == "HIGH"
    assert result.rank_eligible is True
    assert result.final_approved_allowed is False


def test_standard_model_qualified_preserves_existing_governed_threshold():
    result = q(0.63, 0.58, 0.68)
    assert result.model_qualified is True
    assert result.confidence_tier == "STANDARD"


def test_phase_a_precalibration_probability_is_research_only_even_above_high_threshold():
    result = q(0.69, 0.62, 0.74, status="PRECALIBRATION_SHRINKAGE")
    assert result.terminal_label == "RESEARCH_INTEREST"
    assert result.confidence_tier == "PRECALIBRATION"
    assert result.model_supported is True
    assert result.model_qualified is False
    assert result.rank_eligible is False
    assert result.downstream_money_evaluation_allowed is False
    assert result.final_approved_allowed is False
    assert "PRECALIBRATION_CEILING=RESEARCH_ONLY_NO_MONEY_QUALIFICATION" in result.qualification_reasons


def test_phase_a_precalibration_preserves_completed_low_probability_package_without_rank_eligibility():
    result = q(0.52, 0.49, 0.57, status="PRECALIBRATION_SHRINKAGE")
    assert result.terminal_label == "RESEARCH_INTEREST"
    assert result.model_supported is True
    assert result.model_qualified is False
    assert result.rank_eligible is False
    assert result.downstream_money_evaluation_allowed is False


def test_sub_60_probability_is_research_only_even_with_strong_lower_bound():
    result = q(0.5923, 0.5861, 0.6500)
    assert result.model_qualified is False
    assert result.confidence_tier == "RESEARCH"
    assert result.terminal_label == "RESEARCH_INTEREST"
    assert result.rank_eligible is False


def test_research_interest_is_never_rank_eligible():
    result = q(0.575, 0.515, 0.61)
    assert result.terminal_label == "RESEARCH_INTEREST"
    assert result.confidence_tier == "RESEARCH"
    assert result.rank_eligible is False
    assert result.model_qualified is False


def test_low_probability_not_qualified():
    result = q(0.52, 0.49, 0.57)
    assert result.terminal_label == "NO_LOW_PROBABILITY"
    assert result.rank_eligible is False


def test_upper_bound_is_advisory_not_a_new_qualification_cutoff():
    result = q(0.62, 0.56, 0.90)
    assert result.model_qualified is True
    assert result.uncertainty_width is not None


def test_malformed_upper_bound_is_output_invalid():
    result = classify_prop_probability(
        calibrated_probability=0.63,
        calibrated_lower_bound=0.58,
        calibrated_upper_bound=0.50,
        calibration_status="PASS",
        blockers=[],
        probability_publishable=True,
    )
    assert result.terminal_label == "MODEL_OUTPUT_INVALID"
    assert result.rank_eligible is False


def test_unhealthy_calibration_cannot_model_qualify():
    result = q(0.70, 0.64, 0.75, status="BLOCKED")
    assert result.model_qualified is False
    assert result.rank_eligible is False


def test_market_identity_is_not_a_sporting_model_hard_blocker():
    result = classify_prop_probability(
        calibrated_probability=0.62,
        calibrated_lower_bound=0.58,
        calibrated_upper_bound=0.68,
        calibration_status="PLATT_TIME_SPLIT_V1",
        blockers=["EXACT_MARKET_IDENTITY_UNAVAILABLE"],
        probability_publishable=True,
    )
    assert result.model_qualified is True
    assert result.rank_eligible is True


def test_unpublishable_sporting_probability_is_not_model_qualified():
    result = q(None, None, None, status="UNKNOWN_OR_BLOCKED", publishable=False)
    assert result.model_qualified is False
    assert result.rank_eligible is False
