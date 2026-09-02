from pathlib import Path


INSTRUCTIONS = Path(__file__).with_name("WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt")


def _instructions() -> str:
    return INSTRUCTIONS.read_text(encoding="utf-8")


def test_exact_supported_prop_row_cannot_terminate_at_capability_only():
    text = _instructions()
    assert "SCORE-REQUIRED COMPLETION INVARIANT" in text
    assert "MODEL_CAPABILITY_AVAILABLE" in text
    assert "is preflight evidence only and is never a terminal result" in text
    assert "scoreWowV17PickRequest" in text
    assert "scoreWowV17Prop" in text


def test_no_action_attempt_has_typed_host_orchestration_failure():
    text = _instructions()
    assert "LIVE_GPT_ACTION_INVOCATION_BLOCKED" in text
    assert "scoring_attempted=false" in text
    assert "backend_model_capability=UNKNOWN" in text
    assert "host-orchestration failure, not a model result" in text


def test_board_workflow_requires_canonical_batch_scoring_after_extraction():
    text = _instructions()
    assert "after extracting all readable rows" in text
    assert "canonical batch `/score-pick-request` bridge" in text
    assert "each row reaches exactly one terminal outcome" in text
    assert "Unsupported/OOD rows must still be submitted" in text


def test_governance_safety_remains_non_executable():
    text = _instructions()
    assert "can_execute=false always" in text
    assert "Never place, route, modify, approve or cancel a wager/order" in text
