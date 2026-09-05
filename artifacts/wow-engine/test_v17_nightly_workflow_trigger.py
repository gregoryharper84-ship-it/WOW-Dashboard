from pathlib import Path


def test_nightly_workflow_self_verifies_relevant_main_changes():
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "wow-v17-nightly-engineering-scan.yml"
    ).read_text()

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "push:" in workflow
    assert "- main" in workflow
    assert '".github/workflows/wow-v17-nightly-engineering-scan.yml"' in workflow
    assert '"artifacts/wow-engine/v17/nightly_incident_records.py"' in workflow
    assert "can_execute: false" in workflow
