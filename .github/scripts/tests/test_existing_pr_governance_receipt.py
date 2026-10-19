from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "verify_existing_pr_governance_receipt.py"
WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "wow-v17-existing-pr-governance-review.yml"
REPO_ROOT = Path(__file__).resolve().parents[3]
RELEASE_CHECKS_SCRIPT = REPO_ROOT / "artifacts/wow-engine/v17/independent_release_checks.py"
spec = importlib.util.spec_from_file_location("verify_existing_pr_governance_receipt", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)
release_spec = importlib.util.spec_from_file_location("independent_release_checks_for_existing_pr", RELEASE_CHECKS_SCRIPT)
release_checks = importlib.util.module_from_spec(release_spec)
assert release_spec and release_spec.loader
sys.modules[release_spec.name] = release_checks
release_spec.loader.exec_module(release_checks)

NOW = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
REPOSITORY = verifier.CANONICAL_REPOSITORY
REVIEWER = {"id": 9001, "login": "independent-reviewer"}
ALLOWLIST = [REVIEWER]


def test_certifier_workflow_uses_no_claude_or_write_permissions():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "uses: ./.github/actions/wow-claude-agent" not in workflow
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
        "changed_files": [{"filename": "src/fix.py", "previous_filename": None, "status": "modified"}],
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


BOT_AUTHOR = {"id": 4242, "login": "copilot-swe-agent", "type": "Bot"}


