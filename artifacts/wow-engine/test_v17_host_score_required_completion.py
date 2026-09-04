from pathlib import Path


INSTRUCTIONS = Path(__file__).with_name("WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt")


def _instructions() -> str:
    return INSTRUCTIONS.read_text(encoding="utf-8")


def test_exact_supported_prop_row_cannot_terminate_at_capability_only():
    text = _instructions()
    assert "MODEL_CAPABILITY_AVAILABLE" in text
    assert "preflight evidence only" in text
    assert "never a terminal result" in text
    assert "continue to Action scoring" in text
    assert "scoreWowV17PickRequest" in text
    assert "scoreWowV17Prop" in text


def test_no_action_attempt_has_typed_host_orchestration_failure():
    text = _instructions()
    assert "LIVE_GPT_ACTION_INVOCATION_BLOCKED" in text
    assert "scoring_attempted=false" in text
    assert "backend_model_capability=UNKNOWN" in text
    assert "host-orchestration failure" in text
    assert "not a model result" in text


def test_board_workflow_requires_canonical_batch_scoring_after_extraction():
    text = _instructions()
    assert "After extracting all readable rows" in text
    assert "canonical batch `/score-pick-request` bridge" in text
    assert "each row reaches exactly one terminal outcome" in text
    assert "Unsupported/OOD rows" in text
    assert "deterministic rejection semantics" in text


def test_full_model_target_is_rank_ready_not_capability_only():
    text = _instructions()
    assert "Full Model targets backend rank-eligible rows" in text
    assert "Model/capability availability is not rank readiness" in text
    assert "publication, rank-eligibility and calibrated-bound contract" in text
    assert "otherwise report blockers" in text


def test_host_preserves_probability_while_reporting_calibration_readiness():
    text = _instructions()
    assert "Do not infer rank eligibility from calibration phase" in text
    assert "Preserve any row-level sporting probability the backend marks publishable" in text
    assert "aggregate calibration/final approval is held" in text
    assert "only rank/card admission remains blocked" in text


def test_calibration_lifecycle_remains_server_owned():
    text = _instructions()
    assert "Calibration lifecycle is server-owned" in text
    assert "must not fit a calibrator" in text
    assert "advance calibration phases" in text
    assert "flip probability_publishable, rank_eligible, or final-approval flags" in text


def test_governance_safety_remains_non_executable():
    text = _instructions()
    assert "can_execute=false" in text
    assert "Never place, route, modify, approve, or cancel a wager/order" in text
    assert "Never enable execution" in text
