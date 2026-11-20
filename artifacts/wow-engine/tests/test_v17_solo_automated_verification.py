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
        "base": {"ref": "main", "sha": "c" * 40},
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
    assert res["verification_status"] == "HOLD"
    assert res["code"] == "SOLO_PROTECTED_INDEPENDENT_ADMIN_UNPROVEN"
    assert "SOLO_INDEPENDENT_ADMIN_NOT_PROVEN" in res["findings"]
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
    assert res["verification_status"] == "HOLD"
    assert res["code"] == "SOLO_VERIFICATION_HOLD"
    assert "SOLO_EXTERNAL_ADMIN_ATTESTATION_INVALID" in res["findings"]
    assert res["independent_administration_proven"] is False
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

    # Valid calibration replay alone cannot independently approve Class C.
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
    assert valid["verification_status"] == "HOLD"
    assert "SOLO_INDEPENDENT_ADMIN_NOT_PROVEN" in valid["findings"]
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


# ---------------------------------------------------------------------------
# 6. Exhaustive edge-case, HTTP hardening, rename & CLI receipt coverage
# ---------------------------------------------------------------------------

def test_model_artifact_suffix_under_v17_classifies_as_class_c():
    c = sav.classify_change_scope(["artifacts/wow-engine/v17/weights/nba_fitted.joblib"])
    assert c["change_class"] == "C"
    assert "artifacts/wow-engine/v17/weights/nba_fitted.joblib" in c["model_calibration_paths"]


def test_evaluate_rejects_invalid_targets_evidence_pr_owner_and_non_green_check():
    bad_repo = sav.evaluate(
        repository="other/repo",
        expected_sha=HEAD,
        pr=_pr(),
        files=[ROUTINE_FILE],
        check_runs=_check_runs(include_codeql=False),
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
    )
    assert bad_repo["code"] == "SOLO_TARGET_INVALID"

    bad_types = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr="not-a-dict",
        files=[ROUTINE_FILE],
        check_runs=[],
        owner_id=OWNER_ID,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
    )
    assert bad_types["code"] == "SOLO_EVIDENCE_INVALID"

    non_green = _check_runs(include_codeql=False)
    non_green[0]["conclusion"] = "failure"
    bad_pr = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(draft=True),
        files=[ROUTINE_FILE],
        check_runs=non_green,
        owner_id=0,
        qa_app_id=QA_APP_ID,
        release_app_id=RELEASE_APP_ID,
    )
    assert bad_pr["verification_status"] == "HOLD"
    assert "SOLO_PR_IDENTITY_OR_HEAD_INVALID" in bad_pr["findings"]
    assert "SOLO_OWNER_IDENTITY_INVALID" in bad_pr["findings"]
    assert any(f.startswith("SOLO_REQUIRED_CHECK_NOT_GREEN:") for f in bad_pr["findings"])


def test_adversarial_calibration_and_external_admin_receipt_negative_fields():
    adv_bad = sav._validate_adversarial_receipt(
        _adversarial_receipt(
            schema_version=2,
            head_sha=OTHER_HEAD,
            status="FAIL",
            passed_count=0,
            failed_count=1,
            negative_cases_verified=[],
            unresolved_high_critical_findings=2,
            payload_sha256="not-hex",
        ),
        HEAD,
    )
    assert set(adv_bad) >= {
        "SOLO_ADVERSARIAL_RECEIPT_INVALID",
        "SOLO_ADVERSARIAL_RECEIPT_HEAD_MISMATCH",
        "SOLO_ADVERSARIAL_TESTS_NOT_PASSED",
        "SOLO_ADVERSARIAL_NEGATIVE_CASES_MISSING",
        "SOLO_UNRESOLVED_SECURITY_FINDINGS",
        "SOLO_ADVERSARIAL_RECEIPT_DIGEST_INVALID",
    }

    cal_bad = sav._validate_calibration_replay_receipt(
        _calibration_receipt(
            schema_version=2,
            head_sha=OTHER_HEAD,
            status="DRIFT",
            model_version="",
            artifact_sha256="bad",
            calibrated_lower_bound_sha256="bad",
            source_snapshot_sha256="bad",
            chronology_verified=False,
        ),
        HEAD,
    )
    assert set(cal_bad) >= {
        "SOLO_CALIBRATION_REPLAY_RECEIPT_INVALID",
        "SOLO_CALIBRATION_REPLAY_HEAD_MISMATCH",
        "SOLO_CALIBRATION_REPLAY_NOT_CONSISTENT",
        "SOLO_CALIBRATION_REPLAY_MODEL_VERSION_MISSING",
        "SOLO_CALIBRATION_REPLAY_DIGEST_INVALID:artifact_sha256",
        "SOLO_CALIBRATION_REPLAY_DIGEST_INVALID:calibrated_lower_bound_sha256",
        "SOLO_CALIBRATION_REPLAY_DIGEST_INVALID:source_snapshot_sha256",
        "SOLO_CALIBRATION_REPLAY_INTEGRITY_FAILED",
    }

    ok_non_map, bad_non_map = sav._validate_external_admin_attestation(
        "not-a-map", expected_sha=HEAD, owner_id=OWNER_ID, author_id=OWNER_ID, head_commit_author_id=OWNER_ID
    )
    assert ok_non_map is False
    assert bad_non_map == ["SOLO_EXTERNAL_ADMIN_ATTESTATION_INVALID"]

    ok_bad_att, bad_att = sav._validate_external_admin_attestation(
        _external_admin(verified=False, head_sha=OTHER_HEAD, attestation_sha256="short"),
        expected_sha=HEAD,
        owner_id=OWNER_ID,
        author_id=OWNER_ID,
        head_commit_author_id=OWNER_ID,
    )
    assert ok_bad_att is False
    assert bad_att == ["SOLO_EXTERNAL_ADMIN_ATTESTATION_INVALID"]


