from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "wow-v17-scout-brain-persist.yml"
)


def test_persist_workflow_serializes_all_scout_database_writers():
    text = WORKFLOW.read_text(encoding="utf-8")

    identity = "${{ inputs.run_id || github.event.workflow_run.id }}"
    assert "concurrency:" in text
    assert "group: wow-v17-scout-brain-persist-db-writer" in text
    assert "cancel-in-progress: false" in text
    assert f"SOURCE_RUN_ID: {identity}" in text


def test_persist_concurrency_does_not_cancel_the_active_writer():
    text = WORKFLOW.read_text(encoding="utf-8")
    block = text.split("concurrency:", 1)[1].split("jobs:", 1)[0]
    assert "group: wow-v17-scout-brain-persist-db-writer" in block
    assert "cancel-in-progress: false" in block
