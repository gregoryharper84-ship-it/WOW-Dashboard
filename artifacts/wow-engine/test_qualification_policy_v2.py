from qualification_policy_v2 import classify_prop_probability


def test_phase_a_can_be_model_qualified_hold_but_not_money_or_final():
    result = classify_prop_probability(
        calibrated_probability=0.63,
        calibrated_lower_bound=0.56,
        calibration_status="PRECALIBRATION_SHRINKAGE",
        blockers=[],
        probability_publishable=True,
    )
    assert result.terminal_label == "MODEL_QUALIFIED_HOLD"
    assert result.rank_eligible is True
    assert result.model_supported is True
    assert result.money_qualified_allowed is False
    assert result.final_approved_allowed is False


def test_phase_a_research_interest_is_preserved():
    result = classify_prop_probability(
        calibrated_probability=0.585,
        calibrated_lower_bound=0.515,
        calibration_status="PRECALIBRATION_SHRINKAGE",
        blockers=[],
        probability_publishable=True,
    )
    assert result.terminal_label == "RESEARCH_INTEREST"
    assert result.rank_eligible is True
    assert result.money_qualified_allowed is False
    assert result.final_approved_allowed is False


def test_low_probability_remains_rejected():
    result = classify_prop_probability(
        calibrated_probability=0.537,
        calibrated_lower_bound=0.517,
        calibration_status="PRECALIBRATION_SHRINKAGE",
        blockers=[],
        probability_publishable=True,
    )
    assert result.terminal_label == "NO_LOW_PROBABILITY"
    assert result.rank_eligible is False


def test_hard_model_blocker_cannot_be_overridden_by_probability():
    result = classify_prop_probability(
        calibrated_probability=0.80,
        calibrated_lower_bound=0.72,
        calibration_status="PLATT_TIME_SPLIT_V1",
        blockers=["CONTROLLING_SPECIALIST_UNAVAILABLE"],
        probability_publishable=True,
    )
    assert result.terminal_label == "MODEL_UNAVAILABLE"
    assert result.rank_eligible is False
    assert result.model_supported is False


def test_high_confidence_non_phase_a_can_advance_probability_lane():
    result = classify_prop_probability(
        calibrated_probability=0.69,
        calibrated_lower_bound=0.62,
        calibration_status="PLATT_TIME_SPLIT_V1",
        blockers=[],
        probability_publishable=True,
    )
    assert result.terminal_label == "HIGH_CONFIDENCE_MODEL_QUALIFIED_HOLD"
    assert result.rank_eligible is True
    assert result.money_qualified_allowed is True
    assert result.final_approved_allowed is True
