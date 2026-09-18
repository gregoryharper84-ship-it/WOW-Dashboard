from prop_terminal_reducer_v2 import reduce_prop_terminal


def test_missing_artifact_is_capability_blocked_not_pick_rejected():
    result = reduce_prop_terminal(proposed_label="NO_PLAY", blockers=["MODEL_ARTIFACT_NOT_PROMOTED"], model_evaluated=False)
    assert result.terminal_label == "MODEL_UNAVAILABLE"
    assert result.verdict_class == "CAPABILITY_BLOCKED"


def test_missing_evidence_is_input_insufficient_not_model_unavailable():
    result = reduce_prop_terminal(proposed_label="NO_LOW_PROBABILITY", blockers=["PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"], model_evaluated=False)
    assert result.terminal_label == "MODEL_INPUTS_INSUFFICIENT"
    assert result.verdict_class == "ACQUISITION_BLOCKED"
    assert result.pick_rejected is False


def test_player_identity_ambiguity_is_input_insufficient():
    result = reduce_prop_terminal(proposed_label="MODEL_UNAVAILABLE", blockers=["PROP_PLAYER_IDENTITY_UNRESOLVED"], model_evaluated=False)
    assert result.terminal_label == "MODEL_INPUTS_INSUFFICIENT"
    assert result.verdict_class == "ACQUISITION_BLOCKED"


def test_recent_starts_shortfall_is_input_insufficient():
    result = reduce_prop_terminal(proposed_label="MODEL_UNAVAILABLE", blockers=["MLB_RECENT_STARTS_INSUFFICIENT"], model_evaluated=False)
    assert result.terminal_label == "MODEL_INPUTS_INSUFFICIENT"


def test_missing_market_data_preserves_completed_model_terminal_without_rejection():
    result = reduce_prop_terminal(proposed_label="MODEL_QUALIFIED_HOLD", blockers=["PAYOUT_UNRESOLVED"], model_evaluated=True)
    assert result.terminal_label == "MODEL_QUALIFIED_HOLD"
    assert result.verdict_class == "MARKET_BLOCKED"
    assert result.model_evaluated is True


def test_real_low_probability_rejection_survives_market_hold():
    result = reduce_prop_terminal(proposed_label="NO_LOW_PROBABILITY", blockers=["PAYOUT_UNRESOLVED"], model_evaluated=True)
    assert result.terminal_label == "NO_LOW_PROBABILITY"
    assert result.verdict_class == "MODEL_REJECTED"
    assert result.pick_rejected is True


def test_final_refresh_event_invalidation_uses_native_no_play():
    result = reduce_prop_terminal(proposed_label="MODEL_QUALIFIED_HOLD", blockers=["EVENT_ALREADY_STARTED"], model_evaluated=True)
    assert result.terminal_label == "NO_PLAY"
    assert result.verdict_class == "EVENT_INVALIDATED"


def test_scorer_failure_keeps_typed_scorer_terminal():
    result = reduce_prop_terminal(proposed_label="MODEL_UNAVAILABLE", blockers=["ROW_SCORING_UNAVAILABLE"], model_evaluated=False)
    assert result.terminal_label == "MODEL_SCORER_FAILED"
    assert result.verdict_class == "SCORER_FAILED"


def test_malformed_probability_package_keeps_output_invalid_terminal():
    result = reduce_prop_terminal(proposed_label="MODEL_OUTPUT_INVALID", blockers=["PROBABILITY_INVALID"], model_evaluated=True)
    assert result.terminal_label == "MODEL_OUTPUT_INVALID"
    assert result.verdict_class == "MODEL_OUTPUT_INVALID"


def test_rejection_without_model_run_is_output_invalid_not_model_unavailable():
    result = reduce_prop_terminal(proposed_label="NO_LOW_PROBABILITY", blockers=[], model_evaluated=False)
    assert result.terminal_label == "MODEL_OUTPUT_INVALID"
    assert "REJECTION_WITHOUT_MODEL_EVALUATION" in result.blockers


def test_certified_line_ood_is_model_contract_rejection_before_probability_scoring():
    result = reduce_prop_terminal(proposed_label="REJECT_OOD", blockers=["MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT"], model_evaluated=False)
    assert result.terminal_label == "REJECT_OOD"
    assert result.verdict_class == "MODEL_CONTRACT_REJECTED"


def test_stale_starter_slate_purge_remains_row_rejection_not_model_unavailable():
    result = reduce_prop_terminal(proposed_label="SLATE_PURGE", blockers=["SLATE_PURGE"], model_evaluated=False)
    assert result.terminal_label == "SLATE_PURGE"
    assert result.verdict_class == "ROW_INVALIDATED"
