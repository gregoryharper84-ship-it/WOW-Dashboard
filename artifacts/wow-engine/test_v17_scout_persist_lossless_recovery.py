from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PERSIST_WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-scout-brain-persist.yml"
RESUME_WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-scout-persist-resume.yml"


def test_global_writer_has_lossless_cancelled_source_recovery():
    persist = PERSIST_WORKFLOW.read_text(encoding="utf-8")
    resume = RESUME_WORKFLOW.read_text(encoding="utf-8")

    assert "group: wow-v17-scout-brain-persist-global" in persist
    assert "cancel-in-progress: false" in persist

    assert "SCOUT_PERSIST_LOSSLESS_RECOVERY_EPOCH:" in resume
    assert "resolve_oldest_cancelled_without_receipt()" in resume
    assert "status=cancelled&per_page=100&page=" in resume
    assert 'capture("source=(?<source_id>[0-9]+)")' in resume
    assert "wow-v17-scout-brain-persistence-${source_id}" in resume
    assert "Recovering oldest cancelled Scout persistence source" in resume


def test_lossless_recovery_is_oldest_first_and_fails_closed_if_scan_is_bounded():
    resume = RESUME_WORKFLOW.read_text(encoding="utf-8")

    assert "sort -u -t '|' -k1,1 -k2,2" in resume
    assert "SCOUT_PERSIST_RECOVERY_MAX_PAGES:" in resume
    assert "SCOUT_PERSISTENCE_RECOVERY_SCAN_INCOMPLETE" in resume
    assert "Lossless Scout persistence recovery scan could not prove completeness; fail closed." in resume


def test_lossless_recovery_does_not_weaken_scout_governance():
    resume = RESUME_WORKFLOW.read_text(encoding="utf-8")
    persist = PERSIST_WORKFLOW.read_text(encoding="utf-8")

    assert 'force_replay="true"' in resume
    assert 'if [ "$GITHUB_EVENT_NAME" = "workflow_dispatch" ]; then' in resume
    assert "SCOUT_PERSISTENCE_RECOVERY_COOLDOWN_ACTIVE" in resume
    assert "cancel-in-progress: false" in persist