def bootstrap_evidence(
    *,
    author=None,
    owner_state="APPROVED",
    owner_sha=HEAD,
    qa_conclusion="success",
    qa_app=5245832,
    check_sha=HEAD,
):
    data = evidence(author=author or BOT_AUTHOR)
    data["changed_files"] = [{
        "filename": ".github/workflows/wow-v17-existing-pr-governance-review.yml",
        "previous_filename": None,
        "status": "modified",
    }]
    data["reviews"] = [
        {
            "id": 12,
            "state": "APPROVED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T22:00:00Z",
            "user": {**REVIEWER, "type": "User"},
        },
        {
            "id": 77,
            "state": owner_state,
            "commit_id": owner_sha,
            "submitted_at": "2026-10-17T23:10:00Z",
            "user": {"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN, "type": "User"},
        },
    ]
    data["check_runs"] = []
    for index, (name, app_id) in enumerate(verifier.BOOTSTRAP_CHECKS.items()):
        conclusion = qa_conclusion if name == "WOW Independent QA exact-head" else "success"
        resolved_app = qa_app if name == "WOW Independent QA exact-head" else app_id
        data["check_runs"].append({
            "id": index + 300,
            "name": name,
            "head_sha": check_sha,
            "app": {"id": resolved_app},
            "status": "completed",
            "conclusion": conclusion,
            "started_at": f"2026-10-17T23:{index:02d}:00Z",
        })
    return data


def build_bootstrap(data=None, *, now=NOW):
    return verifier.build_trust_root_bootstrap(
        data or bootstrap_evidence(),
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_pr_number=1822,
        expected_incident_id="1806",
        expected_workflow_run_id="12345",
        expected_workflow_run_attempt=1,
        repo_root=REPO_ROOT,
        now=now,
    )


def verify_bootstrap(receipt, *, now=NOW, live_reviews=None, live_checks=None, data=None):
    source = data or bootstrap_evidence()
    return verifier.verify_trust_root_bootstrap(
        receipt,
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_workflow_run_id="12345",
        expected_workflow_name=verifier.WORKFLOW_NAME,
        expected_pr_number=1822,
        live_reviews=source["reviews"] if live_reviews is None else live_reviews,
        live_checks=source["check_runs"] if live_checks is None else live_checks,
        repo_root=REPO_ROOT,
        now=now,
    )


def verify(receipt, *, head=HEAD, now=NOW, live_reviews=None):
    return verifier.verify_receipt(
        receipt,
        expected_repository=REPOSITORY,
        expected_head_sha=head,
        expected_head_ref="fix/issue-1806",
        expected_workflow_run_id="12345",
        expected_workflow_name=verifier.WORKFLOW_NAME,
        expected_pr_number=1822,
        live_reviews=live_reviews if live_reviews is not None else evidence()["reviews"],
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


def test_receipt_rejects_withdrawn_or_replaced_live_approval():
    receipt = build()
    withdrawn = evidence()["reviews"] + [
        {
            "id": 13,
            "state": "DISMISSED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T23:01:00Z",
            "user": {**REVIEWER, "type": "User"},
        }
    ]

    with pytest.raises(
        verifier.ExistingPrGovernanceReceiptError,
        match="EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING|WITHDRAWN_OR_CHANGED",
    ):
        verify(receipt, live_reviews=withdrawn)


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


@pytest.mark.parametrize(("name", "app_id"), [("WOW required-three regression", 1)])
def test_wrong_check_app_identity_holds(name, app_id):
    data = evidence()
    check = next(item for item in data["check_runs"] if item["name"] == name)
    check["app"]["id"] = app_id

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CHECK_MISSING"):
        build(data)


def test_non_green_source_regression_holds():
    data = evidence()
    data["check_runs"][-1]["conclusion"] = "failure"

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CHECK_NOT_GREEN"):
        build(data)


def test_pre_governance_receipt_precedes_qa_and_release_but_final_checks_remain_mandatory():
    data = evidence()
    data["check_runs"].append(
        {
            "id": 500,
            "name": release_checks.QA_CHECK,
            "head_sha": HEAD,
            "app_id": 5245832,
            "status": "in_progress",
            "conclusion": None,
            "started_at": "2026-10-17T23:30:00Z",
        }
    )
    receipt = build(data)
    assert receipt["evidence_state"] == "REVIEW_EVIDENCE_VERIFIED"
    assert set(receipt["required_checks"]) == {
        "WOW required-three regression",
        "WOW governed probability backend",
        "WOW additional required regression",
    }
    assert "merge_authorized" not in receipt
    assert "qa" not in receipt and "release" not in receipt

    later = release_evidence()
    missing_qa = release_checks.release_findings(
        later, qa_app_id=5245832, release_app_id=5245853
    )
    assert "RELEASE_QA_MISSING" in missing_qa
    assert release_checks.decision("release", missing_qa, later)["conclusion"] == "failure"

    later["check_runs"].append(final_app_check(release_checks.QA_CHECK, 5245832, "success", 601))
    assert release_checks.release_findings(
        later, qa_app_id=5245832, release_app_id=5245853
    ) == []
    assert release_checks.decision("release", [], later)["conclusion"] == "success"

    later["check_runs"][0]["conclusion"] = "failure"
    later["check_runs"].append(final_app_check(release_checks.QA_CHECK, 5245832, "failure", 602))
    failed_qa = release_checks.qa_findings(later)
    failed_release = release_checks.release_findings(
        later, qa_app_id=5245832, release_app_id=5245853
    )
    assert release_checks.decision("qa", failed_qa, later)["conclusion"] == "failure"
    assert "RELEASE_QA_FAILED" in failed_release
    assert release_checks.decision("release", failed_release, later)["conclusion"] == "failure"
    assert release_checks.decision("release", failed_release, later)["merge_or_release_authorized"] is False

    failed_release = release_checks.release_findings(
        release_evidence(), qa_app_id=5245832, release_app_id=5245832
    )
    assert "RELEASE_IDENTITY_NOT_DISTINCT" in failed_release
    assert release_checks.decision("release", failed_release, release_evidence())["conclusion"] == "failure"


def final_app_check(name, app_id, conclusion, check_id):
    return {
        "id": check_id,
        "name": name,
        "head_sha": HEAD,
        "app_id": app_id,
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": "2026-10-17T23:45:00Z",
    }


def release_evidence():
    checks = [
        {
            "id": index + 1,
            "name": name,
            "head_sha": HEAD,
            "app_id": 15368,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-10-17T23:40:00Z",
        }
        for index, name in enumerate(
            (*release_checks.QA_EVIDENCE_CHECKS, release_checks.TRUSTED_GOVERNANCE_CHECK)
        )
    ]
    return {
        "repository": verifier.CANONICAL_REPOSITORY,
        "expected_head_sha": HEAD,
        "owner_login": verifier.OWNER_LOGIN,
        "evidence_errors": [],
        "pr": {
            "state": "open",
            "draft": False,
            "merged": False,
            "head_sha": HEAD,
            "head_repo": verifier.CANONICAL_REPOSITORY,
            "base_ref": "main",
            "author": "wow-engineering[bot]",
            "changed_files": 1,
        },
        "files": ["src/fix.py"],
        "check_runs": checks,
        "reviews": [],
    }


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
    data["changed_files"] = [{"filename": path, "previous_filename": None, "status": "modified"}]

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_CHANGE"):
        build(data)


def test_renaming_a_trust_root_is_rejected():
    data = evidence()
    data["changed_files"] = [{
        "filename": "src/renamed.py",
        "previous_filename": ".github/scripts/verify.py",
        "status": "renamed",
    }]

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_CHANGE"):
        build(data)


@pytest.mark.parametrize(
    ("file_record", "reason"),
    [
        ({"previous_filename": None, "status": "modified"}, "DIFF_FILE_SCHEMA_INVALID"),
        ({"filename": "src/fix.py", "previous_filename": None}, "DIFF_FILE_SCHEMA_INVALID"),
        (
            {"filename": "src/fix.py", "previous_filename": None, "status": "unknown"},
            "DIFF_STATUS_INVALID",
        ),
        (
            {"filename": ".", "previous_filename": None, "status": "modified"},
            "DIFF_PATH_INVALID",
        ),
        (
            {"filename": "src/fix.py", "previous_filename": None, "status": "renamed"},
            "DIFF_PREVIOUS_FILENAME_MISSING",
        ),
    ],
)
def test_changed_file_inventory_requires_typed_provenance(file_record, reason):
    data = evidence()
    data["changed_files"] = [file_record]

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match=reason):
        build(data)


def test_every_dynamically_declared_governance_trust_root_is_protected():
    protected = verifier._protected_paths(REPO_ROOT)
    policy_path = REPO_ROOT / "artifacts/wow-engine/v17/agent_identity_policy.py"
    policy_spec = importlib.util.spec_from_file_location("agent_identity_policy_test", policy_path)
    policy = importlib.util.module_from_spec(policy_spec)
    assert policy_spec and policy_spec.loader
    policy_spec.loader.exec_module(policy)

    assert set(policy.governance_trust_roots(REPO_ROOT)) <= protected
    assert set(policy.trust_root_paths(REPO_ROOT)) <= protected

    for path in protected:
        data = evidence()
        data["changed_files"] = [{"filename": path, "previous_filename": None, "status": "modified"}]
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


def test_receipt_ttl_is_30_minutes_within_provider_sunset():
    current = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
    receipt = build(now=current)
    assert receipt["fallback_expires_utc"] == "2026-10-18T00:30:00Z"
    verify(receipt, now=current)
    verify(receipt, now=current + timedelta(minutes=29, seconds=59))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_EXPIRED_OR_FUTURE_RECEIPT"):
        verify(receipt, now=current + timedelta(minutes=30))


def test_temporary_provider_sunset_blocks_new_receipts_and_replay():
    before_sunset = datetime(2026, 10, 31, 23, 40, tzinfo=timezone.utc)
    receipt = build(now=before_sunset)
    assert receipt["fallback_expires_utc"] == "2026-11-01T00:10:00Z"
    verify(receipt, now=before_sunset + timedelta(minutes=19, seconds=59))
    at_sunset = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
        verify(receipt, now=at_sunset)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
        build(now=at_sunset)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
        build(now=datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc))


