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
        "sirt_enabled": True,
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


def test_sirt_required_checks_match_canonical_policy_order():
    from v17.sirt_review_evidence import REQUIRED
    assert REQUIRED == qa.REQUIRED_REGRESSION_CHECKS


def test_sirt_inactive_policy_rejects_previous_pass_receipt():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    ev["sirt_enabled"] = False
    assert "QA_SIRT_REVIEW_POLICY_CONFLICT" in qa.qa_findings(ev)


def test_sirt_receipt_cannot_overlap_gemini_activation():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    ev["current_review_active"] = True
    assert "QA_SIRT_REVIEW_POLICY_CONFLICT" in qa.qa_findings(ev)


def test_sirt_receipt_cannot_overlap_zero_credit_activation():
    ev = sample()
    ev["sirt_review_evidence"] = ready_receipt()
    ev["zero_credit_enabled"] = True
    assert "QA_SIRT_REVIEW_POLICY_CONFLICT" in qa.qa_findings(ev)



def test_sirt_activation_fences_legacy_and_other_historical_review_modes():
    # Pre-activation QA decisions remain append-only on GitHub. Once a
    # protected environment activates SIRT, none can be reused as authority.
    for mode in ("legacy", "independent", "canary", "zero_credit"):
        ev = sample()
        ev["qa_review_policy"]["mode"] = mode
        ev["sirt_review_evidence"] = ready_receipt()
        qa_findings = qa.qa_findings(ev)
        assert "QA_SIRT_REVIEW_POLICY_CONFLICT" in qa_findings, mode
        release_findings = qa.release_findings(
            ev, qa_app_id="101", release_app_id="202"
        )
        assert "RELEASE_QA_SIRT_REVIEW_POLICY_CONFLICT" in release_findings, mode


def test_sirt_activation_requires_current_review_policy_receipt():
    ev = sample()
    ev["qa_review_policy"] = None
    ev["sirt_review_evidence"] = ready_receipt()
    assert "QA_SIRT_REVIEW_POLICY_CONFLICT" in qa.qa_findings(ev)
    assert "RELEASE_QA_SIRT_REVIEW_POLICY_CONFLICT" in qa.release_findings(
        ev, qa_app_id="101", release_app_id="202"
    )


def test_sirt_disabled_does_not_retroactively_change_legacy_policy():
    ev = sample()
    ev["sirt_enabled"] = False
    ev["qa_review_policy"]["mode"] = "legacy"
    ev["source_review"] = None
    assert "QA_SIRT_REVIEW_POLICY_CONFLICT" not in qa.qa_findings(ev)
