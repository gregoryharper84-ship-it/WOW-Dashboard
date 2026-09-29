from pathlib import Path


INSTRUCTIONS = (
    Path(__file__).parents[1] / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
).read_text()
LLP_INSTRUCTIONS = (
    Path(__file__).parents[2] / "LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md"
).read_text()


def test_unbound_action_remains_invocation_blocked_only_before_invocation():
    assert (
        "Missing or unbound required Action => LIVE_GPT_ACTION_INVOCATION_BLOCKED"
        in INSTRUCTIONS
    )
    assert "action_invocation_attempted=false" in INSTRUCTIONS


def test_invoked_health_transport_failure_is_not_rewritten_as_unbound_action():
    assert "ACTION_TRANSPORT_TIMEOUT" in INSTRUCTIONS
    assert "ACTION_TRANSPORT_FAILURE" in INSTRUCTIONS
    assert "action_invocation_attempted=true" in INSTRUCTIONS
    assert (
        "do not relabel the invoked transport failure as LIVE_GPT_ACTION_INVOCATION_BLOCKED or MODEL_UNAVAILABLE"
        in INSTRUCTIONS
    )


def test_health_preflight_does_not_claim_specialist_scoring_attempt():
    assert (
        "a health-only Action attempt does not mean specialist scoring was attempted"
        in INSTRUCTIONS
    )
    assert "rows_attempted=0" in INSTRUCTIONS
    assert "backend_model_capability=UNKNOWN" in INSTRUCTIONS


def test_transport_failure_remains_non_executable_and_non_model_capability_claim():
    assert "can_execute=false" in INSTRUCTIONS
    assert (
        "it is not LIVE_GPT_ACTION_INVOCATION_BLOCKED, MODEL_UNAVAILABLE, or evidence that the controlling model is absent"
        in INSTRUCTIONS
    )


def test_llp_preserves_action_transport_boundary_before_model_failure_taxonomy():
    assert "ACTION_TRANSPORT_TIMEOUT or ACTION_TRANSPORT_FAILURE" in LLP_INSTRUCTIONS
    assert "action_invocation_attempted=true" in LLP_INSTRUCTIONS
    assert (
        "do not relabel transport as DISCOVERY_OR_ACQUISITION_INCOMPLETE, MODEL_SCORER_FAILED, or MODEL_UNAVAILABLE"
        in LLP_INSTRUCTIONS
    )
    assert (
        "Action/HTTP transport before a terminal backend response remains ACTION_TRANSPORT_TIMEOUT/ACTION_TRANSPORT_FAILURE"
        in LLP_INSTRUCTIONS
    )


def test_llp_ambiguous_stateful_transport_requires_receipt_recovery_before_retry():
    assert (
        "recover the immutable receipt before retrying with the same IDs"
        in LLP_INSTRUCTIONS
    )
    assert (
        "A transport failure proves neither model absence nor acquisition failure"
        in LLP_INSTRUCTIONS
    )
    assert "can_execute=false always" in LLP_INSTRUCTIONS


def test_llp_pasteable_authority_block_stays_under_editor_limit():
    authority_block = LLP_INSTRUCTIONS.split("```", 2)[1]
    assert len(authority_block) < 8000
