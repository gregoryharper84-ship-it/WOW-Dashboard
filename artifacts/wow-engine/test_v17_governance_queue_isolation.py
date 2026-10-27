"""P0 regression: targetless governance observers must not starve exact-PR checks.

These are static protection contracts. Live GitHub Actions queue/receipt acceptance is
still required, and a source CI pass never means independent QA/Release acceptance.
"""

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
QA_PATH = REPO / ".github/workflows/wow-v17-independent-qa-check.yml"
RELEASE_PATH = REPO / ".github/workflows/wow-v17-release-authority-check.yml"


def _group(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in source.splitlines() if line.startswith("  group: ")]
    assert len(lines) == 1, "Exactly one workflow-level concurrency group is required"
    return lines[0]


def test_exact_pr_qa_serialization_survives_no_target_fallback():
    source = QA_PATH.read_text(encoding="utf-8")
    group = _group(QA_PATH)
    assert "inputs.pr_number || github.event.workflow_run.pull_requests[0].number" in group
    assert "|| github.run_id }}" in group
    assert "|| 'AUTO'" not in group
    # No-PR workflow_run events are filtered before credentialed QA job admission.
    assert "(github.event_name != 'workflow_run' || github.event.workflow_run.pull_requests[0])" in source
    assert "cancel-in-progress: false" in source
    assert "queue: max" in source
    assert "environment: wow-qa" in source


def test_skipped_qa_does_not_congest_authentic_release_policy_queue():
    source = RELEASE_PATH.read_text(encoding="utf-8")
    group = _group(RELEASE_PATH)
    assert "inputs.pr_number || github.event.workflow_run.pull_requests[0].number" in group
    assert "(github.event.workflow_run.conclusion == 'skipped' && github.run_id)" in group
    # Only provably skipped upstream QA gets a unique admission group.
    # Successful/failed QA without PR metadata remains serialized until its signed
    # policy receipt identifies the exact target.
    assert "|| 'AUTO' }}" in group
    assert "(github.event_name != 'workflow_run' || github.event.workflow_run.conclusion != 'skipped')" in source
    assert "cancel-in-progress: false" in source
    assert "queue: max" in source
    assert "environment: wow-release" in source


def test_no_new_qa_or_release_proof_authority_was_added():
    qa = QA_PATH.read_text(encoding="utf-8")
    release = RELEASE_PATH.read_text(encoding="utf-8")
    assert "vars.WOW_QA_APP_ID" in qa
    assert "vars.WOW_RELEASE_APP_ID" in release
    assert "pull-requests: read" in qa and "pull-requests: read" in release
    assert "checks: read" in qa and "checks: read" in release
    assert "GITHUB_TOKEN" in qa and "GITHUB_TOKEN" in release
