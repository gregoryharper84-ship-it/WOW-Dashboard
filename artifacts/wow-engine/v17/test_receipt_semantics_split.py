"""Action invocation and specialist scoring are two facts, reported separately.

Conflating them under one ``scoring_attempted`` name made a receipt read as
"no required Action call occurred" when the Action had in fact been invoked and
only the controlling specialist scorer had not run.
"""
from prop_terminal_reducer_v2 import reduce_prop_terminal
from v17.host_routing import FullModelActionReceipt, validate_full_model_action_receipt


def _receipt(**overrides):
    base = dict(
        candidate_family="PLAYER_PROP",
        action_invoked=True,
        operation_id="scoreWowV17PickRequest",
        backend_terminal_status="REJECTED",
    )
    base.update(overrides)
    return FullModelActionReceipt(**base)


def test_invoked_action_whose_scorer_never_ran_is_not_reported_as_uninvoked():
    """The exact reported defect: a direct 1IP receipt after a real Action call."""
    out = validate_full_model_action_receipt(
        _receipt(backend_specialist_scoring_attempted=False)
    )
    assert out["action_invocation_attempted"] is True
    assert out["specialist_scoring_attempted"] is False
    # The host contract's field keeps its own meaning: an Action call occurred.
    assert out["scoring_attempted"] is True


def test_specialist_fact_is_unknown_rather_than_inferred_from_the_host():
    out = validate_full_model_action_receipt(_receipt())
    assert out["specialist_scoring_attempted"] == "UNKNOWN"
    assert out["action_invocation_attempted"] is True


def test_uninvoked_action_reports_both_facts_as_not_attempted():
    out = validate_full_model_action_receipt(
        _receipt(action_invoked=False, backend_specialist_scoring_attempted=False)
    )
    assert out["action_invocation_attempted"] is False
    assert out["scoring_attempted"] is False
    assert out["specialist_scoring_attempted"] is False
    assert out["status"] == "LIVE_GPT_ACTION_INVOCATION_BLOCKED"


def test_operation_mismatch_still_counts_as_an_invoked_action():
    out = validate_full_model_action_receipt(
        _receipt(operation_id="someOtherOperation", backend_specialist_scoring_attempted=True)
    )
    assert out["action_invocation_attempted"] is True
    assert out["specialist_scoring_attempted"] is True
    assert out["status"] == "LIVE_GPT_ACTION_RESULT_INVALID"
    assert out["can_execute"] is False


def test_backend_receipt_carries_the_unambiguous_specialist_field():
    import pick_request_runtime as runtime

    out = runtime._terminal("row-1", "REJECTED", "ROW_SCORING_UNAVAILABLE", detail={})
    assert out["specialist_scoring_attempted"] is False
    assert out["scoring_attempted"] is False


# --- infrastructure_blocked disambiguation -----------------------------------

def test_model_rejection_with_a_market_blocker_is_not_infrastructure_blocked():
    decision = reduce_prop_terminal(
        proposed_label="NO_LOW_PROBABILITY", blockers=["PAYOUT_UNRESOLVED"], model_evaluated=True
    )
    assert decision.terminal_label == "NO_LOW_PROBABILITY"
    assert decision.pick_rejected is True
    assert decision.infrastructure_blocked is False
    assert decision.terminal_cause == "MODEL_JUDGMENT"
    # The blocker is preserved, just not misreported as the cause.
    assert "PAYOUT_UNRESOLVED" in decision.blockers
    assert decision.concurrent_infrastructure_blockers == ("PAYOUT_UNRESOLVED",)


def test_a_genuinely_infrastructure_caused_terminal_still_reports_as_such():
    decision = reduce_prop_terminal(
        proposed_label="MODEL_UNAVAILABLE", blockers=["MODEL_ARTIFACT_NOT_PROMOTED"], model_evaluated=False
    )
    assert decision.infrastructure_blocked is True
    assert decision.terminal_cause == "INFRASTRUCTURE"


def test_market_blocked_completed_model_preserves_model_supported_terminal_cause():
    decision = reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD", blockers=["PAYOUT_UNRESOLVED"], model_evaluated=True
    )
    assert decision.terminal_label == "MODEL_QUALIFIED_HOLD"
    assert decision.verdict_class == "MARKET_BLOCKED"
    assert decision.model_evaluated is True
    assert decision.pick_rejected is False
    assert decision.infrastructure_blocked is False
    assert decision.terminal_cause == "MODEL_SUPPORTED"
    assert decision.concurrent_infrastructure_blockers == ("PAYOUT_UNRESOLVED",)


def test_market_blocker_before_model_evaluation_remains_infrastructure_caused():
    decision = reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD", blockers=["PAYOUT_UNRESOLVED"], model_evaluated=False
    )
    assert decision.terminal_label == "MODEL_INPUTS_INSUFFICIENT"
    assert decision.verdict_class == "MARKET_BLOCKED"
    assert decision.infrastructure_blocked is True
    assert decision.terminal_cause == "INFRASTRUCTURE"