def test_receipt_expiry_cannot_be_extended_or_backdated():
    receipt = build()
    receipt["fallback_expires_utc"] = "2036-11-02T00:00:00Z"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="FALLBACK_EXPIRY_MISMATCH"):
        verify(receipt)

    receipt = build()
    receipt["created_at_utc"] = "2026-10-17T00:00:00Z"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="FALLBACK_EXPIRY_MISMATCH"):
        verify(receipt)


def test_independent_approval_still_required_with_fresh_receipt():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"):
        build(data=evidence(review_state="COMMENTED"))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"):
        build(data=evidence(review_state="DISMISSED"))


def test_consumer_context_requires_independently_bound_pr_and_live_reviews(tmp_path, monkeypatch):
    # CLI uses live wall clock; never feed it the suite’s frozen historical NOW.
    receipt = build(now=datetime.now(timezone.utc))
    live_reviews = tmp_path / "reviews.json"
    live_reviews.write_text(json.dumps(evidence()["reviews"]), encoding="utf-8")
    (tmp_path / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "verify",
        "--receipt", str(tmp_path / "receipt.json"),
        "--repository", REPOSITORY,
        "--head-sha", HEAD,
        "--head-ref", "fix/issue-1806",
        "--workflow-run-id", "12345",
        "--workflow-name", verifier.WORKFLOW_NAME,
        "--pr-number", "1822",
        "--live-reviews-json", str(live_reviews),
    ])

    assert verifier.main() == 0


