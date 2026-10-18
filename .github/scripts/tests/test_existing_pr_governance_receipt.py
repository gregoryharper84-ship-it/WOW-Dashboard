from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "verify_existing_pr_governance_receipt.py"
WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "wow-v17-existing-pr-governance-review.yml"
spec = importlib.util.spec_from_file_location("verify_existing_pr_governance_receipt", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)

NOW = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
REPOSITORY = verifier.CANONICAL_REPOSITORY
REVIEWER = {"id": 9001, "login": "independent-reviewer"}
ALLOWLIST = [REVIEWER]


def test_certifier_workflow_uses_no_claude_or_write_permissions():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "wow-claude-agent" not in workflow
    assert "ANTHROPIC_API_KEY" not in workflow
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in workflow
    assert "TRUSTED_REVIEWERS_JSON: ${{ vars.WOW_V17_INDEPENDENT_REVIEWERS }}" in workflow
    assert "checks: read" in workflow
    assert "actions: write" not in workflow
    assert "contents: write" not in workflow
    assert "pull-requests: write" not in workflow


def evidence(*, author=None, review_state="APPROVED", review_sha=HEAD):
    author = author or {"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN, "type": "User"}
    return {
        "complete_inventory": True,
        "pr": {
            "number": 1822,
            "state": "open",
            "draft": False,
            "merged_at": None,
            "changed_files": 1,
            "user": author,
            "head": {
                "sha": HEAD,
                "ref": "fix/issue-1806",
                "repo": {"full_name": REPOSITORY},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": REPOSITORY},
            },
        },
        "changed_files": [{"filename": "src/fix.py", "previous_filename": None}],
        "reviews": [
            {
                "id": 12,
                "state": review_state,
                "commit_id": review_sha,
                "submitted_at": "2026-10-17T23:00:00Z",
                "user": {**REVIEWER, "type": "User"},
            }
        ],
        "workflow_runs": [
            {
                "id": index + 100,
                "name": name,
                "path": path,
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-17T23:00:00Z",
            }
            for index, (name, path) in enumerate(verifier.REQUIRED_WORKFLOWS.items())
        ],
        "check_runs": [
            {
                "id": index + 200,
                "name": name,
                "head_sha": HEAD,
                "app": {"id": app_id},
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-10-17T23:00:00Z",
            }
            for index, (name, app_id) in enumerate(verifier.REQUIRED_CHECKS.items())
        ],
    }


def build(data=None, *, trusted_reviewers=ALLOWLIST, now=NOW):
    return verifier.build_receipt(
        data or evidence(),
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_pr_number=1822,
        expected_incident_id="1806",
        expected_workflow_run_id="12345",
        expected_workflow_run_attempt=1,
        trusted_reviewers=trusted_reviewers,
        now=now,
    )


def verify(receipt, *, head=HEAD, now=NOW):
    return verifier.verify_receipt(
        receipt,
        expected_repository=REPOSITORY,
        expected_head_sha=head,
        expected_head_ref="fix/issue-1806",
        expected_workflow_run_id="12345",
        expected_workflow_name=verifier.WORKFLOW_NAME,
        expected_pr_number=1822,
        now=now,
    )


def test_builds_versioned_exact_head_review_evidence_without_legacy_pass_fields():
    receipt = build()

    assert receipt["receipt_schema_version"] == 2
    assert receipt["evidence_state"] == "REVIEW_EVIDENCE_VERIFIED"
    assert "handoff" not in receipt
    assert "governance_complete" not in receipt
    assert receipt["can_execute"] is False
    verify(receipt)


def test_bot_author_is_not_a_required_identity_for_ordinary_fixes():
    data = evidence(author={"id": 77, "login": "wow-engineering[bot]", "type": "Bot"})

    assert build(data)["independent_review"]["reviewer"] == {**REVIEWER, "type": "User"}


def test_empty_allowlist_holds():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="ALLOWLIST_MISSING"):
        build(trusted_reviewers=[])


@pytest.mark.parametrize(
    "reviewer",
    [
        {"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN},
        {"id": 88, "login": "unknown[bot]"},
    ],
)
def test_owner_or_bot_cannot_be_added_as_independent_reviewer(reviewer):
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="NOT_INDEPENDENT_HUMAN"):
        build(trusted_reviewers=[reviewer])


def test_duplicate_allowlist_entries_hold():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="DUPLICATE_REVIEWER"):
        build(trusted_reviewers=[REVIEWER, REVIEWER])


def test_unapproved_or_stale_head_review_holds():
    data = evidence(review_state="COMMENTED")
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="APPROVAL_MISSING"):
        build(data)

    data = evidence(review_sha="b" * 40)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="APPROVAL_MISSING"):
        build(data)


def test_author_cannot_approve_their_own_pr():
    data = evidence(author={"id": REVIEWER["id"], "login": REVIEWER["login"], "type": "User"})

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="APPROVAL_MISSING"):
        build(data)


def test_latest_trusted_reviewer_state_must_remain_approved():
    data = evidence()
    data["reviews"].append(
        {
            "id": 13,
            "state": "CHANGES_REQUESTED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T23:01:00Z",
            "user": {**REVIEWER, "type": "User"},
        }
    )

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="APPROVAL_MISSING"):
        build(data)