def test_http_no_redirect_and_response_size_limit():
    handler = sav.NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example") is None

    class _FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self, n: int = -1) -> bytes:
            return self._payload[:n]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeOpener:
        def __init__(self, payload: bytes):
            self._payload = payload

        def open(self, req, timeout=15):
            return _FakeResp(self._payload)

    with patch.object(sav, "build_opener", return_value=_FakeOpener(b'{"ok":true}')):
        assert sav.github_json("pulls/1", "tok") == {"ok": True}

    with patch.object(sav, "build_opener", return_value=_FakeOpener(b"x" * 2_000_001)):
        with pytest.raises(ValueError, match="SOLO_GITHUB_RESPONSE_LIMIT"):
            sav.github_json("pulls/1", "tok")


def test_collect_and_evaluate_edge_cases_and_rename_escalation():
    assert sav.collect_and_evaluate(
        0, HEAD, token="tok", qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID
    )["code"] == "SOLO_TARGET_INVALID"

    assert sav.collect_and_evaluate(
        2740, HEAD, token="", qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID
    )["code"] == "SOLO_GITHUB_EVIDENCE_UNAVAILABLE"

    with patch.object(sav, "github_json", side_effect=ValueError("boom")):
        assert sav.collect_and_evaluate(
            2740, HEAD, token="tok", qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID
        )["code"] == "SOLO_GITHUB_EVIDENCE_UNAVAILABLE"

    # Malformed PR record -> SOLO_EVIDENCE_INVALID
    with patch.object(sav, "github_json", side_effect=[{}, {}, {}, [], {"total_count": 0, "check_runs": []}, {}]):
        assert sav.collect_and_evaluate(
            2740, HEAD, token="tok", qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID
        )["code"] == "SOLO_EVIDENCE_INVALID"

    # Malformed repo/checks -> SOLO_EVIDENCE_INVALID
    with patch.object(sav, "github_json", side_effect=[_pr(), "bad-repo", {}, [], {}, _pr()]):
        assert sav.collect_and_evaluate(
            2740, HEAD, token="tok", qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID
        )["code"] == "SOLO_EVIDENCE_INVALID"

    # Malformed file row -> SOLO_EVIDENCE_INVALID
    with patch.object(
        sav,
        "github_json",
        side_effect=[_pr(), {"owner": {"id": OWNER_ID}}, {}, ["bad-row"], {"total_count": 0, "check_runs": []}, _pr()],
    ):
        assert sav.collect_and_evaluate(
            2740, HEAD, token="tok", qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID
        )["code"] == "SOLO_EVIDENCE_INVALID"

    # Renamed file with previous_filename escalates R-allowlist path to P (fails closed without adversarial receipt)
    renamed_files = [{"filename": ROUTINE_FILE, "status": "renamed", "previous_filename": "old/path.md"}]
    with patch.object(
        sav,
        "github_json",
        side_effect=[
            _pr(),
            {"owner": {"id": OWNER_ID}},
            {"author": {"id": OWNER_ID}},
            renamed_files,
            {"total_count": 4, "check_runs": _check_runs(include_codeql=False)},
            _pr(),
        ],
    ):
        res = sav.collect_and_evaluate(
            2740, HEAD, token="tok", qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID
        )
    assert res["verification_status"] == "HOLD"
    assert res["change_class"] == "P"


