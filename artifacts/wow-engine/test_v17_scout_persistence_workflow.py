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
    # Every sync step is guarded on present == 'true'. Echoing a summary line
    # from a step that exits 0 left the job concluding success with nothing
    # persisted, so the missing-artifact path has to fail the job instead.
    assert "persistence failed closed rather than reporting success" in text
    assert "persistence blocked without fabricating a run" not in text


def test_a_pull_request_source_run_is_the_only_missing_artifact_exemption():
    text = _workflow_text()
    # A pull_request source run skips discovery by design, so it has no
    # artifact and nothing to persist. Every other source run that produced
    # none is a lost run.
    assert 'source_event=$(gh api "/repos/${GITHUB_REPOSITORY}/actions/runs/${SOURCE_RUN_ID}"' in text
    assert 'if [ "$source_event" = "pull_request" ]' in text
    assert "discovery is skipped by design and persistence is not applicable" in text


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
