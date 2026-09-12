from pathlib import Path


def _workflow_text() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / ".github/workflows/wow-v17-scout-brain-persist.yml").read_text()


def test_failed_multiscout_runs_are_allowed_to_reach_artifact_recovery():
    text = _workflow_text()
    assert "github.event.workflow_run.conclusion != 'cancelled'" in text
    assert "github.event.workflow_run.conclusion == 'success'" not in text


def test_persistence_requires_recoverable_artifact_before_sync():
    text = _workflow_text()
    assert "id: artifact" in text
    assert "present=false" in text
    assert "steps.artifact.outputs.present == 'true'" in text
    assert "persistence blocked without fabricating a run" in text


def test_governance_contract_remains_research_only():
    text = _workflow_text()
    assert "scout_brain_sync.py" in text
    assert "scout_brain_edge_sync.py" in text
    assert "WOW_SCOUT_DATABASE_URL" in text
    assert "id-token: write" in text
    assert "wow-v17-scout-brain-persist" in text


def test_missing_database_secret_uses_oidc_edge_fallback_instead_of_skipping():
    text = _workflow_text()
    assert "using short-lived GitHub OIDC" in text
    assert "steps.credential.outputs.configured != 'true'" in text
    assert "Locate recoverable Scout discovery artifact" in text
