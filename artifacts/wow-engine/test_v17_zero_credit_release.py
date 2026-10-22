"""Negative and positive checks for opt-in zero-credit deterministic governance."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "artifacts/wow-engine/v17"))
import independent_release_checks as policy

SHA = "a" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
ROOTS = frozenset({"src/trust-root.py"})


def evidence(*, enabled=True, author="copilot-swe-agent[bot]", path="src/code.py"):
    checks = [
        {"id": i, "name": name, "head_sha": SHA, "app_id": policy.GITHUB_ACTIONS_APP_ID,
         "status": "completed", "conclusion": "success"}
        for i, name in enumerate(policy.QA_EVIDENCE_CHECKS, 1)
    ]
    return {
        "repository": REPO, "pr_number": 1974, "expected_head_sha": SHA,
        "evidence_errors": [], "owner_login": "gregoryharper84-ship-it",
        "pr": {"head_sha": SHA, "head_ref": "fix/nfl", "head_repo": REPO, "base_ref": "main",
               "state": "open", "merged": False, "draft": False, "author": author},
        "files": [path], "reviews": [], "source_review": None,
        "independent_review_active": False, "current_review_active": False,
        "zero_credit_enabled": enabled,
        "qa_review_policy": {
            "mode": "zero_credit",
            "target": {"pr_number": 1974, "head_sha": SHA, "head_ref": "fix/nfl"},
        },
        "check_runs": checks,
    }


def qa(ev):
    return policy.qa_findings(ev, governance_files=ROOTS)


def test_free_mode_procedural_checks_pass_without_semantic_review():
    assert qa(evidence()) == []


def test_free_mode_opt_in_mandatory():
    assert "QA_ZERO_CREDIT_POLICY_NOT_ENABLED" in qa(evidence(enabled=False))


def test_free_mode_never_passes_with_claimed_source_review():
    ev = evidence()
    ev["source_review"] = {"artifact_id": 123}
    assert "QA_ZERO_CREDIT_SOURCE_REVIEW_UNEXPECTED" in qa(ev)


def test_free_mode_rejects_active_paid_mode():
    ev = evidence()
    ev["current_review_active"] = True
    assert "QA_ZERO_CREDIT_POLICY_CONFLICT" in qa(ev)


def test_free_mode_rejects_forged_target_head():
    ev = evidence()
    ev["qa_review_policy"]["target"]["head_sha"] = "b" * 40
    assert "QA_ZERO_CREDIT_POLICY_TARGET_MISMATCH" in qa(ev)


def test_free_mode_cannot_lower_legacy_governance_requirement():
    ev = evidence()
    ev["qa_review_policy"]["mode"] = "legacy"
    assert "QA_EVIDENCE_MISSING:Trusted exact-head engineering governance" in qa(ev)


def test_free_mode_still_requires_all_regressions():
    ev = evidence()
    ev["check_runs"].pop()
    assert any(x.startswith("QA_EVIDENCE_MISSING:") for x in qa(ev))


def test_free_mode_rejects_stale_head():
    ev = evidence()
    ev["pr"]["head_sha"] = "b" * 40
    assert "QA_HEAD_STALE" in qa(ev)


def test_free_mode_owner_authored_trust_root_still_holds():
    ev = evidence(author="gregoryharper84-ship-it", path="src/trust-root.py")
    assert "QA_TRUST_ROOT_OWNER_AUTHORED" in qa(ev)


def test_release_cannot_use_same_identity_as_qa():
    ev = evidence()
    ev["check_runs"].append({
        "id": 55, "name": policy.QA_CHECK, "head_sha": SHA,
        "app_id": 5245832, "conclusion": "success", "status": "completed",
    })
    assert policy.release_findings(
        ev, qa_app_id="5245832", release_app_id="5245853",
        governance_files=ROOTS,
    ) == []
    assert "RELEASE_IDENTITY_NOT_DISTINCT" in policy.release_findings(
        ev, qa_app_id="5245832", release_app_id="5245832",
        governance_files=ROOTS,
    )


def test_release_rejects_qa_from_wrong_app():
    ev = evidence()
    ev["check_runs"].append({
        "id": 55, "name": policy.QA_CHECK, "head_sha": SHA,
        "app_id": 15368, "conclusion": "success",
    })
    assert "RELEASE_QA_FOREIGN_SOURCE" in policy.release_findings(
        ev, qa_app_id="5245832", release_app_id="5245853",
        governance_files=ROOTS,
    )


def test_workflows_keep_paid_review_and_explicit_free_flag():
    qa_yaml = (ROOT / ".github/workflows/wow-v17-independent-qa-check.yml").read_text()
    release_yaml = (ROOT / ".github/workflows/wow-v17-release-authority-check.yml").read_text()
    assert "WOW_QA_ZERO_CREDIT_MODE" in qa_yaml
    assert "WOW_QA_ZERO_CREDIT_MODE" in release_yaml
    assert "--zero-credit-policy-enabled" in qa_yaml
    assert "--zero-credit-policy-enabled" in release_yaml
    assert "uses: ./.github/actions/wow-chatgpt-agent" in qa_yaml


def dispatcher_case(*, zero_credit=True, checks=None, author="copilot-swe-agent[bot]",
                    source_review="PENDING", file="src/code.py"):
    import governance_dispatcher as dispatch_policy
    checked = checks if checks is not None else [
        {"id": i, "name": name, "head_sha": SHA, "status": "completed",
         "conclusion": "success", "app": {"id": 15368},
         "started_at": f"2026-10-21T00:00:{i:02d}Z"}
        for i, name in enumerate(dispatch_policy.QA_EVIDENCE_CHECKS, 1)
    ]
    pr = {"number": 41, "state": "open", "draft": False,
          "merged_at": None, "changed_files": 1,
          "user": {"id": 9001, "login": author},
          "head": {"sha": SHA, "ref": "fix/nfl", "repo": {"full_name": REPO}},
          "base": {"ref": "main", "repo": {"full_name": REPO}}}
    return dispatch_policy.classify_target(
        repository=REPO, owner_id=285088163, owner_login="gregoryharper84-ship-it",
        pull=pr, file_records=[{"filename": file, "status": "modified"}],
        check_runs=checked, reviews=[], source_review=source_review,
        available_roles={"qa", "release"}, zero_credit_mode=zero_credit,
    )


def test_dispatcher_zero_credit_needs_no_nonexistent_source_workflow():
    decision = dispatcher_case()
    assert decision["action"] == "DISPATCH" and decision["role"] == "qa"


def test_dispatcher_legacy_still_requires_source_workflow():
    decision = dispatcher_case(zero_credit=False)
    assert decision["findings"] == ["SOURCE_REVIEW_WORKFLOW_UNAVAILABLE"]


def test_dispatcher_zero_credit_still_requires_all_checks():
    decision = dispatcher_case(checks=[])
    assert decision["action"] == "HOLD"
    assert decision["findings"][0].startswith("REQUIRED_CHECK_MISSING:")


def test_dispatcher_zero_credit_rejects_wrong_app_on_regressions():
    import governance_dispatcher as dp
    checks = [
        {"id": i, "name": name, "head_sha": SHA, "status": "completed",
         "conclusion": "success", "app": {"id": 666},
         "started_at": f"2026-10-21T00:00:{i:02d}Z"}
        for i, name in enumerate(dp.QA_EVIDENCE_CHECKS, 1)
    ]
    assert dispatcher_case(checks=checks)["action"] == "HOLD"


def test_dispatcher_zero_credit_routes_release_after_signed_qa_pass():
    import governance_dispatcher as dp
    checks = [
        {"id": i, "name": name, "head_sha": SHA, "status": "completed",
         "conclusion": "success", "app": {"id": 15368},
         "started_at": f"2026-10-21T00:00:{i:02d}Z"}
        for i, name in enumerate(dp.QA_EVIDENCE_CHECKS, 1)
    ]
    checks.append({"id": 30, "name": dp.QA_CHECK, "head_sha": SHA,
                   "status": "completed", "conclusion": "success",
                   "app": {"id": dp.QA_APP_ID},
                   "started_at": "2026-10-21T01:00:00Z"})
    decision = dispatcher_case(checks=checks)
    assert decision["action"] == "DISPATCH" and decision["role"] == "release"


def test_release_dispatch_requires_real_qa_run_identity():
    import pytest
    import governance_dispatcher_github as transport

    class Client:
        def __init__(self): self.body = None
        def post(self, _url, body):
            self.body = body

    workflow = {"id": 999, "path": transport.WORKFLOWS["release"]["path"]}
    client = Client()
    with pytest.raises(transport.DispatchError, match="RELEASE_QA_RUN_IDENTITY_MISSING"):
        transport.dispatch(client, "release", workflow, 41, SHA)
    transport.dispatch(client, "release", workflow, 41, SHA,
                       qa_run_id=120118553031, qa_run_attempt=2)
    assert client.body["inputs"]["qa_run_id"] == "120118553031"
    assert client.body["inputs"]["qa_run_attempt"] == "2"
