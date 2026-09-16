from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
STATUS = ROOT / "artifacts" / "wow-engine" / "V17_PRODUCTION_STATUS.md"
EDITOR_SYNC = ROOT / "artifacts" / "wow-engine" / "V17_CUSTOM_GPT_EDITOR_SYNC.md"
USER_HEALTH = ROOT / "artifacts" / "wow-engine" / "V17_USER_JOURNEY_HEALTH.md"
ATTESTATION = ROOT / "artifacts" / "wow-engine" / "v17" / "WOW_BETTING_ENGINE_EDITOR_ATTESTATION.md"
INSTRUCTIONS = ROOT / "artifacts" / "wow-engine" / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
SCHEMA = ROOT / "artifacts" / "wow-engine" / "v17" / "openapi.wow-betting-engine.v17.yaml"
PERSIST_RESUME = ROOT / ".github" / "workflows" / "wow-v17-scout-persist-resume.yml"


def test_live_editor_status_cannot_claim_verified_before_real_canary():
    production = STATUS.read_text(encoding="utf-8")
    editor = EDITOR_SYNC.read_text(encoding="utf-8")
    attestation = ATTESTATION.read_text(encoding="utf-8")

    assert "LIVE_EDITOR_SYNC_PENDING" in production
    assert "LIVE_EDITOR_SYNC_VERIFIED" not in production
    assert "LIVE_EDITOR_SYNC_PENDING" in editor
    assert "SOURCE_CONTRACT_READY_LIVE_EDITOR_SYNC_PENDING" in attestation
    assert "scoreWowV17PickRequest exposed after reload" in attestation
    assert "canonical row-level Action receipt" in attestation


def test_canonical_action_contract_still_exposes_prop_batch_operation():
    instructions = INSTRUCTIONS.read_text(encoding="utf-8")
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    operation = schema["paths"]["/score-pick-request"]["post"]

    assert schema["servers"][0]["url"] == "https://wow-governed-probability-engine.onrender.com"
    assert operation["operationId"] == "scoreWowV17PickRequest"
    assert schema["components"]["securitySchemes"]["actionBearer"]["scheme"] == "bearer"
    assert "scoreWowV17PickRequest" in instructions
    assert "LIVE_GPT_ACTION_INVOCATION_BLOCKED" in instructions
    assert "scoring_attempted=true" in instructions


def test_persist_resume_cannot_pass_by_skipping_when_merge_sha_has_no_scout_run():
    workflow = PERSIST_RESUME.read_text(encoding="utf-8")

    assert 'echo "skip=true"' not in workflow
    assert "persistence is not applicable" not in workflow
    assert "using latest recoverable main run" in workflow
    assert "No completed recoverable Multi-Scout run found; fail closed." in workflow
    assert "Verify persistence receipt appears" in workflow


def test_user_journey_health_is_fail_closed_until_end_to_end_canary():
    health = USER_HEALTH.read_text(encoding="utf-8")

    assert "Status: **FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT**" in health
    assert "Use full model and provide me the best props across all sports." in health
    assert "workflow conclusion `success`" in health
    assert "skipped verification steps" in health
    assert "USER_JOURNEY_HEALTH = FAIL" in health
    assert "can_execute = false" in health
