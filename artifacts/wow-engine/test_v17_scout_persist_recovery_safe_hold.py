from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESUME_WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-scout-persist-resume.yml"


def test_automatic_scout_persistence_recovery_is_safe_held_during_1237():
    workflow = RESUME_WORKFLOW.read_text(encoding="utf-8")
    trigger_block = workflow.split("permissions:", 1)[0]

    assert "workflow_dispatch:" in trigger_block
    assert "workflow_run:" not in trigger_block
    assert "schedule:" not in trigger_block
    assert "cron:" not in trigger_block
    assert "group: wow-v17-scout-persist-resume-manual" in workflow


def test_manual_recovery_remains_governed_and_explicit():
    workflow = RESUME_WORKFLOW.read_text(encoding="utf-8")

    assert 'if [ "$GITHUB_EVENT_NAME" = "workflow_dispatch" ]; then' in workflow
    assert 'force_replay="true"' in workflow
    assert "SCOUT_PERSISTENCE_RECOVERY_COOLDOWN_ACTIVE" in workflow
    assert "can_execute" not in workflow or "can_execute=true" not in workflow
