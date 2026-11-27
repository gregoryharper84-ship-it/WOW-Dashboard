"""Prevent CI-completion fanout from exhausting the Scout recovery queue.

A direct multiscout -> Scout writer workflow_run handles immediate persistence.
The independent retry sweep must be bounded to cron/manual invocations instead
of rerunning on every green/skipped Morning-Green CI continuation.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESUME = ROOT / ".github/workflows/wow-v17-scout-persist-resume.yml"
PERSIST = ROOT / ".github/workflows/wow-v17-scout-brain-persist.yml"
MORNING = ROOT / ".github/workflows/wow-v17-morning-green-continuation.yml"


def test_recovery_has_no_unbounded_ci_workflow_run_fanout():
    text = RESUME.read_text(encoding="utf-8")
    assert 'cron: "*/15 * * * *"' in text
    assert "workflow_dispatch:" in text
    assert "workflow_run:" not in text
    assert "wow-v17-morning-green-continuation" not in text
    assert "group: wow-v17-scout-persist-resume-sweep" in text
    assert "cancel-in-progress: false" in text


def test_native_multiscout_persistence_is_still_immediate():
    text = PERSIST.read_text(encoding="utf-8")
    assert 'workflows: ["wow-v17-nightly-multiscout"]' in text
    assert "types: [completed]" in text
    assert "workflow_dispatch:" in text
    assert "group: wow-v17-scout-brain-persist-global" in text
    assert "cancel-in-progress: false" in text
    assert "wow-v17-scout-brain-persistence-" in text


def test_scheduled_recovery_preserves_lossless_receipts_and_cooldown():
    text = RESUME.read_text(encoding="utf-8")
    assert "resolve_oldest_cancelled_without_receipt()" in text
    assert "SCOUT_PERSIST_LOSSLESS_RECOVERY_EPOCH:" in text
    assert "SCOUT_PERSIST_RECOVERY_MAX_PAGES:" in text
    assert "SCOUT_PERSISTENCE_RECOVERY_SCAN_INCOMPLETE" in text
    assert "SCOUT_PERSISTENCE_RECOVERY_COOLDOWN_ACTIVE" in text
    assert "Check for existing persistence receipt" in text
    assert "Detect same-source persistence writer state" in text
    assert "gh workflow run wow-v17-scout-brain-persist.yml" in text
    assert "Verify persistence receipt appears" in text
    assert 'TARGET_SHA: ""' in text


def test_morning_green_continuation_still_wakes_on_governed_ci():
    text = MORNING.read_text(encoding="utf-8")
    assert 'workflows: ["wow-verify", "wow-engine-verify"]' in text
    assert "types: [completed]" in text
    assert "Verify trusted exact-head governance receipt" in text
    assert "Morning-Green-Autonomous: true" in text
