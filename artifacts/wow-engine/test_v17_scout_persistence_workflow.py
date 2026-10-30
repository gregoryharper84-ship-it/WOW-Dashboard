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
    # Verified code-only runs are neutral no-ops, never persistence receipts.
    # True discovery with missing artifacts remains a hard typed HOLD, not PASS.
    assert "SCOUT_DISCOVERY_NOT_APPLICABLE" in text
    assert 'if [ "$discovery_state" != "success" ]; then' in text
    assert "SCOUT_DISCOVERY_ARTIFACT_MISSING_HOLD" in text
    assert "SCOUT_SKIPPED_DISCOVERY_ARTIFACT_CONFLICT_HOLD" in text


def test_verification_only_source_runs_require_actual_skipped_discovery():
    text = _workflow_text()
    # push / PR / workflow_run are neutral only when the exact source's actual
    # discovery job was SKIPPED and its contract-verification job succeeded.
    # Scheduled/dispatch discovery and any genuinely missing artifact HOLD.
    assert "source_json=$(gh api" in text
    assert "jobs_json=$(gh api" in text
    assert "source_event=$(jq -r '.event // \"\"' <<<\"$source_json\")" in text
    assert '[ "$source_event" = "pull_request" ]' in text
    assert '[ "$source_event" = "push" ]' in text
    assert '[ "$source_event" = "workflow_run" ]' in text
    assert '[ "$discovery_state" = "skipped" ]' in text
    assert '[ "$verification_state" = "success" ]' in text
    assert "SCOUT_DISCOVERY_NOT_APPLICABLE" in text


def test_governance_contract_remains_research_only():
    text = _workflow_text()
    assert "python -m v17.scout_brain_sync" in text
    assert "python -m v17.scout_brain_edge_sync" in text
    assert "WOW_SCOUT_DATABASE_URL" in text
    assert "id-token: write" in text
    assert "wow-v17-scout-brain-persist" in text


def test_missing_database_secret_uses_oidc_edge_fallback_instead_of_skipping():
    text = _workflow_text()
    assert "using short-lived GitHub OIDC" in text
    assert "steps.credential.outputs.configured != 'true'" in text
    assert "Locate recoverable Scout discovery artifact" in text


def test_successful_persistence_materializes_today_and_tomorrow_inline():
    text = _workflow_text()
    persist = text.index("Persist through governed Supabase Edge OIDC")
    receipt = text.index("Upload persistence receipt")
    materialize = text.index("Materialize current Scout boards after successful persistence")
    assert persist < receipt < materialize
    assert "if: steps.artifact.outputs.present == 'true'" in text[materialize:]
    assert 'today=$(date -u +%F)' in text[materialize:]
    assert "tomorrow=$(date -u -d '+1 day' +%F)" in text[materialize:]
    assert 'python -m v17.scout_board_edge_materializer --date "$today"' in text[materialize:]
    assert 'python -m v17.scout_board_edge_materializer --date "$tomorrow"' in text[materialize:]