@pytest.mark.parametrize("field", ["complete_inventory", "reviews", "check_runs", "workflow_runs"])
def test_incomplete_or_missing_inventory_holds(field):
    data = evidence()
    if field == "complete_inventory":
        data[field] = False
    else:
        data[field] = None
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError):
        build(data)


def test_unknown_review_identity_holds():
    data = evidence()
    data["reviews"][0]["user"] = {"login": "independent-reviewer", "type": "User"}

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="IDENTITY_UNKNOWN"):
        build(data)


def test_unallowlisted_human_review_identity_holds():
    data = evidence()
    data["reviews"].append(
        {
            "id": 13,
            "state": "COMMENTED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T23:01:00Z",
            "user": {"id": 444, "login": "unknown-human", "type": "User"},
        }
    )

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="NOT_ALLOWLISTED"):
        build(data)


@pytest.mark.parametrize(
    ("name", "app_id"),
    [
        ("WOW required-three regression", 1),
        ("WOW Independent QA exact-head", 1),
        ("WOW Release Authority exact-head", 1),
    ],
)
def test_wrong_check_app_identity_holds(name, app_id):
    data = evidence()
    check = next(item for item in data["check_runs"] if item["name"] == name)
    check["app"]["id"] = app_id

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CHECK_MISSING"):
        build(data)


def test_non_green_qa_release_and_regression_checks_hold():
    data = evidence()
    data["check_runs"][-1]["conclusion"] = "failure"

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CHECK_NOT_GREEN"):
        build(data)


def test_missing_required_workflow_and_failed_latest_run_hold():
    data = evidence()
    data["workflow_runs"] = data["workflow_runs"][:-1]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="WORKFLOW_MISSING"):
        build(data)

    data = evidence()
    data["workflow_runs"][0]["conclusion"] = "failure"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="WORKFLOW_NOT_GREEN"):
        build(data)


@pytest.mark.parametrize("path", [".github/workflows/unsafe.yml", ".agents/skills/unsafe.md", "render.yaml"])
def test_trust_root_changes_cannot_be_certified(path):
    data = evidence()
    data["changed_files"] = [{"filename": path, "previous_filename": None}]

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_CHANGE"):
        build(data)


def test_renaming_a_trust_root_is_rejected():
    data = evidence()
    data["changed_files"] = [{
        "filename": "src/renamed.py",
        "previous_filename": ".github/scripts/verify.py",
    }]

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_CHANGE"):
        build(data)


def test_draft_fork_or_stale_pr_head_holds():
    data = evidence()
    data["pr"]["draft"] = True
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="STATE_OR_BASE_MISMATCH"):
        build(data)

    data = evidence()
    data["pr"]["head"]["repo"]["full_name"] = "fork/WOW-Dashboard"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="STATE_OR_BASE_MISMATCH"):
        build(data)

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TARGET_MISMATCH"):
        verify(build(), head="b" * 40)


def test_schema_mutation_and_legacy_schema_fail_closed():
    receipt = build()
    receipt["governance_complete"] = True
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="SCHEMA_INVALID"):
        verify(receipt)

    receipt = build()
    receipt["receipt_schema_version"] = 1
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="VERSION_UNSUPPORTED"):
        verify(receipt)


def test_receipt_cannot_relabel_a_bot_as_an_independent_human():
    receipt = build()
    receipt["independent_review"]["reviewer"]["type"] = "Bot"

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REVIEW_INVALID"):
        verify(receipt)


def test_expired_temporary_fallback_fails_closed():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="FALLBACK_EXPIRED"):
        build(now=datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="FALLBACK_EXPIRED"):
        verify(build(), now=datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))


@pytest.mark.parametrize(
    "event",
    [
        {"pull_request": {"number": 1822}},
        {"workflow_run": {"pull_requests": [{"number": 1822, "head": {"sha": HEAD}}]}},
        {"inputs": {"pr_number": "1822"}},
    ],
)
def test_consumer_context_binds_receipt_to_exact_pr(tmp_path, monkeypatch, event):
    receipt = build()
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setattr(sys, "argv", [
        "verify",
        "--receipt", str(tmp_path / "receipt.json"),
        "--repository", REPOSITORY,
        "--head-sha", HEAD,
        "--head-ref", "fix/issue-1806",
        "--workflow-run-id", "12345",
        "--workflow-name", verifier.WORKFLOW_NAME,
    ])
    (tmp_path / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    assert verifier.main() == 0


def test_consumer_context_rejects_other_pr(tmp_path):
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="PR_NUMBER_MISMATCH"):
        verifier.verify_receipt(
            build(),
            expected_repository=REPOSITORY,
            expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806",
            expected_workflow_run_id="12345",
            expected_workflow_name=verifier.WORKFLOW_NAME,
            expected_pr_number=1823,
            now=NOW,
        )


def test_terminal_and_execution_invariants_are_enforced():
    receipt = build()
    receipt["can_execute"] = True
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CAN_EXECUTE"):
        verify(receipt)

    receipt = build()
    receipt["terminal_authority"] = "OTHER"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TERMINAL_AUTHORITY"):
        verify(receipt)