def test_consumer_context_does_not_infer_pr_number_from_event(tmp_path, monkeypatch):
    (tmp_path / "receipt.json").write_text(json.dumps(build()), encoding="utf-8")
    (tmp_path / "reviews.json").write_text(json.dumps(evidence()["reviews"]), encoding="utf-8")
    (tmp_path / "event.json").write_text(json.dumps({"pull_request": {"number": 1822}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "event.json"))
    monkeypatch.setattr(sys, "argv", [
        "verify",
        "--receipt", str(tmp_path / "receipt.json"),
        "--repository", REPOSITORY,
        "--head-sha", HEAD,
        "--head-ref", "fix/issue-1806",
        "--workflow-run-id", "12345",
        "--workflow-name", verifier.WORKFLOW_NAME,
        "--live-reviews-json", str(tmp_path / "reviews.json"),
    ])

    assert verifier.main() == 1


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
            live_reviews=evidence()["reviews"],
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


def test_trusted_governance_workflow_executes_legacy_and_v2_verifier_argv_shapes(tmp_path):
    workflow = (REPO_ROOT / ".github/workflows/wow-v17-trusted-governance-gate.yml").read_text()
    start = workflow.index('verifier=".github/scripts/verify_engineering_governance_receipt.py"')
    end_marker = 'echo "Trusted exact-head governance receipt verified. can_execute=false."'
    end = workflow.index(end_marker, start) + len(end_marker)
    invocation = workflow[start:end]

    def run(workflow_name, receipt, live_reviews=None, live_checks=None):
        receipt_path = tmp_path / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        reviews_path = tmp_path / "reviews.json"
        reviews_path.write_text(json.dumps(live_reviews or []), encoding="utf-8")
        checks_path = tmp_path / "checks.json"
        checks_path.write_text(json.dumps(live_checks or []), encoding="utf-8")
        script = (
            'set -euo pipefail\n'
            'receipt="$RECEIPT_PATH"\n'
            'live_reviews="$LIVE_REVIEWS_PATH"\n'
            'live_checks="$LIVE_CHECKS_PATH"\n'
            f"{invocation}\n"
        )
        env = os.environ | {
            "WORKFLOW_NAME": workflow_name,
            "RECEIPT_PATH": str(receipt_path),
            "LIVE_REVIEWS_PATH": str(reviews_path),
            "LIVE_CHECKS_PATH": str(checks_path),
            "PR_NUMBER": "1822",
            "PR_HEAD_SHA": HEAD,
            "PR_HEAD_REF": "fix/issue-1806",
            "RUN_ID": "12345",
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    legacy = {
        "receipt_schema_version": 1,
        "workflow_name": "wow-v17-chatgpt-engineering-worker",
        "repository": REPOSITORY,
        "workflow_run_id": "12345",
        "implementation": {
            "changed": True,
            "branch": "fix/issue-1806",
            "head_sha": HEAD,
            "incident_id": "1806",
        },
        "handoff": {
            "lead_dispatch": {"decision": "REPAIR"},
            "triage": {"repairable": True, "incident_id": "1806"},
            "engineering": {"changed": True, "incident_id": "1806"},
            "independent_review": {"decision": "PASS"},
            "system_architect": {"decision": "NOT_APPLICABLE"},
            "deterministic_regression": {"status": "0"},
            "qa": {"decision": "PASS"},
        },
        "governance_complete": True,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
    legacy_valid = run("wow-v17-chatgpt-engineering-worker", legacy)
    assert legacy_valid.returncode == 0, legacy_valid.stderr
    legacy["implementation"]["head_sha"] = "b" * 40
    legacy_invalid = run("wow-v17-chatgpt-engineering-worker", legacy)
    assert legacy_invalid.returncode == 1
    assert "receipt head SHA mismatch" in legacy_invalid.stdout

    # External Python verifier uses the current wall clock, not the test fixture clock.
    receipt = build(now=datetime.now(timezone.utc))
    reviews = evidence()["reviews"]
    v2_valid = run(verifier.WORKFLOW_NAME, receipt, reviews)
    assert v2_valid.returncode == 0, v2_valid.stderr
    withdrawn = reviews + [
        {
            "id": 13,
            "state": "DISMISSED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T23:01:00Z",
            "user": {**REVIEWER, "type": "User"},
        }
    ]
    v2_withdrawn = run(verifier.WORKFLOW_NAME, receipt, withdrawn)
    assert v2_withdrawn.returncode == 1
    assert "EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING" in v2_withdrawn.stdout

    renamed_source = dict(receipt)
    renamed_source["changed_files"] = [
        {
            "filename": "src/relocated.py",
            "previous_filename": ".github/scripts/verify_existing_pr_governance_receipt.py",
            "status": "renamed",
        }
    ]
    v2_renamed = run(verifier.WORKFLOW_NAME, renamed_source, reviews)
    assert v2_renamed.returncode == 1
    assert "TRUST_ROOT_CHANGE_NOT_CERTIFIABLE" in v2_renamed.stdout

    boot_data = bootstrap_evidence()
    boot = build_bootstrap(boot_data, now=datetime.now(timezone.utc))
    boot_ok = run(verifier.WORKFLOW_NAME, boot, boot_data["reviews"], boot_data["check_runs"])
    assert boot_ok.returncode == 0, boot_ok.stdout + boot_ok.stderr
    assert "TRUST_ROOT_BOOTSTRAP_VERIFIED" in boot_ok.stdout
    assert "REVIEW_EVIDENCE_VERIFIED" not in boot_ok.stdout
    withdrawn_owner = json.loads(json.dumps(boot_data["reviews"]))
    withdrawn_owner[-1]["state"] = "CHANGES_REQUESTED"
    boot_withdrawn = run(verifier.WORKFLOW_NAME, boot, withdrawn_owner, boot_data["check_runs"])
    assert boot_withdrawn.returncode == 1
    assert "TRUST_ROOT_OWNER_APPROVAL_MISSING" in boot_withdrawn.stdout


def test_trust_root_bootstrap_receipt_uses_owner_approval_not_an_allowlist():
    receipt = build_bootstrap()
    verify_bootstrap(receipt)
    assert receipt["certification_mode"] == "TRUST_ROOT_BOOTSTRAP"
    assert receipt["evidence_state"] == "TRUST_ROOT_BOOTSTRAP_VERIFIED"
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert "independent_review" not in receipt
    assert "trusted_reviewers" not in receipt
    assert set(receipt["required_checks"]) == set(verifier.BOOTSTRAP_CHECKS)
    assert "WOW Independent QA exact-head" in receipt["required_checks"]
    assert "WOW Release Authority exact-head" in receipt["required_checks"]
    for name in verifier.REQUIRED_CHECKS:
        assert name in receipt["required_checks"]


def test_trust_root_bootstrap_holds_for_missing_or_stale_owner_approval():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_APPROVAL_MISSING"):
        build_bootstrap(bootstrap_evidence(owner_state="CHANGES_REQUESTED"))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_APPROVAL_MISSING"):
        build_bootstrap(bootstrap_evidence(owner_sha="b" * 40))
    data = bootstrap_evidence()
    data["reviews"] = [review for review in data["reviews"] if review["user"]["id"] != verifier.OWNER_ID]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_APPROVAL_MISSING"):
        build_bootstrap(data)


def test_owner_authored_trust_root_change_cannot_bootstrap():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_AUTHORED"):
        build_bootstrap(bootstrap_evidence(author={
            "id": verifier.OWNER_ID,
            "login": verifier.OWNER_LOGIN,
            "type": "User",
        }))


def test_ordinary_diff_cannot_use_the_bootstrap_receipt():
    data = bootstrap_evidence()
    data["changed_files"] = [{"filename": "src/fix.py", "previous_filename": None, "status": "modified"}]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_REQUIRED"):
        build_bootstrap(data)


def test_bootstrap_requires_green_qa_release_and_exact_check_provenance():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_NOT_GREEN"):
        build_bootstrap(bootstrap_evidence(qa_conclusion="failure"))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_MISSING"):
        build_bootstrap(bootstrap_evidence(qa_app=1))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_MISSING"):
        build_bootstrap(bootstrap_evidence(check_sha="b" * 40))


def test_bootstrap_receipt_expires_in_30_minutes_and_at_the_provider_sunset():
    current = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
    receipt = build_bootstrap(now=current)
    assert receipt["fallback_expires_utc"] == "2026-10-18T00:30:00Z"
    verify_bootstrap(receipt, now=current)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_EXPIRED_OR_FUTURE_RECEIPT"):
        verify_bootstrap(receipt, now=current + timedelta(minutes=30))
    at_sunset = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
        build_bootstrap(now=at_sunset)
    before_sunset = datetime(2026, 10, 31, 23, 40, tzinfo=timezone.utc)
    sunset_receipt = build_bootstrap(now=before_sunset)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
        verify_bootstrap(sunset_receipt, now=at_sunset)


def test_live_recheck_rejects_replaced_owner_review_and_superseded_checks():
    data = bootstrap_evidence()
    receipt = build_bootstrap(data)
    replaced = json.loads(json.dumps(data["reviews"]))
    replaced.append({
        "id": 88,
        "state": "APPROVED",
        "commit_id": HEAD,
        "submitted_at": "2026-10-17T23:20:00Z",
        "user": {"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN, "type": "User"},
    })
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_APPROVAL_WITHDRAWN_OR_CHANGED"):
        verify_bootstrap(receipt, data=data, live_reviews=replaced)
    superseded = json.loads(json.dumps(data["check_runs"]))
    superseded.append({
        "id": 999,
        "name": "WOW Release Authority exact-head",
        "head_sha": HEAD,
        "app": {"id": 5245853},
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-10-17T23:40:00Z",
    })
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_SUPERSEDED"):
        verify_bootstrap(receipt, data=data, live_checks=superseded)


def test_protected_worker_paths_classify_as_trust_root_bootstrap():
    for path in (
        ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
        ".github/workflows/wow-v17-claude-engineering-worker.yml",
        ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
        "render.yaml",
    ):
        data = bootstrap_evidence()
        data["changed_files"] = [{"filename": path, "previous_filename": None, "status": "modified"}]
        assert verifier.classify_changed_files(
            data["changed_files"], repo_root=REPO_ROOT, expected_count=1,
        ) == verifier.BOOTSTRAP_MODE
    ordinary = evidence()
    assert verifier.classify_changed_files(
        ordinary["changed_files"], repo_root=REPO_ROOT, expected_count=1,
    ) == verifier.ORDINARY_MODE


def test_cli_splits_bootstrap_and_ordinary_receipts(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    data = bootstrap_evidence()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "receipt.json"
    common = [
        "--evidence", str(evidence_path),
        "--repository", REPOSITORY,
        "--head-sha", HEAD,
        "--head-ref", "fix/issue-1806",
        "--workflow-run-id", "12345",
        "--workflow-run-attempt", "1",
        "--workflow-name", verifier.WORKFLOW_NAME,
        "--pr-number", "1822",
        "--incident-id", "1806",
    ]
    monkeypatch.setattr(sys, "argv", ["verify", *common, "--classify"])
    assert verifier.main() == 0
    monkeypatch.setattr(sys, "argv", ["verify", *common, "--output", str(output), "--bootstrap"])
    assert verifier.main() == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["certification_mode"] == "TRUST_ROOT_BOOTSTRAP"
    assert receipt["can_execute"] is False
    monkeypatch.setattr(sys, "argv", [
        "verify", *common, "--output", str(output), "--bootstrap",
        "--trusted-reviewers-json", json.dumps(ALLOWLIST),
    ])
    assert verifier.main() == 1
    ordinary = evidence()
    evidence_path.write_text(json.dumps(ordinary), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["verify", *common, "--classify"])
    assert verifier.main() == 0
    monkeypatch.setattr(sys, "argv", [
        "verify", *common, "--output", str(output),
        "--trusted-reviewers-json", json.dumps(ALLOWLIST),
    ])
    # CLI verifies against the wall clock; the fixture clock is historical.
    original_build = verifier.build_receipt

    def build_now(*args, **kwargs):
        kwargs["now"] = datetime.now(timezone.utc)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(verifier, "build_receipt", build_now)
    assert verifier.main() == 0
