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
    assert result.terminal_label == "MODEL_UNAVAILABLE"
    assert result.verdict_class == "ACQUISITION_BLOCKED"
    assert result.pick_rejected is False
    assert result.infrastructure_blocked is True


def test_missing_market_data_preserves_completed_model_terminal_without_rejection():
    result = reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD",
        blockers=["PAYOUT_UNRESOLVED"],
        model_evaluated=True,
    )
    assert result.terminal_label == "MODEL_QUALIFIED_HOLD"
    assert result.verdict_class == "MARKET_BLOCKED"
    assert result.model_evaluated is True
    assert result.pick_rejected is False


def test_real_low_probability_rejection_survives_market_hold():
    result = reduce_prop_terminal(
        proposed_label="NO_LOW_PROBABILITY",
        blockers=["PAYOUT_UNRESOLVED"],
        model_evaluated=True,
    )
    assert result.terminal_label == "NO_LOW_PROBABILITY"
    assert result.verdict_class == "MODEL_REJECTED"
    assert result.pick_rejected is True
    assert result.infrastructure_blocked is True


def test_final_refresh_event_invalidation_uses_native_no_play():
    result = reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD",
        blockers=["EVENT_ALREADY_STARTED"],
        model_evaluated=True,
    )
    assert result.terminal_label == "NO_PLAY"
    assert result.verdict_class == "EVENT_INVALIDATED"
    assert result.pick_rejected is False


def test_event_invalidation_outranks_concurrent_acquisition_gap():
    result = reduce_prop_terminal(
        proposed_label="MODEL_UNAVAILABLE",
        blockers=["RUN_INVALID_ACQUISITION_INCOMPLETE", "EVENT_NOT_PREGAME"],
        model_evaluated=False,
    )
    assert result.terminal_label == "NO_PLAY"
    assert result.verdict_class == "EVENT_INVALIDATED"
    assert result.pick_rejected is False
    assert result.infrastructure_blocked is False


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


def test_stale_starter_slate_purge_remains_row_rejection_not_model_unavailable():
    result = reduce_prop_terminal(
        proposed_label="SLATE_PURGE",
        blockers=["SLATE_PURGE"],
        model_evaluated=False,
    )
    assert result.terminal_label == "SLATE_PURGE"
    assert result.verdict_class == "ROW_INVALIDATED"
    assert result.pick_rejected is True
    assert result.infrastructure_blocked is False


def test_exhausted_1ip_data_quality_remains_row_rejection_not_model_unavailable():
    result = reduce_prop_terminal(
        proposed_label="REJECT_DATA_QUALITY",
        blockers=["REJECT_DATA_QUALITY"],
        model_evaluated=False,
    )
    assert result.terminal_label == "REJECT_DATA_QUALITY"
    assert result.verdict_class == "DATA_QUALITY_REJECTED"
    assert result.pick_rejected is True
    assert result.infrastructure_blocked is False
