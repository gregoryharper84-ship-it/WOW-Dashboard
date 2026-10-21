"""LLM-free exact-source adoption: true App write identity, no review substitution."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
FILE = ROOT / "artifacts/wow-engine/v17/engineering_authorized_adoption.py"
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-authorized-adoption.yml"
spec = spec_from_file_location("engineering_authorized_adoption", FILE)
mod = module_from_spec(spec)
spec.loader.exec_module(mod)

SOURCE_SHA = "a" * 40
BASE_SHA = "b" * 40
OTHER_SHA = "c" * 40
PATHS = [
    ".github/workflows/wow-v17-render-production-deploy.yml",
    "artifacts/wow-engine/tests/test_v17_github_release_journal.py",
    "artifacts/wow-engine/tests/test_v17_render_production_deploy_workflow.py",
    "artifacts/wow-engine/tests/test_v17_render_release_provenance_gate.py",
    "artifacts/wow-engine/v17/github_release_journal.py",
    "artifacts/wow-engine/v17/render_deployment_pointer.py",
    "artifacts/wow-engine/v17/test_render_deployment_pointer.py",
]


def evidence():
    pr = {
        "number": 1862, "state": "open", "changed_files": len(PATHS),
        "head": {"sha": SOURCE_SHA, "repo": {"full_name": mod.REPO}},
        "base": {"sha": BASE_SHA, "ref": "main", "repo": {"full_name": mod.REPO}},
    }
    files = [{"filename": path, "status": "modified"} for path in PATHS]
    checks = {"total_count": 3, "check_runs": [
        {"id": k + 1, "name": name, "status": "completed",
         "conclusion": "success", "app": {"id": mod.ACTIONS_APP_ID},
         "head_sha": SOURCE_SHA}
        for k, name in enumerate(mod.REQUIRED_CHECKS)
    ]}
    return pr, files, checks


def verify(pr, files, checks):
    return mod.validate_evidence(
        pr, files, checks, requested_pr=1862, requested_sha=SOURCE_SHA)


def test_real_seven_file_release_source_accepted_with_genuine_ci():
    pr, files, checks = evidence()
    assert verify(pr, files, checks) == (BASE_SHA, sorted(PATHS))
    assert mod.branch_for(1862, SOURCE_SHA) == "wow-engineering/exact-replay-1862-aaaaaaaaaaaa"


@pytest.mark.parametrize("mutation,code", [
    ("wrong_repo", "SOURCE_PR_OR_HEAD_PROVENANCE_HOLD"),
    ("source_drift", "SOURCE_PR_OR_HEAD_PROVENANCE_HOLD"),
    ("closed_pr", "SOURCE_PR_OR_HEAD_PROVENANCE_HOLD"),
    ("wrong_base", "SOURCE_PR_OR_HEAD_PROVENANCE_HOLD"),
    ("inventory_missing", "SOURCE_FILE_INVENTORY_INCOMPLETE_HOLD"),
    ("untrusted_workflow_path", "SOURCE_CHANGE_SCOPE_OR_RENAME_HOLD"),
    ("renamed_path", "SOURCE_CHANGE_SCOPE_OR_RENAME_HOLD"),
    ("deleted_file", "SOURCE_CHANGE_SCOPE_OR_RENAME_HOLD"),
    ("duplicate_file", "DUPLICATE_SOURCE_PATH_HOLD"),
    ("foreign_app", "SOURCE_REQUIRED_REGRESSION_MISSING_HOLD"),
    ("missing_check", "SOURCE_REQUIRED_REGRESSION_MISSING_HOLD"),
    ("newest_check_failed", "SOURCE_REQUIRED_REGRESSION_RED_HOLD"),
    ("missing_check_page", "SOURCE_CHECK_INVENTORY_INCOMPLETE_HOLD"),
])
def test_bypass_attempts_fail_closed(mutation, code):
    pr, files, checks = evidence()
    if mutation == "wrong_repo":
        pr["head"]["repo"]["full_name"] = "attacker/WOW-Dashboard"
    elif mutation == "source_drift":
        pr["head"]["sha"] = OTHER_SHA
    elif mutation == "closed_pr":
        pr["state"] = "closed"
    elif mutation == "wrong_base":
        pr["base"]["ref"] = "release"
    elif mutation == "inventory_missing":
        pr["changed_files"] += 1
    elif mutation == "untrusted_workflow_path":
        files[0]["filename"] = ".github/actions/wow-engineering-token/action.yml"
    elif mutation == "renamed_path":
        files[0]["status"] = "renamed"
    elif mutation == "deleted_file":
        files[0]["status"] = "removed"
    elif mutation == "duplicate_file":
        files[1]["filename"] = files[0]["filename"]
    elif mutation == "foreign_app":
        checks["check_runs"][0]["app"]["id"] = 9999
    elif mutation == "missing_check":
        checks["check_runs"][0]["name"] = "Unrelated check"
    elif mutation == "newest_check_failed":
        checks["check_runs"].append({
            **checks["check_runs"][0], "id": 999, "conclusion": "failure"
        })
        checks["total_count"] += 1
    elif mutation == "missing_check_page":
        checks["total_count"] += 1
    with pytest.raises(mod.AdoptionHold, match=code):
        verify(pr, files, checks)


def test_invalid_pr_request_and_too_many_files_fail_closed():
    pr, files, checks = evidence()
    with pytest.raises(mod.AdoptionHold, match="SOURCE_REQUEST_INVALID_HOLD"):
        mod.validate_evidence(pr, files, checks, requested_pr=1862,
                              requested_sha="4427e9fa")
    with pytest.raises(mod.AdoptionHold, match="SOURCE_FILE_COUNT_INVALID_HOLD"):
        mod.source_paths(files * 4)


def test_empty_or_owner_read_write_token_cannot_mutate(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "read-token")
    monkeypatch.delenv("APP_TOKEN", raising=False)
    with pytest.raises(mod.AdoptionHold, match="APP_TOKEN_AND_READ_TOKEN_REQUIRED_HOLD"):
        mod.integrate(1862, SOURCE_SHA)
    monkeypatch.setenv("APP_TOKEN", "read-token")
    with pytest.raises(mod.AdoptionHold, match="READ_AND_WRITE_IDENTITY_NOT_SEPARATED_HOLD"):
        mod.integrate(1862, SOURCE_SHA)


def test_wrong_model_execution_setting_holds_before_any_write(monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "app-token")
    monkeypatch.setenv("GH_TOKEN", "read-token")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "true")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    with pytest.raises(mod.AdoptionHold, match="DRY_RUN_INVARIANTS_HOLD"):
        mod.integrate(1862, SOURCE_SHA)


def test_source_mode_and_path_drift_are_challenged(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "read-only-test-token")
    def fake(*args, **kwargs):
        if args[:2] == ("git", "fetch"):
            return ""
        if args[:2] == ("git", "rev-parse"):
            return OTHER_SHA
        if args[:2] == ("git", "merge-base"):
            return BASE_SHA
        if args[:3] == ("git", "diff", "--name-only"):
            return "\n".join(PATHS)
        if args[:2] == ("git", "ls-tree"):
            return "100644 blob 1234 " + args[-1]
        raise AssertionError(args)
    monkeypatch.setattr(mod, "call", fake)
    monkeypatch.setattr(mod.subprocess, "run", lambda *args, **kw: type("R", (), {"returncode": 1})())
    with pytest.raises(mod.AdoptionHold, match="MAIN_CHANGED_SOURCE_PATH_HOLD"):
        mod.verify_git_source(BASE_SHA, OTHER_SHA, SOURCE_SHA, sorted(PATHS))


def test_private_source_fetch_requires_readonly_authentication(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(mod.AdoptionHold, match="READ_TOKEN_REQUIRED_FOR_SOURCE_FETCH_HOLD"):
        mod.verify_git_source(BASE_SHA, OTHER_SHA, SOURCE_SHA, sorted(PATHS))


def test_protected_workflow_is_manual_and_never_auto_approves_or_deploys():
    text = WORKFLOW.read_text()
    parsed = yaml.safe_load(text)
    triggers = parsed.get("on", parsed.get(True, {}))
    assert set(triggers) == {"workflow_dispatch"}
    assert parsed["concurrency"] == {
        "group": "wow-v17-engineering-implementation-single-writer",
        "cancel-in-progress": False,
    }
    assert parsed["jobs"]["adopt"]["environment"] == "wow-engineering"
    assert "github.actor == 'gregoryharper84-ship-it'" in parsed["jobs"]["adopt"]["if"]
    assert parsed["permissions"] == {
        "contents": "read", "pull-requests": "read", "checks": "read"
    }
    assert "persist-credentials: false" in text
    assert ".github/actions/wow-engineering-token" in text
    assert "secrets.WOW_ENGINEERING_APP_PRIVATE_KEY" in text
    assert "SOURCE_SHA: ${{ inputs.source_sha }}" in text
    assert "SOURCE_PR: ${{ inputs.source_pr }}" in text
    for unsafe in ["pull_request_target:", "gh pr merge", "RENDER_API_KEY",
                   "secrets.WOW_QA_APP_PRIVATE_KEY", "secrets.WOW_RELEASE_APP_PRIVATE_KEY"]:
        assert unsafe not in text
    source = FILE.read_text()
    assert "f\"Original author/source: PR #" in source
    assert "The App did not independently author, assess or approve" in source
    assert "POST_CREATE_APP_PR_IDENTITY_NOT_VERIFIED_HOLD" in source
    assert "MAIN_MOVED_BEFORE_PUSH_HOLD" in source
