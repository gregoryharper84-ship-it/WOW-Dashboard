from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
STATUS = ROOT / "artifacts" / "wow-engine" / "V17_PRODUCTION_STATUS.md"
EDITOR_SYNC = ROOT / "artifacts" / "wow-engine" / "V17_CUSTOM_GPT_EDITOR_SYNC.md"
USER_HEALTH = ROOT / "artifacts" / "wow-engine" / "V17_USER_JOURNEY_HEALTH.md"
PERSIST_RESUME = ROOT / ".github" / "workflows" / "wow-v17-scout-persist-resume.yml"


def test_editor_update_does_not_imply_live_action_or_user_journey_pass():
    production = STATUS.read_text(encoding="utf-8")
    editor = EDITOR_SYNC.read_text(encoding="utf-8")
    health = USER_HEALTH.read_text(encoding="utf-8")

    assert "LIVE_GPT_EDITOR_SYNC = EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED" in production
    assert "EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED" in editor
    assert "RESYNC_REQUIRED_AFTER_PR617" not in production
    assert "RESYNC_REQUIRED_AFTER_PR617" not in editor
    assert "USER_JOURNEY_HEALTH = FAIL" in production
    assert "Status: **FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT**" in health
    assert "current editor import/update evidence without a fresh authenticated Action result" in health


def test_live_pick_request_operation_is_distinct_from_golden_journey_acceptance():
    editor = EDITOR_SYNC.read_text(encoding="utf-8")
    health = USER_HEALTH.read_text(encoding="utf-8")

    assert "scoreWowPickRequest" in editor
    assert "compatibility aliases only" in editor
    assert "scoreWowPickRequest" in health
    assert "Use full model and provide me the best props across all sports." in health
    assert "backend health endpoint success" in health
    assert "route-mounted or HTTP-200 evidence without the golden user request" in health


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
    assert "LIVE_GPT_EDITOR_SYNC = EDITOR_UPDATED__LIVE_ACTION_ACCEPTANCE_REQUIRED" in health
