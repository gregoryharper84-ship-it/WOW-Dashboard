from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
STATUS = ROOT / "artifacts" / "wow-engine" / "V17_PRODUCTION_STATUS.md"
EDITOR_SYNC = ROOT / "artifacts" / "wow-engine" / "V17_CUSTOM_GPT_EDITOR_SYNC.md"
USER_HEALTH = ROOT / "artifacts" / "wow-engine" / "V17_USER_JOURNEY_HEALTH.md"
PERSIST_RESUME = ROOT / ".github" / "workflows" / "wow-v17-scout-persist-resume.yml"


EDITOR_RESYNC = "RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED"


def test_repository_host_change_requires_editor_resync_and_keeps_user_journey_fail_closed():
    production = STATUS.read_text(encoding="utf-8")
    editor = EDITOR_SYNC.read_text(encoding="utf-8")
    health = USER_HEALTH.read_text(encoding="utf-8")

    assert f"LIVE_GPT_EDITOR_SYNC = {EDITOR_RESYNC}" in production
    assert f"Status: **{EDITOR_RESYNC}**" in editor
    assert "USER_JOURNEY_HEALTH = FAIL" in production
    assert "Status: **FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT**" in health
    assert f"LIVE_GPT_EDITOR_SYNC = {EDITOR_RESYNC}" in health
    assert "repository host-contract changes without a current editor save/reload and authenticated Action result" in health
    assert "Repository merge/CI does not itself update the OpenAI Custom GPT editor" in editor


def test_live_pick_request_operation_is_distinct_from_golden_journey_acceptance():
    editor = EDITOR_SYNC.read_text(encoding="utf-8")
    health = USER_HEALTH.read_text(encoding="utf-8")

    assert "scoreWowPickRequest" in editor
    assert "scoreWowPickRequest" in health
    assert "Use full model and provide me the best props across all sports." in health
    assert "backend health endpoint success" in health
    assert "route-mounted or HTTP-200 evidence without the golden user request" in health


def test_prizepicks_source_ingestion_failure_is_typed_separately_from_model_capability():
    editor = EDITOR_SYNC.read_text(encoding="utf-8")
    health = USER_HEALTH.read_text(encoding="utf-8")

    assert "SOURCE_PAGE_UNREADABLE:<page_number>" in editor
    assert "SOURCE_PAGE_UNREADABLE:<page_number>" in health
    assert "FAIL_SOURCE_INGESTION" in health
    assert "this is not MODEL_UNAVAILABLE" in health
    assert "never claim `omitted rows = 0`" in editor


def test_merge_sha_without_scout_run_forces_current_main_persistence_replay():
    workflow = PERSIST_RESUME.read_text(encoding="utf-8")

    assert 'echo "skip=true"' not in workflow
    assert "persistence is not applicable to this workflow_run event" not in workflow
    assert 'force_replay="true"' in workflow
    assert "Capture persistence receipt baseline" in workflow
    assert "BASELINE_COUNT" in workflow
    assert 'receipt_count" -gt "$BASELINE_COUNT' in workflow
    assert "current-main replay acceptance remains open" in workflow
    assert "No completed recoverable Multi-Scout run found; fail closed." in workflow


def test_user_journey_health_remains_fail_closed_until_full_path_canary():
    health = USER_HEALTH.read_text(encoding="utf-8")

    assert "workflow conclusion `success`" in health
    assert "skipped verification steps" in health
    assert "Discovery-only candidates do not satisfy the journey" in health
    assert "V17_TERMINAL_REDUCER" in health
    assert "can_execute=false" in health
    assert "USER_JOURNEY_HEALTH = FAIL" in health
    assert f"LIVE_GPT_EDITOR_SYNC = {EDITOR_RESYNC}" in health
