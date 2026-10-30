"""Proposed workflow contract; staged YAML is NOT installed under .github/workflows."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / "artifacts/wow-engine/v17/proposals/wow-v17-flint-engineering-worker.candidate.yml"
SCRIPT = ROOT / "artifacts/wow-engine/v17/engineering_openrouter_flint_worker.py"


def test_manual_dispatch_only_and_single_writer():
    source = WORKFLOW.read_text()
    assert "workflow_dispatch:" in source
    assert "schedule:" not in source
    assert "workflow_run:" not in source
    assert "wow-v17-engineering-implementation-single-writer" in source
    assert "cancel-in-progress: false" in source
    assert "environment: wow-engineering" in source
    assert "contents: read" in source
    assert "issues: read" in source
    assert "contents: write" not in source
    assert "actions: write" not in source


def test_explicit_external_data_consent_and_main_only():
    source = WORKFLOW.read_text()
    assert "consent_external_title:" in source
    assert "default: false" in source
    assert 'EXTERNAL_TITLE_CONSENT' in source
    assert 'refs/heads/main' in source
    assert 'SOURCE_EVENT' in source
    assert 'workflow_dispatch' in source
    assert 'persist-credentials: false' in source
    assert 'FLINT_SOURCE_SHA_CHANGED_AFTER_DISPATCH' in source
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in source


def test_canonical_queue_and_remote_writer_guard():
    source = WORKFLOW.read_text()
    assert "engineering_remote_fallback.py guard" in source
    assert "engineering_gemini_headless.py select" in source
    assert "engineering_dispatch_manifest.json" in source
    assert "--target" in source
    assert "--lease-group" in source
    assert 'test "$(jq -r' in source


def test_model_secret_is_single_step_and_no_write_actions():
    source = WORKFLOW.read_text()
    assert source.count("secrets.OPENROUTER_API_KEY") == 1
    assert "FLINT_ENGINEERING_ENABLED" in source
    assert "--acknowledged" in source
    assert "uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in source
    assert "git push" not in source
    assert "gh pr create" not in source
    assert "gh pr merge" not in source
    assert "gh workflow run" not in source
    assert "OpenRouter Flint research inference" in source
    assert SCRIPT.is_file()

def test_exact_canonical_incident_is_verified_independently_of_list_limit():
    source = WORKFLOW.read_text()
    assert 'gh issue view "$INCIDENT"' in source
    assert '--json number,title,state,updatedAt' in source
    assert 'FLINT_EXACT_ISSUE_NOT_OPEN' in source
    assert 'FLINT_INCIDENT_ID_INVALID' in source
    assert 'rows.append(exact)' in source
    # No issue body or credentials need to be read for this diagnostic.
    assert '--json number,title,body' not in source

def test_source_candidate_has_pinned_dependencies_and_verified_provenance():
    source = WORKFLOW.read_text()
    for full in (
        "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        assert "uses: " + full in source
    assert "uses: actions/checkout@v" not in source
    assert "uses: actions/setup-python@v" not in source
    assert "uses: actions/upload-artifact@v" not in source
    assert 'git cat-file -e "$GITHUB_SHA:$path"' in source
    for filename in (
        "engineering_remote_fallback.py",
        "engineering_gemini_headless.py",
        "engineering_openrouter_flint_worker.py",
        "engineering_openrouter_flint_probe.py",
        "engineering_dispatch_manifest.json",
    ):
        assert filename in source
    # Staged inert data can never itself install a protected trust-root workflow.
    assert not (ROOT / ".github/workflows/wow-v17-flint-engineering-worker.yml").exists()


def test_untrusted_issue_metadata_and_provider_wallclock_are_bounded():
    source = WORKFLOW.read_text()
    assert '"$RUNNER_TEMP/wow-flint/issues.json" "$INCIDENT"' in source
    assert 'type(exact.get("number")) is not int' in source
    assert 'str(exact["number"]) != sys.argv[4]' in source
    assert 'len(title) > 200' in source
    assert 'chr(0) in title' in source
    assert 'FLINT_ISSUE_TITLE_INVALID' in source
    assert 'FLINT_OPEN_ISSUE_EVIDENCE_INVALID' in source
    assert 'timeout --signal=TERM --kill-after=5s 75s python' in source
    assert 'FLINT_PROVIDER_WALLCLOCK_BUDGET_EXCEEDED' in source
    assert 'timeout-minutes: 8' in source
