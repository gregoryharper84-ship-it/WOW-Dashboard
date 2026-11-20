"""E2E integration tests wiring solo_verified into QA, Release, policy receipts & workflows."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "artifacts/wow-engine/v17"))

import independent_release_checks as irc  # noqa: E402
import solo_automated_verification as sav  # noqa: E402

_RECEIPT_SPEC = importlib.util.spec_from_file_location(
    "_solo_receipt_mod",
    REPO_ROOT / ".github/scripts/independent_source_review_receipt.py",
)
assert _RECEIPT_SPEC and _RECEIPT_SPEC.loader
receipt_mod = importlib.util.module_from_spec(_RECEIPT_SPEC)
_RECEIPT_SPEC.loader.exec_module(receipt_mod)

HEAD = "c" * 40
QA_APP_ID = "5245832"
RELEASE_APP_ID = "5245853"
ROUTINE_FILE = "artifacts/wow-engine/learning/README.md"


def _qa_run():
    return {
        "id": 987654321,
        "run_attempt": 1,
        "name": "wow-v17-independent-qa-check",
        "workflow_id": 380562324,
        "path": ".github/workflows/wow-v17-independent-qa-check.yml",
        "event": "workflow_run",
        "head_branch": "main",
        "repository": sav.REPO,
        "head_sha": "d" * 40,
    }


def _policy(mode: str = "solo_verified", head: str = HEAD):
    return receipt_mod.build_qa_review_policy(
        repository=sav.REPO,
        pr_number=2740,
        head_sha=head,
        head_ref="feature/solo-test",
        mode=mode,
        run=_qa_run(),
        now=datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc),
    )


def _solo_receipt(head: str = HEAD, **overrides):
    rec = sav.decision(
        "SOLO_R_EVIDENCE_READY",
        sha=head,
        change_class="R",
        checks=sav.REQUIRED_NATIVE_CHECKS,
        assurance_level="SOLO_AUTOMATED_DETERMINISTIC_VERIFICATION",
        administration_audit={
            "execution_isolated_from_pr_code": True,
            "distinct_qa_release_apps": True,
            "qa_app_id": int(QA_APP_ID),
            "release_app_id": int(RELEASE_APP_ID),
            "independent_administration_proven": False,
            "independent_human_review_performed": False,
            "owner_administered_apps": True,
        },
    )
    rec.update(overrides)
    return rec


def _base_ev(head: str = HEAD, mode: str = "solo_verified", include_qa_check: bool = False):
    runs = [
        {
            "id": i + 1,
            "name": name,
            "head_sha": head,
            "status": "completed",
            "conclusion": "success",
            "app_id": 15368,
        }
        for i, name in enumerate(irc.QA_EVIDENCE_CHECKS)
    ]
    if include_qa_check:
        runs.append(
            {
                "id": 999,
                "name": irc.QA_CHECK,
                "head_sha": head,
                "status": "completed",
                "conclusion": "success",
                "app_id": int(QA_APP_ID),
            }
        )
    return {
        "repository": sav.REPO,
        "pr_number": 2740,
        "expected_head_sha": head,
        "evidence_errors": [],
        "owner_login": "gregoryharper84-ship-it",
        "pr": {
            "number": 2740,
            "body": "Incident: #2020\nSolo automated verification test.",
            "state": "open",
            "draft": False,
            "merged": False,
            "head_sha": head,
            "head_ref": "feature/solo-test",
            "head_repo": sav.REPO,
            "base_ref": "main",
            "author": "gregoryharper84-ship-it",
            "author_id": 285088163,
            "changed_files": 1,
        },
        "files": [ROUTINE_FILE],
        "source_review_paths": [ROUTINE_FILE],
        "zero_credit_disallowed_file_operations": [],
        "check_runs": runs,
        "native_workflow_runs": [],
        "reviews": [],
        "qa_review_policy": _policy(mode=mode, head=head),
        "current_review_active": False,
        "zero_credit_enabled": False,
        "solo_verified_enabled": True,
        "independent_review_active": False,
        "source_review": None,
        "solo_verification_evidence": _solo_receipt(head=head),
    }


def test_qa_review_policy_builds_and_verifies_solo_verified_mode():
    now = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)
    pol = _policy(mode="solo_verified")
    assert pol["mode"] == "solo_verified"
    assert pol["canary_scope"] is None
    api_run = {
        **_qa_run(),
        "repository": {"full_name": sav.REPO},
        "head_repository": {"full_name": sav.REPO},
        "status": "completed",
        "conclusion": "success",
    }
    receipt_mod.verify_qa_review_policy(
        pol,
        run=api_run,
        expected_repository=sav.REPO,
        expected_pr_number=2740,
        expected_head_sha=HEAD,
        expected_attempt=1,
        now=now,
    )


def test_qa_and_release_pass_for_valid_solo_verified_evidence_with_honest_summary():
    ev = _base_ev(include_qa_check=True)
    qa_f = irc.qa_findings(ev)
    assert qa_f == []
    qa_dec = irc.decision("qa", qa_f, ev)
    assert qa_dec["conclusion"] == "success"
    assert "no independent human review performed" in qa_dec["summary"]
    assert qa_dec["can_execute"] is False
    assert qa_dec["merge_or_release_authorized"] is False

    rel_f = irc.release_findings(ev, qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID)
    assert rel_f == []
    rel_dec = irc.decision("release", rel_f, ev)
    assert rel_dec["conclusion"] == "success"
    assert rel_dec["can_execute"] is False


def test_solo_verified_activation_dominates_legacy_or_missing_policy():
    ev = _base_ev(mode="legacy")
    ev["solo_verified_enabled"] = True
    findings = irc.qa_findings(ev)
    assert "QA_SOLO_VERIFIED_POLICY_CONFLICT" in findings

    ev_missing = _base_ev()
    ev_missing["qa_review_policy"] = None
    assert "QA_SOLO_VERIFIED_POLICY_CONFLICT" in irc.qa_findings(ev_missing)


def test_solo_verified_rejects_forged_evidence_and_mode_conflicts():
    # Claiming independent_human_review_performed=True must fail closed
    ev_forged = _base_ev()
    ev_forged["solo_verification_evidence"] = _solo_receipt(independent_human_review_performed=True)
    assert "QA_SOLO_VERIFIED_EVIDENCE_INVALID" in irc.qa_findings(ev_forged)

    # Missing evidence fails closed
    ev_missing = _base_ev()
    ev_missing["solo_verification_evidence"] = None
    assert "QA_SOLO_VERIFIED_EVIDENCE_MISSING" in irc.qa_findings(ev_missing)

    # Overlapping zero_credit or current_review_active fails closed
    ev_conflict = _base_ev()
    ev_conflict["zero_credit_enabled"] = True
    assert "QA_SOLO_VERIFIED_POLICY_CONFLICT" in irc.qa_findings(ev_conflict)

    # Release rejects foreign QA App check even if QA findings pass
    ev_foreign = _base_ev(include_qa_check=True)
    ev_foreign["check_runs"][-1]["app_id"] = int(RELEASE_APP_ID)
    rel_f = irc.release_findings(ev_foreign, qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID)
    assert "RELEASE_QA_FOREIGN_SOURCE" in rel_f
    assert "RELEASE_QA_MISSING" in rel_f


def test_workflows_wire_solo_verified_opt_in_with_mutual_exclusion():
    qa_wf = yaml.safe_load((REPO_ROOT / ".github/workflows/wow-v17-independent-qa-check.yml").read_text())
    rel_wf = yaml.safe_load((REPO_ROOT / ".github/workflows/wow-v17-release-authority-check.yml").read_text())

    qa_steps = {s["name"]: s for s in qa_wf["jobs"]["qa"]["steps"] if "name" in s}
    policy_step = qa_steps["Select exact-head review policy"]
    assert "WOW_QA_SOLO_VERIFICATION_ACTIVE" in str(policy_step["env"]["SOLO_VERIFIED_ACTIVATION"])
    assert "QA_SOLO_VERIFIED_MODES_CONFLICT" in policy_step["run"]
    assert "mode=solo_verified" in policy_step["run"]

    decide_step = qa_steps["Decide at exact head (read-only)"]
    assert "--solo-verified-policy-enabled" in decide_step["run"]
    assert "WOW_QA_SOLO_OWNER_ADMIN_ACCEPTED" in decide_step["env"]

    rel_steps = {s["name"]: s for s in rel_wf["jobs"]["release"]["steps"] if "name" in s}
    rel_decide = rel_steps["Decide at exact head (read-only)"]
    assert "WOW_QA_SOLO_VERIFICATION_ACTIVE" in str(rel_decide["env"]["SOLO_VERIFIED_ACTIVATION"])
    assert "--solo-verified-policy-enabled" in rel_decide["run"]


def test_gather_solo_verified_mode_collects_and_fails_closed_on_missing_token_or_outage(monkeypatch):
    def fake_fetch(path: str):
        if path == f"repos/{sav.REPO}":
            return 200, {"owner": {"login": "gregoryharper84-ship-it", "id": 285088163}}
        if path == f"repos/{sav.REPO}/pulls/2740":
            return 200, {
                "number": 2740,
                "body": "Incident: #2020",
                "state": "open",
                "draft": False,
                "merged": False,
                "head": {"sha": HEAD, "ref": "feature/solo-test", "repo": {"full_name": sav.REPO}},
                "base": {"ref": "main"},
                "user": {"login": "gregoryharper84-ship-it", "id": 285088163},
                "changed_files": 1,
            }
        if path.startswith(f"repos/{sav.REPO}/pulls/2740/files"):
            return 200, [{"filename": ROUTINE_FILE, "status": "modified"}]
        if path.startswith(f"repos/{sav.REPO}/commits/{HEAD}/check-runs"):
            return 200, {
                "total_count": len(irc.QA_EVIDENCE_CHECKS),
                "check_runs": [
                    {
                        "id": idx + 1,
                        "name": name,
                        "head_sha": HEAD,
                        "status": "completed",
                        "conclusion": "success",
                        "app": {"id": 15368},
                    }
                    for idx, name in enumerate(irc.QA_EVIDENCE_CHECKS)
                ],
            }
        if path.startswith(f"repos/{sav.REPO}/actions/"):
            return 200, {"total_count": 0, "workflow_runs": []}
        if path.startswith(f"repos/{sav.REPO}/pulls/2740/reviews"):
            return 200, []
        return 404, {}

    # Missing GH_TOKEN fails closed inside gather()
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("WOW_QA_APP_ID", "not-an-int")
    monkeypatch.setenv("WOW_RELEASE_APP_ID", "not-an-int")
    ev_no_tok = irc.gather(
        sav.REPO,
        2740,
        HEAD,
        fetch=fake_fetch,
        qa_review_policy=_policy(mode="solo_verified", head=HEAD),
        solo_verified_enabled=True,
    )
    assert ev_no_tok["solo_verification_evidence"]["code"] == "SOLO_GITHUB_EVIDENCE_UNAVAILABLE"
    assert "QA_SOLO_VERIFIED_EVIDENCE_INVALID" in irc.qa_findings(ev_no_tok)

    # Upstream exception inside solo_collect fails closed
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv("WOW_QA_APP_ID", QA_APP_ID)
    monkeypatch.setenv("WOW_RELEASE_APP_ID", RELEASE_APP_ID)
    monkeypatch.setattr(irc, "solo_collect", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("api down")))
    ev_err = irc.gather(
        sav.REPO,
        2740,
        HEAD,
        fetch=fake_fetch,
        qa_review_policy=_policy(mode="solo_verified", head=HEAD),
        solo_verified_enabled=True,
    )
    assert ev_err["solo_verification_evidence"]["code"] == "SOLO_GITHUB_EVIDENCE_UNAVAILABLE"

    # Happy path inside gather() populates valid solo_verification_evidence
    monkeypatch.setattr(irc, "solo_collect", lambda *a, **kw: _solo_receipt(head=HEAD))
    ev_ok = irc.gather(
        sav.REPO,
        2740,
        HEAD,
        fetch=fake_fetch,
        qa_review_policy=_policy(mode="solo_verified", head=HEAD),
        solo_verified_enabled=True,
    )
    assert ev_ok["solo_verification_evidence"]["code"] == "SOLO_R_EVIDENCE_READY"
    assert irc.qa_findings(ev_ok) == []


def test_solo_verified_policy_target_mismatch_fails_closed():
    ev = _base_ev()
    ev["qa_review_policy"]["target"]["head_ref"] = "other-branch"
    assert "QA_SOLO_VERIFIED_POLICY_TARGET_MISMATCH" in irc.qa_findings(ev)


def test_deprecated_gemini_capture_is_absent_and_independent_mode_fails_closed():
    # The old Gemini source-review capture (and its credential) are retired
    # rather than silently used by the solo-maintainer QA replacement.
    workflow_path = REPO_ROOT / ".github/workflows/wow-v17-independent-qa-check.yml"
    raw = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)
    qa_steps = workflow["jobs"]["qa"]["steps"]
    names = [s.get("name", "") for s in qa_steps]
    assert "Capture complete exact-head source evidence" not in names
    assert "Run isolated read-only Gemini semantic review" not in names
    assert "Submit Gemini App review at exact PR head" not in names
    assert "WOW_QA_REVIEW_GEMINI_API_KEY" not in raw
    assert "wow_v28_gemini_qa_bridge.py" not in raw
    assert "--capture" not in raw

    reject = next(s for s in qa_steps if s.get("name") == "Reject retired independent capture mode")
    assert reject["if"] == "steps.policy.outputs.review_active == 'true'"
    assert "QA_LEGACY_INDEPENDENT_CAPTURE_RETIRED" in reject["run"]
    assert "exit 1" in reject["run"]

    # Terminal QA HOLD must be published even when the retired path fails;
    # never remove native exact-head checks or the existing release workflow.
    terminal = next(s for s in qa_steps if s.get("name") == "Preserve terminal QA decision for every resolved target")
    publish = next(s for s in qa_steps if s.get("name") == "Publish exact-head check")
    assert "always()" in terminal["if"]
    assert "always()" in publish["if"]
    assert "QA_REVIEW_EXECUTION_FAILED" in terminal["run"]


def test_solo_qa_never_accepts_protected_success_codes_or_nonroutine_changes():
    # Reproduces the latent bypass: QA used to accept success codes the
    # protected verifier intentionally never emits, or a routine receipt for
    # a P/C path. Release must independently re-derive the same HOLD.
    cases = [
        ("dormant protected code", {"code": "SOLO_PROTECTED_EVIDENCE_READY", "change_class": "P"}, None, []),
        ("dormant external code", {"code": "SOLO_EXTERNALLY_ATTESTED_EVIDENCE_READY", "change_class": "P+C"}, None, []),
        ("routine code claiming protected class", {"change_class": "C"}, None, []),
        ("routine code on trust root", {}, ["artifacts/wow-engine/v17/independent_release_checks.py"], []),
        ("routine code on model file", {}, ["artifacts/wow-engine/v17/nfl_fitted_specialist.py"], []),
        ("routine receipt with rename", {}, [ROUTINE_FILE], [ROUTINE_FILE]),
        ("forged independent administration", {
            "independent_administration_proven": True,
            "administration_audit": {
                "execution_isolated_from_pr_code": True,
                "distinct_qa_release_apps": True,
                "independent_human_review_performed": False,
                "independent_administration_proven": True,
                "owner_administered_apps": False,
            },
        }, None, []),
    ]
    for label, overrides, paths, operations in cases:
        ev = _base_ev(include_qa_check=True)
        ev["solo_verification_evidence"] = _solo_receipt(**overrides)
        if paths is not None:
            ev["files"] = paths
            ev["source_review_paths"] = paths
        ev["zero_credit_disallowed_file_operations"] = operations
        assert "QA_SOLO_VERIFIED_EVIDENCE_INVALID" in irc.qa_findings(ev), label
        assert "RELEASE_QA_SOLO_VERIFIED_EVIDENCE_INVALID" in irc.release_findings(
            ev, qa_app_id=QA_APP_ID, release_app_id=RELEASE_APP_ID
        ), label
