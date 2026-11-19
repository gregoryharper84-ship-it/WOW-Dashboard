"""No-cost independent SIRT source gate: negative/fail-closed QA contract."""
from v17 import independent_release_checks as qa

SHA = "a" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"


def sample():
    return {
        "repository": REPO, "pr_number": 18, "expected_head_sha": SHA,
        "evidence_errors": [],
        "owner_login": "owner",
        "pr": {
            "number": 18, "head_sha": SHA, "head_ref": "feature",
            "head_repo": REPO, "base_ref": "main", "state": "open",
            "draft": False, "merged": False, "author": "author",
            "author_id": 101, "body": "Incident: #2020",
        },
        "files": ["docs/superpowers/specs/proof.md"],
        "source_review": None,
        "independent_review_active": False,
        "current_review_active": False,
        "sirt_review_evidence": None,
        "qa_review_policy": {
            "mode": "sirt",
            "target": {"pr_number": 18, "head_sha": SHA, "head_ref": "feature"},
            "canary_scope": None,
        },
        "check_runs": [],
        "reviews": [{"user": "independent-sirt", "state": "APPROVED",
                     "commit_id": SHA, "submitted_at": "2026-11-18T18:00:00Z"}],
    }


def ready_receipt():
    return {
        "schema_version": 1, "review_status": "EVIDENCE_READY",
        "code": "SIRT_SOURCE_REVIEW_EVIDENCE_READY", "head_sha": SHA,
        "reviewer": {"id": 71, "login": "independent-sirt", "user_id": 202,
                     "commit_id": SHA, "submitted_at": "2026-11-18T18:00:00Z"},
        "required_check_names": list(qa.REQUIRED_REGRESSION_CHECKS),
        "findings": [],
        "independent_scientific_acceptance_required": True,
        "protected_release_authority_required": True,
        "merge_or_release_authorized": False, "model_certified": False,
        "probability_publishable": False, "can_execute": False,
    }


def test_sirt_without_authenticated_receipt_holds():
    findings = qa.qa_findings(sample())
    assert "QA_SIRT_INDEPENDENT_EVIDENCE_MISSING" in findings


def test_sirt_does_not_require_gemini_artifact_after_genuine_review_evidence():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    findings = qa.qa_findings(ev)
    assert "QA_REVIEW_POLICY_MODE_INVALID" not in findings
    assert "QA_INDEPENDENT_SOURCE_REVIEW_MISSING" not in findings
    assert "QA_SIRT_INDEPENDENT_EVIDENCE_MISSING" not in findings
    assert "QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" not in findings


def test_sirt_stale_review_receipt_holds():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    ev["sirt_review_evidence"]["head_sha"] = "b" * 40
    assert "QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" in qa.qa_findings(ev)


def test_sirt_forged_author_review_holds():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    ev["sirt_review_evidence"]["reviewer"]["user_id"] = 101
    assert "QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" in qa.qa_findings(ev)


def test_sirt_model_certification_claim_holds():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    ev["sirt_review_evidence"]["model_certified"] = True
    assert "QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" in qa.qa_findings(ev)


def test_sirt_review_error_holds():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    ev["sirt_review_evidence"]["review_status"] = "HOLD"
    assert "QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" in qa.qa_findings(ev)


def test_sirt_missing_reviewer_id_holds():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    ev["sirt_review_evidence"]["reviewer"]["user_id"] = None
    assert "QA_SIRT_INDEPENDENT_EVIDENCE_INVALID" in qa.qa_findings(ev)
