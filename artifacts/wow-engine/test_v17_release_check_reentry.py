"""Adversarial protected-main tests for GitHub App Release-check certifier wakeups."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-existing-pr-governance-review.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
HEAD = "a" * 40
CHECK = {
    "id": 123, "name": "WOW Release Authority exact-head",
    "head_sha": HEAD, "status": "completed", "conclusion": "success",
    "app": {"id": 5245853},
}
PR = {
    "number": 42, "state": "open", "draft": False,
    "head": {"sha": HEAD, "repo": {"full_name": REPO}},
    "base": {"ref": "main", "repo": {"full_name": REPO}},
}

def test_check_run_reentry_keeps_readonly_exact_head_app_provenance():
    raw = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)
    event = workflow.get("on", workflow.get(True))
    assert event["check_run"]["types"] == ["completed"]
    assert "workflow_run" in event and "pull_request_target" in event
    assert "contents: read" in raw and "contents: write" not in raw
    assert "cancel-in-progress: false" in raw
    assert "github.event.check_run.head_sha" in raw
    assert "CHECK_RUN_PR:" in raw and "CHECK_RUN_PR:-" in raw
    assert "RELEASE_CHECK_RECEIPT_INVENTORY_INVALID" in raw
    assert ".app.id == 5245853" in raw
    assert "EXACT_HEAD_RECEIPT_ALREADY_PRESENT" in raw
    assert "gh pr merge" not in raw and "gh pr review" not in raw

def _run_route(tmp_path, *, check=None, associated=None, pr=None, artifacts=None):
    if shutil.which("jq") is None:
        pytest.skip("jq is required by the GitHub Actions runner contract")
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(s for s in data["jobs"]["certify-existing-pr"]["steps"]
                if s.get("id") == "route")
    assert step["env"]["GH_TOKEN"] == "\u0024{{ github.token }}"
    output = tmp_path / "output.txt"
    check = CHECK if check is None else check
    associated = [PR] if associated is None else associated
    pr = PR if pr is None else pr
    artifacts = {"total_count": 0, "artifacts": []} if artifacts is None else artifacts
    # The checked-in runner step executes unchanged; mock only its authenticated API.
    mock = r'''
gh() {
  case "$2" in
    */check-runs/123) printf '%s\n' "$CHECK_JSON" ;;
    */commits/*/pulls\?per_page=100) printf '%s\n' "$ASSOCIATED_JSON" ;;
    */pulls/42) printf '%s\n' "$PR_JSON" ;;
    */actions/artifacts\?name=*) printf '%s\n' "$ARTIFACTS_JSON" ;;
    *) echo "UNEXPECTED_GITHUB_ENDPOINT:$2" >&2; return 2 ;;
  esac
}
'''
    result = subprocess.run(
        ["bash", "-c", mock + "\n" + step["run"]],
        env=os.environ | {
            "GH_TOKEN": "read-only-fixture",
            "GITHUB_REPOSITORY": REPO, "GITHUB_OUTPUT": str(output),
            "EVENT_NAME": "check_run", "REVIEW_ACTIVE": "false",
            "SOURCE_RUN_ID": "", "SOURCE_CONCLUSION": "",
            "CHECK_RUN_ID": "123", "CHECK_RUN_HEAD": HEAD,
            "CHECK_JSON": json.dumps(check),
            "ASSOCIATED_JSON": json.dumps(associated),
            "PR_JSON": json.dumps(pr),
            "ARTIFACTS_JSON": json.dumps(artifacts),
        },
        text=True, capture_output=True, check=False,
    )
    return result, output.read_text() if output.exists() else ""

def test_valid_late_release_check_wakes_exact_head_certifier(tmp_path):
    proc, output = _run_route(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert output == "pr_number=42\napplicable=true\n"

@pytest.mark.parametrize("overrides", [
    {"app": {"id": 1}}, {"name": "WOW Independent QA exact-head"},
    {"head_sha": "b" * 40}, {"id": 999},
    {"status": "in_progress"}, {"conclusion": "failure"},
    {"conclusion": "action_required"},
])
def test_foreign_stale_non_successful_check_never_wakes(tmp_path, overrides):
    proc, output = _run_route(tmp_path, check=CHECK | overrides)
    assert proc.returncode == 0, proc.stderr
    assert "applicable=false" in output
    assert "pr_number=" not in output

@pytest.mark.parametrize("associated", [[], [PR, PR],
    [PR | {"state": "closed"}],
    [PR | {"head": {"sha": "b" * 40, "repo": {"full_name": REPO}}}],
    [PR | {"base": {"ref": "other", "repo": {"full_name": REPO}}}],
])
def test_unassociated_or_ambiguous_pr_never_wakes(tmp_path, associated):
    proc, output = _run_route(tmp_path, associated=associated)
    assert proc.returncode == 0, proc.stderr
    assert "applicable=false" in output

def test_pr_head_moves_between_association_and_readback_fails_closed(tmp_path):
    proc, output = _run_route(
        tmp_path, pr=PR | {"head": {"sha": "b" * 40, "repo": {"full_name": REPO}}})
    assert proc.returncode != 0
    assert "RELEASE_CHECK_PR_HEAD_MOVED" in proc.stderr
    assert "applicable=true" not in output

def test_existing_immutable_receipt_prevents_duplicate_certification(tmp_path):
    proc, output = _run_route(tmp_path, artifacts={
        "total_count": 1,
        "artifacts": [{"name": "wow-v17-engineering-governance-" + HEAD,
                       "expired": False}],
    })
    assert proc.returncode == 0, proc.stderr
    assert "EXACT_HEAD_RECEIPT_ALREADY_PRESENT" in output
    assert "applicable=true" not in output

@pytest.mark.parametrize("inventory", [
    {"total_count": 2, "artifacts": []},
    {"total_count": "0", "artifacts": []},
    {"total_count": 0, "artifacts": {}},
])
def test_truncated_or_malformed_receipt_inventory_fails_closed(tmp_path, inventory):
    proc, output = _run_route(tmp_path, artifacts=inventory)
    assert proc.returncode != 0
    assert "RELEASE_CHECK_RECEIPT_INVENTORY_INVALID" in proc.stderr
    assert "applicable=true" not in output
