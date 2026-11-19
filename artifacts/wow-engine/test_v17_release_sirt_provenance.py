"""Release role checks exact-head signed QA+SIRT provenance without re-running QA."""
from copy import deepcopy

import v17.independent_release_checks as checks

HEAD = "a" * 40


def evidence():
    reviewer = {
        "id": 88, "user_id": 17, "login": "independent-reviewer",
        "commit_id": HEAD, "submitted_at": "2026-11-19T13:00:00Z",
    }
    return {
        "repository": "gregoryharper84-ship-it/WOW-Dashboard",
        "pr_number": 2732, "expected_head_sha": HEAD, "owner_login": "repo-owner",
        "evidence_errors": [],
        "files": ["docs/superpowers/plans/release-evidence.md"],
        "pr": {
            "head_sha": HEAD, "head_ref": "feature", "head_repo": "gregoryharper84-ship-it/WOW-Dashboard",
            "state": "open", "merged": False, "draft": False, "base_ref": "main",
            "author": "code-author", "author_id": 16, "body": "Incident: #2020",
        },
        "qa_review_policy": {
            "mode": "sirt",
            "target": {"pr_number": 2732, "head_sha": HEAD, "head_ref": "feature"},
        },
        "sirt_enabled": True,
        "current_review_active": False,
        "zero_credit_enabled": False,
        "independent_review_active": False,
        "source_review": None,
        "sirt_review_evidence": {
            "schema_version": 1, "review_status": "EVIDENCE_READY",
            "code": "SIRT_SOURCE_REVIEW_EVIDENCE_READY",
            "head_sha": HEAD, "required_check_names": list(checks.REQUIRED_REGRESSION_CHECKS),
            "findings": [], "reviewer": reviewer,
            "independent_scientific_acceptance_required": True,
            "protected_release_authority_required": True,
            "can_execute": False, "merge_or_release_authorized": False,
            "model_certified": False, "probability_publishable": False,
        },
        "reviews": [{
            "user": reviewer["login"], "state": "APPROVED",
            "commit_id": HEAD, "submitted_at": reviewer["submitted_at"],
        }],
        "check_runs": [{
            "name": checks.QA_CHECK, "head_sha": HEAD, "app_id": 111,
            "id": 100, "completed_at": "2026-11-19T13:01:00Z",
            "status": "completed", "conclusion": "success",
        }],
    }


def decide(ev):
    return checks.release_findings(ev, qa_app_id="111", release_app_id="222")


def test_sirt_release_uses_provenance_not_duplicate_qa(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("QA must not be re-run in SIRT release role")
    monkeypatch.setattr(checks, "qa_findings", forbidden)
    assert decide(evidence()) == []


def test_other_modes_preserve_qa_rederivation(monkeypatch):
    ev = evidence()
    ev["qa_review_policy"]["mode"] = "independent"
    monkeypatch.setattr(checks, "qa_findings", lambda *args, **kwargs: ["QA_REVIEW_POLICY_MODE_CONFLICT"])
    assert "RELEASE_QA_REVIEW_POLICY_MODE_CONFLICT" in decide(ev)


def test_stale_sha_fails_closed():
    ev = evidence()
    ev["expected_head_sha"] = "b" * 40
    assert "RELEASE_QA_HEAD_STALE" in decide(ev)


def test_missing_or_foreign_qa_fails_closed():
    ev = evidence()
    ev["check_runs"] = []
    assert "RELEASE_QA_MISSING" in decide(ev)
    ev = evidence()
    ev["check_runs"][0]["app_id"] = 999
    assert "RELEASE_QA_FOREIGN_SOURCE" in decide(ev)


def test_failed_qa_fails_closed():
    ev = evidence()
    ev["check_runs"][0]["conclusion"] = "failure"
    assert "RELEASE_QA_FAILED" in decide(ev)


def test_self_review_and_missing_review_fail_closed():
    ev = evidence()
    ev["sirt_review_evidence"]["reviewer"]["user_id"] = 16
    assert "RELEASE_QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" in decide(ev)
    ev = evidence()
    ev["reviews"] = []
    assert "RELEASE_QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" in decide(ev)


def test_hold_reject_and_changed_policy_fail_closed():
    for field, value in (
        ("review_status", "HOLD"), ("code", "REJECT"), ("head_sha", "b" * 40),
        ("findings", ["SIRT_INDEPENDENT_EXACT_HEAD_APPROVAL_MISSING"]),
    ):
        ev = evidence()
        ev["sirt_review_evidence"][field] = value
        assert "RELEASE_QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" in decide(ev)
    ev = evidence()
    ev["qa_review_policy"]["target"]["head_sha"] = "b" * 40
    assert "RELEASE_QA_SIRT_REVIEW_POLICY_TARGET_MISMATCH" in decide(ev)


def test_draft_foreign_repo_and_invalid_incident_fail_closed():
    ev = evidence()
    ev["pr"]["draft"] = True
    assert "RELEASE_QA_PR_DRAFT" in decide(ev)
    ev = evidence()
    ev["pr"]["head_repo"] = "foreign/repo"
    assert "RELEASE_QA_PR_HEAD_REPOSITORY_MISMATCH" in decide(ev)
    ev = evidence()
    ev["pr"]["body"] = ""
    assert "RELEASE_QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS" in decide(ev)


def test_missing_sirt_and_malformed_policy_fail_closed():
    ev = evidence()
    ev["sirt_review_evidence"] = None
    assert "RELEASE_QA_SIRT_INDEPENDENT_EVIDENCE_MISSING" in decide(ev)
    ev = evidence()
    ev["qa_review_policy"] = "forged"
    assert "RELEASE_QA_REVIEW_POLICY_MISSING" in decide(ev) or "RELEASE_QA_REVIEW_POLICY_INVALID" in decide(ev)


def test_bound_policy_and_qa_roles_required():
    ev = evidence()
    ev["sirt_enabled"] = False
    assert "RELEASE_QA_SIRT_REVIEW_POLICY_CONFLICT" in decide(ev)
    assert "RELEASE_IDENTITY_NOT_DISTINCT" in checks.release_findings(
        evidence(), qa_app_id="111", release_app_id="111"
    )


def test_protected_nonowner_pr_requires_current_owner_approval():
    ev = evidence()
    trust_root = ".github/workflows/wow-v17-release-authority-check.yml"
    ev["files"] = [trust_root]
    governed = frozenset({trust_root})
    missing = checks.release_findings(ev, qa_app_id="111", release_app_id="222",
                                       governance_files=governed)
    assert "RELEASE_QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in missing
    ev["reviews"].append({
        "user": "repo-owner", "state": "APPROVED",
        "commit_id": HEAD, "submitted_at": "2026-11-19T13:02:00Z",
    })
    passed = checks.release_findings(ev, qa_app_id="111", release_app_id="222",
                                      governance_files=governed)
    assert "RELEASE_QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" not in passed
    ev["reviews"].append({
        "user": "repo-owner", "state": "DISMISSED",
        "commit_id": HEAD, "submitted_at": "2026-11-19T13:03:00Z",
    })
    revoked = checks.release_findings(ev, qa_app_id="111", release_app_id="222",
                                       governance_files=governed)
    assert "RELEASE_QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in revoked


def test_missing_changed_file_inventory_is_not_release_ready():
    ev = evidence()
    ev["files"] = []
    assert "RELEASE_QA_FILES_EMPTY" in decide(ev)


def test_invalid_policy_cannot_crash_or_pass():
    ev = evidence()
    ev["qa_review_policy"] = ["malformed"]
    assert "RELEASE_QA_REVIEW_POLICY_MISSING" in decide(ev)
