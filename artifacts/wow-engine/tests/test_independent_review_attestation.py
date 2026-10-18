import importlib.util
from datetime import datetime, timezone
from pathlib import Path

MODULE = Path(__file__).parents[1] / "v17" / "independent_review_attestation.py"
SPEC = importlib.util.spec_from_file_location("independent_review_attestation", MODULE)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
SHA = "a" * 40
OTHER = "b" * 40


def evidence():
    return dict(
        repository=m.CANONICAL_REPO,
        expected_sha=SHA,
        pr={"state": "open", "draft": False, "merged_at": None,
            "head": {"sha": SHA, "repo": {"full_name": m.CANONICAL_REPO}},
            "base": {"ref": "main", "repo": {"full_name": m.CANONICAL_REPO}},
            "user": {"login": "gregoryharper84-ship-it"}, "changed_files": 1},
        changed_files=[{"filename": "artifacts/wow-engine/v17/daily_snapshot_runtime.py"}],
        reviews=[{"id": 101, "user": {"login": "independent-code-reviewer"},
                  "state": "APPROVED", "commit_id": SHA,
                  "submitted_at": "2026-10-17T22:00:00Z"}],
        checks=[{"id": i + 1, "name": name, "head_sha": SHA, "status": "completed",
                 "conclusion": "success", "started_at": "2026-10-17T22:01:00Z",
                 "app": {"id": m.ACTIONS_APP}}
                for i, name in enumerate(m.REQUIRED_REGRESSION)],
        trusted_reviewers=frozenset({"independent-code-reviewer"}),
        complete_inventory=True, now=datetime(2026, 10, 17, 22, 30, tzinfo=timezone.utc),
    )


def run(**overrides):
    e = evidence()
    e.update(overrides)
    return m.evaluate(**e)


def test_authorized_review_evidence_not_a_release_approval():
    result = run()
    assert result["state"] == "REVIEW_EVIDENCE_VERIFIED"
    assert result["can_execute"] is False
    assert result["qa_release_complete"] is False
    assert result["disposition"] == "BLOCKED_WITH_EXACT_REASON"


def test_empty_allowlist_denied():
    assert run(trusted_reviewers=frozenset())["code"] == "INDEPENDENT_REVIEWER_NOT_CONFIGURED"


def test_unknown_reviewer_denied():
    assert run(trusted_reviewers=frozenset({"nobody"}))["code"] == "EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"


def test_self_review_denied():
    assert run(trusted_reviewers=frozenset({"gregoryharper84-ship-it"}))["code"] == "INDEPENDENT_REVIEWER_NOT_CONFIGURED"


def test_stale_sha_reviewer_denied():
    e = evidence()
    e["reviews"][0]["commit_id"] = OTHER
    assert m.evaluate(**e)["code"] == "EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"


def test_later_changes_requested_overrides_approval():
    e = evidence()
    e["reviews"].append({**e["reviews"][0], "id": 102, "state": "CHANGES_REQUESTED",
                         "submitted_at": "2026-10-17T22:05:00Z"})
    assert m.evaluate(**e)["code"] == "EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"


def test_later_new_approval_restores():
    e = evidence()
    e["reviews"].extend([
        {**e["reviews"][0], "id": 102, "state": "CHANGES_REQUESTED",
         "submitted_at": "2026-10-17T22:05:00Z"},
        {**e["reviews"][0], "id": 103, "state": "APPROVED",
         "submitted_at": "2026-10-17T22:06:00Z"},
    ])
    assert m.evaluate(**e)["state"] == "REVIEW_EVIDENCE_VERIFIED"


def test_incomplete_inventory_rejected():
    assert run(complete_inventory=False)["code"] == "REVIEW_OR_CI_INVENTORY_INCOMPLETE"


def test_unknown_sha_rejected():
    assert run(expected_sha="bad")["code"] == "EXPECTED_SHA_INVALID"