def test_cli_loads_receipt_files_and_rejects_malformed_json(tmp_path, capsys):
    adv_path = tmp_path / "adv.json"
    cal_path = tmp_path / "cal.json"
    ext_path = tmp_path / "ext.json"
    adv_path.write_text(json.dumps(_adversarial_receipt()), encoding="utf-8")
    cal_path.write_text(json.dumps(_calibration_receipt()), encoding="utf-8")
    ext_path.write_text(json.dumps(_external_admin()), encoding="utf-8")

    responses = [
        _pr(sha=HEAD),
        {"owner": {"id": OWNER_ID}},
        {"author": {"id": OWNER_ID}},
        [{"filename": MODEL_FILE, "status": "modified"}],
        {"total_count": 5, "check_runs": _check_runs(include_codeql=True)},
        _pr(sha=HEAD),
    ]
    with patch.object(sav, "github_json", side_effect=responses), patch.dict(
        "os.environ", {"GH_TOKEN": "tok", "WOW_QA_APP_ID": str(QA_APP_ID), "WOW_RELEASE_APP_ID": str(RELEASE_APP_ID)}
    ):
        rc = sav.main([
            "--pr", "2740", "--head-sha", HEAD,
            "--adversarial-receipt", str(adv_path),
            "--calibration-replay-receipt", str(cal_path),
            "--external-admin-attestation", str(ext_path),
        ])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["code"] == "SOLO_VERIFICATION_HOLD"
    assert "SOLO_EXTERNAL_ADMIN_ATTESTATION_INVALID" in out["findings"]

    # Non-object JSON receipt fails closed with exit code 1
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("[]", encoding="utf-8")
    rc_bad = sav.main(["--pr", "2740", "--head-sha", HEAD, "--adversarial-receipt", str(bad_path)])
    out_bad = json.loads(capsys.readouterr().out)
    assert rc_bad == 1
    assert out_bad["code"] == "SOLO_EVIDENCE_INVALID"


def test_owner_controlled_external_admin_json_cannot_prove_independent_administration():
    # A syntactically plausible but unsigned fixture has no authenticated
    # provenance. A different claimed GitHub user ID is not independent proof.
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
    assert res["verification_status"] == "HOLD"
    assert res["independent_administration_proven"] is False
    assert "SOLO_EXTERNAL_ADMIN_ATTESTATION_INVALID" in res["findings"]


def test_owner_controlled_opt_in_cannot_replace_independent_protected_signoff():
    # Multiple Apps can isolate execution, not the sole administrator.
    # The protected env flag may not convert absent independence into ready.
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
    assert res["verification_status"] == "HOLD"
    assert res["independent_administration_proven"] is False
    assert "SOLO_INDEPENDENT_ADMIN_NOT_PROVEN" in res["findings"]


def test_r_allowlist_compares_exact_github_paths_without_normalization_aliases():
    valid = sav.classify_change_scope(
        [ROUTINE_FILE], file_statuses={ROUTINE_FILE: "modified"}
    )
    assert valid["change_class"] == "R"
    for distinct_path in (ROUTINE_FILE + " ", ROUTINE_FILE.replace("/", "\\\\")):
        result = sav.classify_change_scope([distinct_path])
        assert result["change_class"] == "P", distinct_path
        assert distinct_path in result["non_allowlisted_paths"], distinct_path


def test_reversed_distinct_qa_release_app_roles_fail_closed():
    result = sav.evaluate(
        repository=sav.REPO,
        expected_sha=HEAD,
        pr=_pr(),
        files=[ROUTINE_FILE],
        file_statuses={ROUTINE_FILE: "modified"},
        check_runs=_check_runs(include_codeql=False),
        owner_id=OWNER_ID,
        qa_app_id=RELEASE_APP_ID,
        release_app_id=QA_APP_ID,
        head_commit_author_id=OWNER_ID,
    )
    assert result["verification_status"] == "HOLD"
    assert "SOLO_QA_RELEASE_APP_IDENTITY_INVALID" in result["findings"]


def test_collect_rejects_base_sha_race_even_when_head_is_stable():
    before = _pr(base={"ref": "main", "sha": "c" * 40})
    after = _pr(base={"ref": "main", "sha": "d" * 40})
    responses = [
        before,
        {"owner": {"id": OWNER_ID}},
        {"author": {"id": OWNER_ID}},
        [{"filename": ROUTINE_FILE, "status": "modified"}],
        {"total_count": 4, "check_runs": _check_runs(include_codeql=False)},
        after,
    ]
    with patch.object(sav, "github_json", side_effect=responses):
        result = sav.collect_and_evaluate(
            2740, HEAD, token="tok",
            qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID,
        )
    assert result["verification_status"] == "HOLD"
    assert result["code"] == "SOLO_PR_CHANGED_DURING_VERIFICATION"
