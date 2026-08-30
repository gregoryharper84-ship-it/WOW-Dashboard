from qualification_policy_v2 import classify_prop_probability


def test_phase_a_can_be_model_qualified_hold_but_not_advance_money_or_final():
    result = classify_prop_probability(
        calibrated_probability=0.63,
        calibrated_lower_bound=0.56,
        calibration_status="PRECALIBRATION_SHRINKAGE",
        blockers=[],
        probability_publishable=True,
    )
    assert result.terminal_label == "MODEL_QUALIFIED_HOLD"
    assert result.confidence_tier == "STANDARD"
    assert result.rank_eligible is True
    assert result.model_supported is True
    assert result.downstream_money_evaluation_allowed is False
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
    assert result.confidence_tier == "RESEARCH"
    assert result.rank_eligible is True
    assert result.downstream_money_evaluation_allowed is False
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
    assert result.confidence_tier == "BELOW_THRESHOLD"
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


def test_high_confidence_uses_native_hold_label_and_metadata():
    result = classify_prop_probability(
        calibrated_probability=0.69,
        calibrated_lower_bound=0.62,
        calibration_status="PLATT_TIME_SPLIT_V1",
        blockers=[],
        probability_publishable=True,
    )
    assert result.terminal_label == "MODEL_QUALIFIED_HOLD"
    assert result.confidence_tier == "HIGH"
    assert result.rank_eligible is True
    assert result.downstream_money_evaluation_allowed is True
    assert result.final_approved_allowed is False


def test_unpublishable_probability_is_capability_state_not_pick_rejection():
    result = classify_prop_probability(
        calibrated_probability=None,
        calibrated_lower_bound=None,
        calibration_status="UNKNOWN_OR_BLOCKED",
        blockers=[],
        probability_publishable=False,
    )
    assert result.terminal_label == "MODEL_UNAVAILABLE"
    assert result.rank_eligible is False
    assert result.model_supported is False
    assert "PROBABILITY_PUBLICATION_BLOCKED" in result.blockers
