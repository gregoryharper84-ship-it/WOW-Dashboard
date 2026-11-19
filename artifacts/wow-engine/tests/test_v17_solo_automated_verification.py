"""Adversarial & contract tests for solo-maintainer-compatible automated verification."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "artifacts/wow-engine/v17"))

import solo_automated_verification as sav  # noqa: E402

HEAD = "a" * 40
OTHER_HEAD = "b" * 40
DIGEST_A = "1" * 64
DIGEST_B = "2" * 64
DIGEST_C = "3" * 64
OWNER_ID = 285088163
QA_APP_ID = 5245832
RELEASE_APP_ID = 5245853
ROUTINE_FILE = "artifacts/wow-engine/learning/README.md"
PROTECTED_FILE = "artifacts/wow-engine/v17/independent_release_checks.py"
MODEL_FILE = "artifacts/wow-engine/v17/nfl_spread_scale_calibrated_challenger.py"


def _pr(sha: str = HEAD, **overrides):
    base = {
        "number": 2740,
        "state": "open",
        "draft": False,
        "merged_at": None,
        "user": {"id": OWNER_ID, "login": "gregoryharper84-ship-it"},
        "base": {"ref": "main"},
        "head": {"sha": sha, "ref": "feature/solo-test", "repo": {"full_name": sav.REPO}},
    }
    base.update(overrides)
    return base


def _check_runs(sha: str = HEAD, include_codeql: bool = True, app_id: int = 15368):
    names = list(sav.PROTECTED_REQUIRED_CHECKS if include_codeql else sav.R_REQUIRED_CHECKS)
    return [
        {
            "id": 100 + idx,
            "name": name,
            "head_sha": sha,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": app_id},
        }
        for idx, name in enumerate(names)
    ]


def _adversarial_receipt(sha: str = HEAD, **overrides):
    receipt = {
        "schema_version": 1,
        "head_sha": sha,
        "status": "PASS",
        "passed_count": 42,
        "failed_count": 0,
        "negative_cases_verified": ["stale_sha", "wrong_app_id", "forged_admin", "can_execute_false"],
        "unresolved_high_critical_findings": 0,
        "payload_sha256": DIGEST_A,
    }
    receipt.update(overrides)
    return receipt


def _calibration_receipt(sha: str = HEAD, **overrides):
    receipt = {
        "schema_version": 1,
        "head_sha": sha,
        "status": "REPLAY_CONSISTENT",
        "model_version": "nfl-spread-v17.4",
        "artifact_sha256": DIGEST_A,
        "calibrated_lower_bound_sha256": DIGEST_B,
        "source_snapshot_sha256": DIGEST_C,
        "chronology_verified": True,
        "leakage_free": True,
        "calibration_bounds_valid": True,
        "heuristic_fallback_used": False,
        "model_certified": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    receipt.update(overrides)
    return receipt


def _external_admin(sha: str = HEAD, admin_user_id: int = 999999, **overrides):
    att = {
        "schema_version": 1,
        "verified": True,
        "head_sha": sha,
        "admin_user_id": admin_user_id,
        "attestation_sha256": DIGEST_A,
    }
    att.update(overrides)
    return att


# ---------------------------------------------------------------------------
# 1. Normal model operation (certified specialist needs NO human reviewer)
# ---------------------------------------------------------------------------

def test_normal_model_operation_requires_no_human_reviewer_when_certified():
    res = sav.assess_normal_model_operation(
        specialist_certified=True,
        calibration_valid=True,
        inputs_fresh=True,
        pregame_eligible=True,
    )
    assert res["operation_status"] == "SCORING_ALLOWED"
    assert res["human_reviewer_required"] is False
    assert res["findings"] == []
    assert res["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert res["can_execute"] is False
    assert res["dry_run_only"] is True


@pytest.mark.parametrize(
    "kwargs,expected_finding",
    [
        ({"specialist_certified": False}, "MODEL_UNAVAILABLE"),
        ({"calibration_valid": False}, "MODEL_OUTPUT_INVALID"),
        ({"inputs_fresh": False}, "MODEL_INPUTS_INSUFFICIENT"),
        ({"pregame_eligible": False}, "PREGAME_ELIGIBILITY_FAILED"),
        ({"terminal_authority": "OTHER"}, "TERMINAL_AUTHORITY_INVALID"),
        ({"can_execute": True}, "CAN_EXECUTE_MUST_BE_FALSE"),
        ({"heuristic_fallback_used": True}, "HEURISTIC_FALLBACK_FORBIDDEN"),
    ],
)
def test_normal_model_operation_fails_closed_on_invariant_violations(kwargs, expected_finding):
    base = dict(
        specialist_certified=True,
        calibration_valid=True,
        inputs_fresh=True,
        pregame_eligible=True,
    )
    base.update(kwargs)
    res = sav.assess_normal_model_operation(**base)
    assert res["operation_status"] == "HOLD"
    assert res["human_reviewer_required"] is False
    assert expected_finding in res["findings"]
    assert res["can_execute"] is False


# ---------------------------------------------------------------------------
# 2. Change scope classification (R vs P vs C vs P+C)
# ---------------------------------------------------------------------------

def test_classify_change_scope_separates_r_p_c_and_pc():
    r = sav.classify_change_scope([ROUTINE_FILE], file_statuses={ROUTINE_FILE: "modified"})
    assert r["change_class"] == "R"
    assert r["r_eligible"] is True

    # Renaming or deleting an R-allowlist file escalates to P
    r_renamed = sav.classify_change_scope([ROUTINE_FILE], file_statuses={ROUTINE_FILE: "renamed"})
    assert r_renamed["change_class"] == "P"
    assert r_renamed["r_eligible"] is False

    # Protected workflow or release check script is P
    p = sav.classify_change_scope([PROTECTED_FILE])
    assert p["change_class"] == "P"
    assert PROTECTED_FILE in p["protected_paths"]

    # Calibration / specialist path is C
    c = sav.classify_change_scope([MODEL_FILE])
    assert c["change_class"] == "C"
    assert MODEL_FILE in c["model_calibration_paths"]

    # Touching both P and C is P+C
    pc = sav.classify_change_scope([PROTECTED_FILE, MODEL_FILE])
    assert pc["change_class"] == "P+C"

    # Empty file list fails closed as P
    empty = sav.classify_change_scope([])
    assert empty["change_class"] == "P"
    assert empty["r_eligible"] is False


# ---------------------------------------------------------------------------
# 3. Routine (R) engineering — solo-maintainer compatible without human reviewer
# ---------------------------------------------------------------------------

def test_routine_r_class_passes_without_human_reviewer():
    res = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[ROUTINE_FILE],
        file_statuses={ROUTINE_FILE: "modified"},
        check_runs=_check_runs(include_codeql=False),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        head_commit_author_id=OWNER_ID,
    )
    assert res["verification_status"] == "EVIDENCE_READY"
    assert res["code"] == "SOLO_R_EVIDENCE_READY"
    assert res["change_class"] == "R"
    assert res["independent_human_review_performed"] is False
    assert res["merge_or_release_authorized"] is False
    assert res["model_certified"] is False
    assert res["probability_publishable"] is False
    assert res["can_execute"] is False
    assert res["administration_audit"]["distinct_qa_release_apps"] is True


# ---------------------------------------------------------------------------
# 4. Protected (P) & Model/Calibration (C) — honest independence & fail-closed
# ---------------------------------------------------------------------------

def test_protected_p_class_holds_by_default_when_independent_admin_unproven():
    """QA/Release Apps administered by the solo owner are NOT independent admin."""
    res = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[PROTECTED_FILE],
        check_runs=_check_runs(include_codeql=True),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        head_commit_author_id=OWNER_ID,
        adversarial_receipt=_adversarial_receipt(),
        require_independent_admin=True,
    )
    assert res["verification_status"] == "HOLD"
    assert res["code"] == "SOLO_PROTECTED_INDEPENDENT_ADMIN_UNPROVEN"
    assert res["findings"] == ["SOLO_INDEPENDENT_ADMIN_NOT_PROVEN"]
    assert res["independent_administration_proven"] is False
    assert res["independent_human_review_performed"] is False
    assert res["administration_audit"]["owner_administered_apps"] is True


def test_protected_p_class_ready_with_explicit_solo_owner_admin_opt_in():
    res = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[PROTECTED_FILE],
        check_runs=_check_runs(include_codeql=True),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        head_commit_author_id=OWNER_ID,
        adversarial_receipt=_adversarial_receipt(),
        require_independent_admin=False,
        allow_solo_owner_admin_for_protected=True,
    )
    assert res["verification_status"] == "EVIDENCE_READY"
    assert res["code"] == "SOLO_PROTECTED_EVIDENCE_READY"
    assert res["assurance_level"] == "SOLO_AUTOMATED_PROTECTED_VERIFICATION_OWNER_ADMIN"
    assert res["independent_administration_proven"] is False
    assert res["independent_human_review_performed"] is False
    assert res["merge_or_release_authorized"] is False
    assert res["can_execute"] is False


def test_protected_p_class_ready_with_verified_external_non_owner_admin():
    res = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[PROTECTED_FILE],
        check_runs=_check_runs(include_codeql=True),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        head_commit_author_id=OWNER_ID,
        adversarial_receipt=_adversarial_receipt(),
        external_admin_attestation=_external_admin(admin_user_id=777777),
        require_independent_admin=True,
    )
    assert res["verification_status"] == "EVIDENCE_READY"
    assert res["code"] == "SOLO_EXTERNALLY_ATTESTED_EVIDENCE_READY"
    assert res["independent_administration_proven"] is True
    assert res["independent_human_review_performed"] is False


def test_forged_external_admin_same_as_owner_fails_closed():
    res = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[PROTECTED_FILE],
        check_runs=_check_runs(include_codeql=True),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        head_commit_author_id=OWNER_ID,
        adversarial_receipt=_adversarial_receipt(),
        external_admin_attestation=_external_admin(admin_user_id=OWNER_ID),
        require_independent_admin=True,
    )
    assert res["verification_status"] == "HOLD"
    assert "SOLO_EXTERNAL_ADMIN_NOT_INDEPENDENT" in res["findings"]


def test_protected_p_class_rejects_missing_codeql_and_bad_adversarial_receipt():
    res = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[PROTECTED_FILE],
        check_runs=_check_runs(include_codeql=False),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        adversarial_receipt=_adversarial_receipt(unresolved_high_critical_findings=1),
        require_independent_admin=False,
        allow_solo_owner_admin_for_protected=True,
    )
    assert res["verification_status"] == "HOLD"
    assert f"SOLO_REQUIRED_CHECK_MISSING:{sav.SECURITY_SCAN_CHECK}" in res["findings"]
    assert "SOLO_UNRESOLVED_SECURITY_FINDINGS" in res["findings"]


def test_class_c_requires_valid_immutable_calibration_replay_receipt():
    # Missing replay receipt -> HOLD
    missing = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[MODEL_FILE],
        check_runs=_check_runs(include_codeql=True),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        adversarial_receipt=_adversarial_receipt(),
        calibration_replay_receipt=None,
        require_independent_admin=False,
        allow_solo_owner_admin_for_protected=True,
    )
    assert missing["verification_status"] == "HOLD"
    assert "SOLO_CALIBRATION_REPLAY_RECEIPT_MISSING" in missing["findings"]

    # Forged self-certification or heuristic fallback -> HOLD
    forged = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[MODEL_FILE],
        check_runs=_check_runs(include_codeql=True),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        adversarial_receipt=_adversarial_receipt(),
        calibration_replay_receipt=_calibration_receipt(
            heuristic_fallback_used=True, model_certified=True
        ),
        require_independent_admin=False,
        allow_solo_owner_admin_for_protected=True,
    )
    assert forged["verification_status"] == "HOLD"
    assert "SOLO_CALIBRATION_REPLAY_HEURISTIC_FALLBACK_FORBIDDEN" in forged["findings"]
    assert "SOLO_CALIBRATION_REPLAY_AUTHORITY_INVARIANT_VIOLATED" in forged["findings"]

    # Valid calibration replay receipt -> EVIDENCE_READY (still never certifies model)
    valid = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[MODEL_FILE],
        check_runs=_check_runs(include_codeql=True),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
        adversarial_receipt=_adversarial_receipt(),
        calibration_replay_receipt=_calibration_receipt(),
        require_independent_admin=False,
        allow_solo_owner_admin_for_protected=True,
    )
    assert valid["verification_status"] == "EVIDENCE_READY"
    assert valid["change_class"] == "C"
    assert valid["model_certified"] is False
    assert valid["probability_publishable"] is False
    assert valid["independent_scientific_acceptance_required"] is True


def test_non_distinct_qa_release_apps_and_wrong_check_app_fail_closed():
    res = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[ROUTINE_FILE],
        check_runs=_check_runs(include_codeql=False, app_id=99999),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=QA_APP_ID,  # Not distinct!
    )
    assert res["verification_status"] == "HOLD"
    assert "SOLO_QA_RELEASE_APP_IDENTITY_INVALID" in res["findings"]
    assert any(f.startswith("SOLO_REQUIRED_CHECK_MISSING:") for f in res["findings"])


# ---------------------------------------------------------------------------
# 5. Live collector TOCTOU race & pagination fail-closed tests
# ---------------------------------------------------------------------------

def test_collect_and_evaluate_detects_toctou_pr_head_race():
    pr_before = _pr(sha=HEAD)
    pr_after = _pr(sha=OTHER_HEAD)
    responses = [
        pr_before,
        {"owner": {"id": OWNER_ID}},
        {"author": {"id": OWNER_ID}},
        [{"filename": ROUTINE_FILE, "status": "modified"}],
        {"total_count": 4, "check_runs": _check_runs(include_codeql=False)},
        pr_after,
    ]
    with patch.object(sav, "github_json", side_effect=responses):
        res = sav.collect_and_evaluate(
            2740,
            HEAD,
            token="tok",
            qa_app_id=QA_APP_ID,
            release_app_id=RELEASE_APP_ID,
        )
    assert res["verification_status"] == "HOLD"
    assert res["code"] == "SOLO_PR_CHANGED_DURING_VERIFICATION"


def test_collect_and_evaluate_rejects_truncated_pagination():
    responses = [
        _pr(sha=HEAD),
        {"owner": {"id": OWNER_ID}},
        {"author": {"id": OWNER_ID}},
        [{"filename": ROUTINE_FILE, "status": "modified"}],
        {"total_count": 105, "check_runs": _check_runs(include_codeql=False)},
        _pr(sha=HEAD),
    ]
    with patch.object(sav, "github_json", side_effect=responses):
        res = sav.collect_and_evaluate(
            2740,
            HEAD,
            token="tok",
            qa_app_id=QA_APP_ID,
            release_app_id=RELEASE_APP_ID,
        )
    assert res["verification_status"] == "HOLD"
    assert res["code"] == "SOLO_EVIDENCE_PAGINATION_INCOMPLETE"


def test_collect_and_evaluate_happy_path_r_class_via_cli(capsys):
    responses = [
        _pr(sha=HEAD),
        {"owner": {"id": OWNER_ID}},
        {"author": {"id": OWNER_ID}},
        [{"filename": ROUTINE_FILE, "status": "modified"}],
        {"total_count": 4, "check_runs": _check_runs(include_codeql=False)},
        _pr(sha=HEAD),
    ]
    with patch.object(sav, "github_json", side_effect=responses), patch.dict(
        "os.environ", {"GH_TOKEN": "tok", "WOW_QA_APP_ID": str(QA_APP_ID), "WOW_RELEASE_APP_ID": str(RELEASE_APP_ID)}
    ):
        rc = sav.main(["--pr", "2740", "--head-sha", HEAD])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["verification_status"] == "EVIDENCE_READY"
    assert out["code"] == "SOLO_R_EVIDENCE_READY"
    assert out["can_execute"] is False