def test_changed_head_after_review_rejected():
    e = evidence()
    e["pr"]["head"]["sha"] = OTHER
    assert m.evaluate(**e)["code"] == "PR_SHA_REPO_OR_BASE_MISMATCH"


def test_pr_draft_rejected():
    e = evidence()
    e["pr"]["draft"] = True
    assert m.evaluate(**e)["code"] == "PR_NOT_OPEN_READY"


def test_pr_fork_rejected():
    e = evidence()
    e["pr"]["head"]["repo"]["full_name"] = "other/repo"
    assert m.evaluate(**e)["code"] == "PR_SHA_REPO_OR_BASE_MISMATCH"


def test_reject_workflow_path():
    e = evidence()
    e["changed_files"][0]["filename"] = ".github/workflows/workflow.yml"
    assert m.evaluate(**e)["code"] == "TRUST_ROOT_REQUIRES_PROTECTED_AUTHORIZATION"


def test_reject_named_trust_root():
    e = evidence()
    e["changed_files"][0]["filename"] = "artifacts/wow-engine/v17/independent_release_checks.py"
    assert m.evaluate(**e)["code"] == "TRUST_ROOT_REQUIRES_PROTECTED_AUTHORIZATION"


def test_reject_renamed_trust_root():
    e = evidence()
    e["changed_files"][0]["previous_filename"] = ".github/workflows/workflow.yml"
    assert m.evaluate(**e)["code"] == "TRUST_ROOT_REQUIRES_PROTECTED_AUTHORIZATION"


def test_reject_empty_diff():
    assert run(changed_files=[])["code"] == "DIFF_INVENTORY_MISMATCH"


def test_reject_truncated_diff():
    e = evidence()
    e["pr"]["changed_files"] = 2
    assert m.evaluate(**e)["code"] == "DIFF_INVENTORY_MISMATCH"


def test_reject_wrong_ci_app():
    e = evidence()
    e["checks"][0]["app"]["id"] = 123
    assert m.evaluate(**e)["code"].startswith("REQUIRED_CI_MISSING")


def test_reject_failed_ci():
    e = evidence()
    e["checks"][0]["conclusion"] = "failure"
    assert m.evaluate(**e)["code"].startswith("REQUIRED_CI_NOT_GREEN")


def test_reject_pending_ci():
    e = evidence()
    e["checks"][0]["status"] = "in_progress"
    e["checks"][0]["conclusion"] = None
    assert m.evaluate(**e)["code"].startswith("REQUIRED_CI_NOT_GREEN")


def test_reject_replayed_ci_other_sha():
    e = evidence()
    e["checks"][0]["head_sha"] = OTHER
    assert m.evaluate(**e)["code"].startswith("REQUIRED_CI_MISSING")


def test_reject_malformed_review():
    e = evidence()
    e["reviews"][0]["submitted_at"] = "unknown"
    assert m.evaluate(**e)["code"] == "REVIEW_INVENTORY_INVALID"


def test_reject_approval_from_old_commit_when_new_review_latest():
    e = evidence()
    e["reviews"].append({**e["reviews"][0], "id": 102, "commit_id": OTHER,
                         "submitted_at": "2026-10-17T22:05:00Z"})
    assert m.evaluate(**e)["code"] == "EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"


def test_reject_untrusted_repo():
    assert run(repository="attacker/WOW-Dashboard")["code"] == "REPOSITORY_IDENTITY_MISMATCH"


def test_explicit_expiry_blocks_even_valid_review():
    assert run(now=datetime(2026, 11, 1, tzinfo=timezone.utc))["code"] == "TEMPORARY_FALLBACK_EXPIRED"


def test_naive_clock_blocks():
    assert run(now=datetime(2026, 10, 17))["code"] == "CLOCK_NOT_TRUSTED"


def test_null_base_fails_closed():
    e = evidence()
    e["pr"]["base"] = None
    assert m.evaluate(**e)["code"] == "REPOSITORY_IDENTITY_MISMATCH"
