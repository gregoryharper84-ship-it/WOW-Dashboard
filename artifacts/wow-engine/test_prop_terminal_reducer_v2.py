from prop_terminal_reducer_v2 import reduce_prop_terminal


def test_missing_artifact_is_capability_blocked_not_pick_rejected():
    result = reduce_prop_terminal(
        proposed_label="NO_PLAY",
        blockers=["MODEL_ARTIFACT_NOT_PROMOTED"],
        model_evaluated=False,
    )
    assert result.terminal_label == "MODEL_UNAVAILABLE"
    assert result.verdict_class == "CAPABILITY_BLOCKED"
    assert result.pick_rejected is False
    assert result.infrastructure_blocked is True


def test_missing_evidence_is_acquisition_blocked_not_pick_rejected():
    result = reduce_prop_terminal(
        proposed_label="NO_LOW_PROBABILITY",
        blockers=["PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"],
        model_evaluated=False,
    )
    assert result.terminal_label == "EVIDENCE_INCOMPLETE"
    assert result.verdict_class == "ACQUISITION_BLOCKED"
    assert result.pick_rejected is False


def test_missing_market_data_preserves_model_evaluation_without_rejection():
    result = reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD",
        blockers=["PAYOUT_UNRESOLVED"],
        model_evaluated=True,
    )
    assert result.terminal_label == "MARKET_DATA_UNAVAILABLE"
    assert result.verdict_class == "MARKET_BLOCKED"
    assert result.model_evaluated is True
    assert result.pick_rejected is False


def test_true_probability_rejection_requires_model_evaluation():
    result = reduce_prop_terminal(
        proposed_label="NO_LOW_PROBABILITY",
        blockers=[],
        model_evaluated=True,
    )
    assert result.terminal_label == "NO_LOW_PROBABILITY"
    assert result.verdict_class == "MODEL_REJECTED"
    assert result.pick_rejected is True


def test_rejection_without_model_run_fails_closed_to_model_unavailable():
    result = reduce_prop_terminal(
        proposed_label="NO_LOW_PROBABILITY",
        blockers=[],
        model_evaluated=False,
    )
    assert result.terminal_label == "MODEL_UNAVAILABLE"
    assert result.pick_rejected is False
    assert "REJECTION_WITHOUT_MODEL_EVALUATION" in result.blockers
