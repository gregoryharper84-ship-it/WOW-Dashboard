"""Regression for #1550 Phase C: Independent QA and Release Authority exact-head checks.

Decision logic is exercised directly; the workflows' real Bash is executed against a
fake ``gh`` on PATH, so target resolution, evidence gathering, decision and the
check-run payload are tested end to end without any GitHub access.
"""
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "artifacts/wow-engine/v17/independent_release_checks.py"
SOURCE_REVIEW_MODULE = ROOT / ".github/scripts/independent_source_review_receipt.py"
QA_WF = ROOT / ".github/workflows/wow-v17-independent-qa-check.yml"
REL_WF = ROOT / ".github/workflows/wow-v17-release-authority-check.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
OWNER = "gregoryharper84-ship-it"
HEAD = "a" * 40
OLD = "b" * 40
QA_APP, REL_APP, GHA = "1002", "1003", 15368

spec = importlib.util.spec_from_file_location("independent_release_checks", MODULE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
source_spec = importlib.util.spec_from_file_location("independent_source_review_receipt_for_qa", SOURCE_REVIEW_MODULE)
source_review = importlib.util.module_from_spec(source_spec)
assert source_spec and source_spec.loader
sys.modules[source_spec.name] = source_review
source_spec.loader.exec_module(source_review)


def source_review_evidence(
    files,
    *,
    author="wow-engineering[bot]",
    pr_number=7,
    producer_context=False,
    review_result=None,
):
    file_paths = sorted(files or ["artifacts/wow-engine/v17/fixture.py"])
    diff = [
        {
            "filename": path,
            "previous_filename": None,
            "status": "modified",
            "patch": "@@ -1 +1 @@\n-old\n+new",
            "additions": 1,
            "deletions": 1,
        }
        for path in file_paths
    ]
    diff_sha = source_review.canonical_diff(diff, expected_count=len(diff))[1]
    author_id = 285088163 if author == OWNER else 9001
    run = {
        "id": 12345,
        "run_attempt": 1,
        "name": source_review.WORKFLOW_NAME,
        "workflow_id": source_review.WORKFLOW_ID,
        "path": source_review.WORKFLOW_PATH,
        "event": "workflow_run",
        "head_branch": "main",
        "repository": REPO,
        "head_sha": OLD,
    }
    receipt = source_review.build_artifact(
        repository=REPO,
        pr={
            "number": pr_number,
            "state": "open",
            "draft": False,
            "merged_at": None,
            "changed_files": len(diff),
            "user": {"id": author_id, "login": author},
            "head": {"sha": HEAD, "ref": "fix/issue-1830", "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}},
        },
        files=diff,
        commit={
            "sha": HEAD,
            "commit": {"tree": {"sha": "d" * 40}, "message": "Fixture review"},
            "parents": [{"sha": "e" * 40}],
            "author": {"login": "copilot-swe-agent"},
            "committer": {"login": "copilot-swe-agent"},
        },
        review_result=review_result or {
            "decision": "PASS",
            "summary": "Reviewed all changed files.",
            "reviewed_head_sha": HEAD,
            "reviewed_diff_sha256": diff_sha,
            "reviewed_files": file_paths,
            "assessment": {
                "source_review": "PASS",
                "security_review": "PASS",
                "probability_integrity_review": "PASS",
            },
            "findings": [],
        },
        run=run,
        now=datetime.now(timezone.utc),
    )
    source_run = run if producer_context else {
        **run,
        "workflow_id": source_review.WORKFLOW_ID,
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
        "status": "completed",
        "conclusion": "success",
    }
    return {"artifact_id": 99, "artifact": receipt, "run": source_run}


def run_(name, conclusion="success", head=HEAD, app=GHA, at="2026-10-09T03:00:00Z", rid=1):
    return {"id": rid, "name": name, "head_sha": head, "status": "completed", "conclusion": conclusion,
            "app_id": app, "completed_at": at}


def evidence(files=("artifacts/wow-engine/v17/x.py",), author="wow-engineering[bot]", **over):
    ev = {
        "repository": REPO, "pr_number": 7, "expected_head_sha": HEAD, "evidence_errors": [],
        "owner_login": OWNER,
        "pr": {"number": 7, "body": "Incident: #2136", "state": "open", "draft": False, "merged": False, "head_sha": HEAD, "head_repo": REPO,
               "head_ref": "fix/issue-1830", "base_ref": "main", "author": author,
               "author_id": 285088163 if author == OWNER else 9001, "changed_files": len(files)},
        "files": list(files),
        "source_review_paths": list(files),
        "source_review": source_review_evidence(files, author=author),
        "qa_review_policy": {
            "mode": "independent",
            "target": {"pr_number": 7, "head_sha": HEAD, "head_ref": "fix/issue-1830"},
            "canary_scope": None,
            "source": {"run_id": "12345", "run_attempt": 1},
        },
        "independent_review_active": True,
        "current_review_active": False,
        "check_runs": [run_(n, rid=i) for i, n in enumerate(m.QA_EVIDENCE_CHECKS)],
        "reviews": [],
    }
    for key, value in over.items():
        if key in ev["pr"]:
            ev["pr"][key] = value
        else:
            ev[key] = value
    return ev


TRUST_ROOT_FILES = (".github/workflows/wow-v17-claude-engineering-worker.yml",)


# --- QA decisions --------------------------------------------------------------------


def test_ordinary_pr_with_complete_exact_head_evidence_passes():
    assert m.qa_findings(evidence()) == []


def test_legacy_policy_requires_trusted_governance_until_review_activation():
    ev = evidence(independent_review_active=False, source_review=None)
    ev["qa_review_policy"]["mode"] = "legacy"
    assert f"QA_EVIDENCE_MISSING:{m.TRUSTED_GOVERNANCE_CHECK}" in m.qa_findings(ev)
    ev["check_runs"].append(run_(m.TRUSTED_GOVERNANCE_CHECK, rid=900))
    assert m.qa_findings(ev) == []
    with_qa(ev)
    assert m.release_findings(ev, qa_app_id=QA_APP, release_app_id=REL_APP) == []


def test_inactive_legacy_trust_root_bootstrap_does_not_wait_for_its_own_receipt():
    ev = evidence(
        files=TRUST_ROOT_FILES,
        independent_review_active=False,
        source_review=None,
    )
    ev["qa_review_policy"]["mode"] = "legacy"
    ev["reviews"] = [{
        "user": OWNER,
        "state": "APPROVED",
        "commit_id": HEAD,
        "submitted_at": "2026-10-09T02:00:00Z",
    }]
    ev["check_runs"] = [r for r in ev["check_runs"] if r["name"] != m.TRUSTED_GOVERNANCE_CHECK]
    assert m.qa_findings(ev) == []


def test_capture_build_producer_context_reaches_local_qa_decision():
    review = source_review_evidence(
        ["artifacts/wow-engine/v17/x.py"],
        producer_context=True,
    )
    ev = evidence(source_review=review)
    ev["source_review"]["producer_context"] = True

    assert m.qa_findings(ev) == []

    ev["source_review"].pop("producer_context")
    assert any(
        finding.startswith("QA_INDEPENDENT_SOURCE_REVIEW_INVALID:SOURCE_REVIEW_WORKFLOW_RUN_UNTRUSTED")
        for finding in m.qa_findings(ev)
    )


def test_rejected_review_receipt_supersedes_prior_pass_for_same_head():
    files = ["artifacts/wow-engine/v17/x.py"]
    diff_sha = source_review.canonical_diff([{
        "filename": files[0],
        "previous_filename": None,
        "status": "modified",
        "patch": "@@ -1 +1 @@\n-old\n+new",
        "additions": 1,
        "deletions": 1,
    }], expected_count=1)[1]
    rejected = source_review_evidence(
        files,
        producer_context=True,
        review_result={
            "decision": "REJECT",
            "summary": "A blocking security defect is present.",
            "reviewed_head_sha": HEAD,
            "reviewed_diff_sha256": diff_sha,
            "reviewed_files": files,
            "assessment": {
                "source_review": "FAIL",
                "security_review": "FAIL",
                "probability_integrity_review": "PASS",
            },
            "findings": [{
                "severity": "high",
                "file": files[0],
                "detail": "Unsafe trust boundary.",
                "evidence": "The handler accepts untrusted authority.",
            }],
        },
    )
    ev = evidence(source_review=rejected)
    ev["source_review"]["producer_context"] = True

    assert "QA_INDEPENDENT_SOURCE_REVIEW_REJECTED" in m.qa_findings(ev)


def test_release_rejects_legacy_qa_after_global_activation_changes():
    ev = evidence(
        independent_review_active=False,
        source_review=None,
        current_review_active=True,
    )
    ev["qa_review_policy"]["mode"] = "legacy"
    ev["check_runs"].append(run_(m.TRUSTED_GOVERNANCE_CHECK, rid=901))
    with_qa(ev)
    findings = m.release_findings(ev, qa_app_id=QA_APP, release_app_id=REL_APP)
    assert "RELEASE_QA_REVIEW_POLICY_LEGACY_AFTER_ACTIVATION" in findings


def test_release_preserves_independent_qa_mode_after_global_activation_turns_off():
    ev = evidence(independent_review_active=True, current_review_active=False)
    ev["check_runs"].append(run_(m.TRUSTED_GOVERNANCE_CHECK, rid=902))
    with_qa(ev)
    assert "RELEASE_QA_INDEPENDENT_SOURCE_REVIEW_MISSING" not in m.release_findings(
        ev, qa_app_id=QA_APP, release_app_id=REL_APP,
    )
    ev["source_review"] = None
    findings = m.release_findings(ev, qa_app_id=QA_APP, release_app_id=REL_APP)
    assert "RELEASE_QA_INDEPENDENT_SOURCE_REVIEW_MISSING" in findings


def test_canary_policy_is_single_sha_scoped_and_rejects_trust_roots():
    ev = evidence()
    ev["qa_review_policy"].update(
        mode="canary",
        canary_scope={"pr_number": 7, "head_sha": HEAD},
    )
    assert m.qa_findings(ev) == []
    ev["qa_review_policy"]["canary_scope"]["head_sha"] = OLD
    assert "QA_REVIEW_CANARY_SCOPE_MISMATCH" in m.qa_findings(ev)
    ev = evidence(files=TRUST_ROOT_FILES)
    ev["qa_review_policy"].update(
        mode="canary",
        canary_scope={"pr_number": 7, "head_sha": HEAD},
    )
    assert "QA_REVIEW_CANARY_TRUST_ROOT" in m.qa_findings(ev)


@pytest.mark.parametrize("name", m.QA_EVIDENCE_CHECKS)
def test_each_missing_or_failed_evidence_check_holds(name):
    ev = evidence()
    ev["check_runs"] = [r for r in ev["check_runs"] if r["name"] != name]
    assert f"QA_EVIDENCE_MISSING:{name}" in m.qa_findings(ev)
    ev["check_runs"].append(run_(name, "failure", rid=99))
    assert f"QA_EVIDENCE_FAILED:{name}" in m.qa_findings(ev)


def test_same_named_check_from_another_app_is_ignored():
    ev = evidence()
    name = m.TRUSTED_GOVERNANCE_CHECK
    ev["check_runs"].append(run_(name, app=424242, rid=50))
    assert m.qa_findings(ev) == []


def test_evidence_at_another_commit_does_not_count():
    ev = evidence()
    ev["check_runs"] = [dict(r, head_sha=OLD) for r in ev["check_runs"]]
    assert all(f"QA_EVIDENCE_MISSING:{n}" in m.qa_findings(ev) for n in m.QA_EVIDENCE_CHECKS)


def test_latest_run_wins_for_reruns():
    name = "WOW governed probability backend"
    ev = evidence()
    ev["check_runs"].append(run_(name, "failure", at="2026-10-09T04:00:00Z", rid=200))
    assert f"QA_EVIDENCE_FAILED:{name}" in m.qa_findings(ev)


@pytest.mark.parametrize("over, code", [
    ({"head_sha": OLD}, "QA_HEAD_STALE"),
    ({"expected_head_sha": "not-a-sha"}, "QA_HEAD_INVALID"),
    ({"state": "closed"}, "QA_PR_NOT_OPEN"),
    ({"merged": True}, "QA_PR_NOT_OPEN"),
    ({"draft": True}, "QA_PR_DRAFT"),
    ({"base_ref": "dev"}, "QA_PR_BASE_NOT_MAIN"),
    ({"head_repo": "someone/fork"}, "QA_PR_HEAD_REPOSITORY_MISMATCH"),
    ({"files": []}, "QA_FILES_EMPTY"),
    ({"evidence_errors": ["CHECK_RUNS_UNREADABLE:403"]}, "QA_EVIDENCE_INCOMPLETE:CHECK_RUNS_UNREADABLE:403"),
])
def test_ineligible_or_unreadable_pr_holds(over, code):
    assert code in m.qa_findings(evidence(**over))


def test_trust_root_pr_needs_owner_approval_of_this_exact_head():
    ev = evidence(files=TRUST_ROOT_FILES)
    ev["check_runs"] = [r for r in ev["check_runs"] if r["name"] != m.TRUSTED_GOVERNANCE_CHECK]
    assert "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in m.qa_findings(ev)
    ev["reviews"] = [{"user": OWNER, "state": "APPROVED", "commit_id": OLD, "submitted_at": "2026-10-09T01:00:00Z"}]
    assert "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in m.qa_findings(ev)
    ev["reviews"].append({"user": OWNER, "state": "APPROVED", "commit_id": HEAD, "submitted_at": "2026-10-09T02:00:00Z"})
    assert m.qa_findings(ev) == []  # trust-root QA uses owner approval, not the governance receipt


def test_app_reproposal_of_owner_source_requires_independent_exact_head_review():
    ev = evidence(files=TRUST_ROOT_FILES, author="wow-engineering[bot]", reviews=[
        {"user": OWNER, "state": "APPROVED", "commit_id": HEAD,
         "submitted_at": "2026-10-09T02:00:00Z"},
    ])
    ev["source_review"] = None
    assert "QA_INDEPENDENT_SOURCE_REVIEW_MISSING" in m.qa_findings(ev)

    ev["source_review"] = source_review_evidence(
        TRUST_ROOT_FILES, author="wow-engineering[bot]")
    assert m.qa_findings(ev) == []


def test_later_owner_change_request_revokes_approval():
    ev = evidence(files=TRUST_ROOT_FILES, reviews=[
        {"user": OWNER, "state": "APPROVED", "commit_id": HEAD, "submitted_at": "2026-10-09T02:00:00Z"},
        {"user": OWNER, "state": "CHANGES_REQUESTED", "commit_id": HEAD, "submitted_at": "2026-10-09T03:00:00Z"}])
    assert "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in m.qa_findings(ev)


def test_non_owner_approval_does_not_count_for_trust_roots():
    ev = evidence(files=TRUST_ROOT_FILES, reviews=[
        {"user": "wow-independent-qa[bot]", "state": "APPROVED", "commit_id": HEAD, "submitted_at": "2026-10-09T02:00:00Z"}])
    assert "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in m.qa_findings(ev)


@pytest.mark.parametrize("path", [
    "artifacts/wow-engine/v17/independent_release_checks.py",   # QA cannot be weakened by an ordinary PR
    "artifacts/wow-engine/v17/agent_identity_policy.py",
    "artifacts/wow-engine/v17/engineering_provider_failover.py",
    "artifacts/wow-engine/v17/persistent_worker_safety.py",      # loaded via inline `from v17.` import
    ".agents/skills/wow-engineering-independent-review-agent/SKILL.md",
    ".agents/skills/brand-new-skill/SKILL.md",
    "artifacts/wow-engine/requirements.txt",
])
def test_governance_files_outside_github_are_trust_roots(path):
    ev = evidence(files=(path,))
    assert "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in m.qa_findings(ev)


def test_ordinary_engine_code_stays_autonomous():
    ev = evidence(files=("artifacts/wow-engine/v17/nfl_ml_challenger_v2.py", "docs/wow/engineering/notes.md"))
    assert m.qa_findings(ev) == []


def test_owner_authored_trust_root_pr_can_never_pass():
    ev = evidence(files=TRUST_ROOT_FILES, author=OWNER, reviews=[
        {"user": OWNER, "state": "APPROVED", "commit_id": HEAD, "submitted_at": "2026-10-09T02:00:00Z"}])
    assert "QA_TRUST_ROOT_OWNER_AUTHORED" in m.qa_findings(ev)


# --- Release decisions --------------------------------------------------------------


def with_qa(ev, conclusion="success", app=QA_APP):
    ev["check_runs"].append(run_(m.QA_CHECK, conclusion, app=app, rid=300))
    return ev


def test_green_ci_and_trusted_gate_without_source_review_hold_qa_and_release():
    ev = evidence()
    ev["check_runs"].append(run_(m.TRUSTED_GOVERNANCE_CHECK, rid=400))
    ev["source_review"] = None
    assert "QA_INDEPENDENT_SOURCE_REVIEW_MISSING" in m.qa_findings(ev)

    with_qa(ev)
    findings = m.release_findings(ev, qa_app_id=QA_APP, release_app_id=REL_APP)
    assert "RELEASE_QA_INDEPENDENT_SOURCE_REVIEW_MISSING" in findings
    assert m.decision("release", findings, ev)["conclusion"] == "failure"


@pytest.mark.parametrize("mutation, expected", [
    (lambda proof: proof["artifact"]["target"].update(head_sha=OLD), "HEAD_MISMATCH"),
    (lambda proof: proof["artifact"]["review"].update(decision="REJECT"),
     "SOURCE_REVIEW_REJECTION_EVIDENCE_MISSING"),
    (lambda proof: proof["run"].update(event="pull_request_target"), "WORKFLOW_RUN_UNTRUSTED"),
])
def test_rejected_stale_or_untrusted_source_review_holds_release_even_with_qa_success(mutation, expected):
    ev = evidence()
    mutation(ev["source_review"])
    assert any(expected in finding for finding in m.qa_findings(ev))

    with_qa(ev)
    findings = m.release_findings(ev, qa_app_id=QA_APP, release_app_id=REL_APP)
    assert any("INDEPENDENT_SOURCE_REVIEW" in finding for finding in findings)
    assert m.decision("release", findings, ev)["conclusion"] == "failure"


def test_release_passes_only_on_qa_app_success_at_exact_head():
    assert m.release_findings(with_qa(evidence()), qa_app_id=QA_APP, release_app_id=REL_APP) == []


def test_pending_required_regression_is_only_classified_for_latest_trusted_exact_head_run():
    ev = evidence()
    active = run_(m.REQUIRED_REGRESSION_CHECKS[0], app=GHA, rid=900)
    active.update(status="in_progress", conclusion=None, created_at=datetime.now(timezone.utc).isoformat())
    ev["check_runs"].append(active)

    assert m.pending_required_regressions(ev) == [m.REQUIRED_REGRESSION_CHECKS[0]]

    completed = dict(active, id=901, status="completed", conclusion="success")
    ev["check_runs"].append(completed)
    assert m.pending_required_regressions(ev) == []

    foreign = dict(active, id=902, app_id=REL_APP)
    ev["check_runs"].append(foreign)
    assert m.pending_required_regressions(ev) == []

    stale = dict(active, id=903, head_sha=OLD)
    ev["check_runs"].append(stale)
    assert m.pending_required_regressions(ev) == []

    expired = dict(
        active,
        id=904,
        created_at=(datetime.now(timezone.utc) - m.MAX_NATIVE_RUN_AGE).isoformat(),
    )
    ev["check_runs"].append(expired)
    assert m.pending_required_regressions(ev) == []


@pytest.mark.parametrize("pr_changes", [
    {"head_sha": OLD},
    {"state": "closed"},
    {"merged": True},
    {"base_ref": "release"},
    {"head_repo": "fork/WOW-Dashboard"},
])
def test_pending_required_regression_never_defers_stale_or_closed_pr(pr_changes):
    ev = evidence()
    active = run_(m.REQUIRED_REGRESSION_CHECKS[0], app=GHA, rid=900)
    active.update(status="queued", conclusion=None)
    ev["check_runs"].append(active)
    ev["pr"].update(pr_changes)

    assert m.pending_required_regressions(ev) == []


def _native_verify_run(**overrides):
    run = {
        "id": 7001,
        "run_attempt": 1,
        "workflow_id": m.NATIVE_REGRESSION_WORKFLOW_ID,
        "path": m.NATIVE_REGRESSION_WORKFLOW_PATH,
        "event": "pull_request",
        "head_sha": HEAD,
        "head_branch": "fix/issue-1830",
        "status": "in_progress",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
        "pull_requests": [{
            "number": 7,
            "head": {"sha": HEAD, "ref": "fix/issue-1830"},
        }],
        "jobs": [{"name": "WOW required-three regression", "status": "queued"}],
    }
    run.update(overrides)
    return run


def test_missing_aggregate_defers_only_for_authentic_active_native_job():
    ev = evidence()
    ev["check_runs"] = [
        check for check in ev["check_runs"]
        if check["name"] != m.REQUIRED_REGRESSION_CHECKS[2]
    ]
    ev["native_workflow_runs"] = [_native_verify_run()]

    assert m.pending_required_regressions(ev) == ["WOW required-three regression"]


@pytest.mark.parametrize("run_changes", [
    {"workflow_id": 1},
    {"path": ".github/workflows/other.yml"},
    {"event": "workflow_dispatch"},
    {"head_sha": OLD},
    {"head_branch": "stale-branch"},
    {"repository": {"full_name": "fork/WOW-Dashboard"}},
    {"head_repository": {"full_name": "fork/WOW-Dashboard"}},
    {"pull_requests": [{"number": 8, "head": {"sha": HEAD, "ref": "fix/issue-1830"}}]},
    {"status": "completed"},
    {"created_at": "invalid"},
    {"jobs": [{"name": "unrelated job", "status": "in_progress"}]},
])
def test_missing_aggregate_does_not_defer_for_untrusted_or_unrelated_native_run(run_changes):
    ev = evidence()
    ev["check_runs"] = [
        check for check in ev["check_runs"]
        if check["name"] != m.REQUIRED_REGRESSION_CHECKS[2]
    ]
    ev["native_workflow_runs"] = [_native_verify_run(**run_changes)]

    assert m.pending_required_regressions(ev) == []


def test_missing_aggregate_does_not_defer_after_native_run_exceeds_pending_bound():
    ev = evidence()
    ev["check_runs"] = [
        check for check in ev["check_runs"]
        if check["name"] != m.REQUIRED_REGRESSION_CHECKS[2]
    ]
    expired = (datetime.now(timezone.utc) - m.MAX_NATIVE_RUN_AGE).isoformat()
    ev["native_workflow_runs"] = [_native_verify_run(created_at=expired)]

    assert m.pending_required_regressions(ev) == []


def test_qa_check_published_by_another_app_is_a_foreign_source():
    findings = m.release_findings(with_qa(evidence(), app=REL_APP), qa_app_id=QA_APP, release_app_id=REL_APP)
    assert "RELEASE_QA_FOREIGN_SOURCE" in findings and "RELEASE_QA_MISSING" in findings


def test_failed_qa_blocks_release():
    assert "RELEASE_QA_FAILED" in m.release_findings(with_qa(evidence(), "failure"), qa_app_id=QA_APP,
                                                     release_app_id=REL_APP)


@pytest.mark.parametrize("qa_id, rel_id, code", [
    (QA_APP, QA_APP, "RELEASE_IDENTITY_NOT_DISTINCT"),
    ("", REL_APP, "RELEASE_APP_IDENTITY_UNBOUND"),
    (QA_APP, "x", "RELEASE_APP_IDENTITY_UNBOUND"),
])
def test_release_identity_must_be_bound_and_distinct(qa_id, rel_id, code):
    assert code in m.release_findings(with_qa(evidence()), qa_app_id=qa_id, release_app_id=rel_id)


def test_release_independently_rederives_qa_conditions():
    ev = with_qa(evidence(head_sha=OLD))
    assert "RELEASE_QA_HEAD_STALE" in m.release_findings(ev, qa_app_id=QA_APP, release_app_id=REL_APP)


def test_decision_is_published_at_the_evaluated_sha_and_never_authorizes():
    ev = evidence(head_sha=OLD)   # PR moved on after the trigger
    out = m.decision("qa", m.qa_findings(ev), ev)
    assert out["head_sha"] == HEAD and out["conclusion"] == "failure"
    assert out["merge_or_release_authorized"] is False and out["can_execute"] is False
    ok = m.decision("release", [], evidence())
    assert ok["check_name"] == m.RELEASE_CHECK and ok["conclusion"] == "success" and "can_execute=false" in ok["summary"]


# --- target resolution and evidence gathering ---------------------------------------


class Fake:
    def __init__(self, routes):
        self.routes = routes

    def __call__(self, path):
        return self.routes.get(path, (404, {"message": "Not Found"}))


def test_workflow_run_targets_use_each_prs_own_head_not_the_base():
    live = {
        "number": 7,
        "head": {"sha": HEAD, "ref": "fix/issue-1830", "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }
    routes = {f"repos/{REPO}/pulls/7": (200, live)}
    prs = [
        {"number": 7, "head": {"sha": HEAD, "repo": {"id": 1, "name": "WOW-Dashboard",
             "url": "https://api.github.com/repos/" + REPO}},
         "base": {"ref": "main", "repo": {"id": 1, "name": "WOW-Dashboard",
             "url": "https://api.github.com/repos/" + REPO}}},
        {"number": 8, "head": {"sha": OLD}, "base": {"ref": "release"}},
        {"number": 9, "head": {"sha": "zz"}, "base": {"ref": "main"}},
    ]
    out = m.targets(REPO, workflow_run_prs=prs, fetch=Fake(routes))
    assert out["targets"] == [{"pr": 7, "head": HEAD, "head_ref": "fix/issue-1830"}]
    assert out["errors"] == ["TARGET_HEAD_INVALID:9"]


def test_workflow_run_target_holds_when_live_pr_is_stale_or_foreign():
    event_pr = [{"number": 7, "head": {"sha": HEAD}, "base": {"ref": "main"}}]
    stale = {"number": 7, "head": {"sha": OLD, "repo": {"full_name": REPO}},
             "base": {"ref": "main", "repo": {"full_name": REPO}}}
    assert m.targets(
        REPO, workflow_run_prs=event_pr,
        fetch=Fake({f"repos/{REPO}/pulls/7": (200, stale)}),
    )["errors"] == ["TARGET_PR_IDENTITY_MISMATCH:7"]
    foreign = {"number": 7, "head": {"sha": HEAD, "repo": {"full_name": "fork/WOW-Dashboard"}},
               "base": {"ref": "main", "repo": {"full_name": REPO}}}
    assert m.targets(
        REPO, workflow_run_prs=event_pr,
        fetch=Fake({f"repos/{REPO}/pulls/7": (200, foreign)}),
    )["errors"] == ["TARGET_PR_IDENTITY_MISMATCH:7"]


def test_dispatch_target_is_validated_and_resolved():
    assert m.targets(REPO, pr="7;rm", fetch=Fake({}))["errors"] == ["TARGET_PR_INVALID"]
    live = {"number": 7, "head": {"sha": HEAD, "ref": "fix/issue-1830", "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"full_name": REPO}}}
    out = m.targets(REPO, pr="7", fetch=Fake({f"repos/{REPO}/pulls/7": (200, live)}))
    assert out == {"targets": [{"pr": 7, "head": HEAD, "head_ref": "fix/issue-1830"}], "errors": []}
    assert m.targets(REPO, pr="7", fetch=Fake({}))["errors"] == ["TARGET_PR_UNREADABLE:404"]


def test_sweep_skips_drafts_and_forks_and_is_bounded():
    pulls = [{"number": n, "draft": n == 2, "head": {"sha": HEAD, "repo": {"full_name": "x/fork" if n == 3 else REPO}}}
             for n in range(1, 40)]
    out = m.targets(REPO, fetch=Fake({f"repos/{REPO}/pulls?state=open&base=main&per_page=100": (200, pulls)}))
    numbers = [t["pr"] for t in out["targets"]]
    assert 2 not in numbers and 3 not in numbers and len(numbers) == m.MAX_TARGETS


def _gather_routes(files=None, runs=None, changed=None):
    files = files if files is not None else [{"filename": "artifacts/x.py"}]
    runs = runs if runs is not None else [{"id": 1, "name": "n", "head_sha": HEAD, "status": "completed",
                                          "conclusion": "success", "app": {"id": GHA}}]
    return {
        f"repos/{REPO}": (200, {"owner": {"login": OWNER}}),
        f"repos/{REPO}/pulls/7": (200, {"number": 7, "body": "Incident: #2136", "state": "open", "draft": False, "merged": False, "head": {"sha": HEAD, "ref": "fix/issue-1830", "repo": {"full_name": REPO}},
                                        "base": {"ref": "main", "repo": {"full_name": REPO}}, "user": {"id": 9001, "login": "bot"},
                                        "changed_files": changed if changed is not None else len(files)}),
        f"repos/{REPO}/pulls/7/files?per_page=100&page=1": (200, files),
        f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1": (200, {"total_count": len(runs), "check_runs": runs}),
        f"repos/{REPO}/pulls/7/reviews?per_page=100&page=1": (200, []),
        f"repos/{REPO}/actions/runs?head_sha={HEAD}&event=pull_request&per_page=100&page=1":
            (200, {"total_count": 0, "workflow_runs": []}),
    }


def _source_review_resolver(**kwargs):
    return source_review_evidence(
        kwargs["expected_file_paths"],
        author="bot",
        pr_number=kwargs["pr_number"],
    )


def test_gather_collects_exact_head_evidence():
    ev = m.gather(
        REPO,
        7,
        HEAD,
        fetch=Fake(_gather_routes()),
        source_review_resolver=_source_review_resolver,
    )
    assert ev["evidence_errors"] == [] and ev["files"] == ["artifacts/x.py"]
    assert ev["check_runs"][0]["app_id"] == GHA and ev["owner_login"] == OWNER


@pytest.mark.parametrize("mutate, error", [
    (lambda r: r.pop(f"repos/{REPO}/pulls/7"), "PULL_REQUEST_UNREADABLE:404"),
    (lambda r: r.update({f"repos/{REPO}/pulls/7/files?per_page=100&page=1": (403, {})}), "FILES_UNREADABLE:403"),
    (lambda r: r.update({f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1": (200, {"total_count": 5, "check_runs": []})}),
     "CHECK_RUNS_TRUNCATED"),
    (lambda r: r.update({f"repos/{REPO}": (500, None)}), "REPOSITORY_UNREADABLE:500"),
])
def test_gather_types_unreadable_and_truncated_inputs(mutate, error):
    routes = _gather_routes()
    mutate(routes)
    ev = m.gather(REPO, 7, HEAD, fetch=Fake(routes), source_review_resolver=_source_review_resolver)
    assert error in ev["evidence_errors"]


def test_gather_detects_file_list_shorter_than_changed_files():
    ev = m.gather(
        REPO,
        7,
        HEAD,
        fetch=Fake(_gather_routes(changed=3)),
        source_review_resolver=_source_review_resolver,
    )
    assert "FILES_TRUNCATED" in ev["evidence_errors"]


# --- workflows: structure -------------------------------------------------------------


@pytest.mark.parametrize("wf, mine, other", [
    (QA_WF, ("WOW_QA_APP_ID", "WOW_QA_APP_PRIVATE_KEY"), ("WOW_RELEASE_APP_PRIVATE_KEY",)),
    (REL_WF, ("WOW_RELEASE_APP_ID", "WOW_RELEASE_APP_PRIVATE_KEY"), ("WOW_QA_APP_PRIVATE_KEY",)),
])
def test_workflow_isolation_and_least_privilege(wf, mine, other):
    text = wf.read_text()
    for name in mine:
        assert name in text
    for name in other:
        assert name not in text, f"{wf.name} must never hold the other role's key"
    on = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert not re.search(r"^\s+pull_request(_target|_review)?\s*:", on, re.M), "never run PR-context triggers"
    perms = text.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
    assert "write" not in perms, "workflow token stays read-only"
    expected_checkout = "ref: ${{ github.sha }}"
    assert expected_checkout in text and "persist-credentials: false" in text
    assert "github.event.pull_request" not in text
    for use in re.findall(r"uses:\s*(\S+)", text):
        assert use.startswith("./") or re.search(r"@[0-9a-f]{40}$", use), f"unpinned action {use}"
    assert "permission-checks: write" in text
    for step in text.split("      - name: ")[1:]:
        if "run: |" in step:
            assert "${{" not in step.split("run: |", 1)[1], "no expression interpolation inside run scripts"
        if "steps.app_token.outputs.token" in step:
            assert step.startswith("Publish exact-head check"), "App token only reaches the publish step"
    # Protect the *new* exact-PR queue rather than require the obsolete global queue.
    concurrency = yaml.load(text, Loader=yaml.BaseLoader)["concurrency"]
    assert concurrency["group"] == (
        wf.stem + "-${{ inputs.pr_number || "
        "github.event.workflow_run.pull_requests[0].number || 'AUTO' }}"
    )
    assert concurrency["cancel-in-progress"] == "false"
    assert concurrency["queue"] == "max"


@pytest.mark.parametrize("wf, upstream", [
    (QA_WF, [
        "wow-verify", "wow-engine-verify", "wow-v17-rapid-repair",
        # Do not restore the governance completion event: QA -> certifier -> trusted gate -> QA.
        "wow-v17-change-impact-gate",
    ]),
    (REL_WF, ["wow-v17-independent-qa-check"]),
])
def test_workflow_keeps_event_and_manual_reentry_without_polling(wf, upstream):
    triggers = yaml.load(wf.read_text(), Loader=yaml.BaseLoader)["on"]

    assert set(triggers) == {"workflow_run", "workflow_dispatch"}
    assert triggers["workflow_run"]["types"] == ["completed"]
    assert triggers["workflow_run"]["workflows"] == upstream
    assert triggers["workflow_dispatch"]["inputs"]["pr_number"]["required"] == "true"
    if wf == QA_WF:
        workflow = yaml.load(wf.read_text(), Loader=yaml.BaseLoader)
        assert "WOW_QA_INDEPENDENT_REVIEW_ACTIVE != 'true'" in workflow["jobs"]["qa"]["if"]
    if wf == REL_WF:
        assert triggers["workflow_dispatch"]["inputs"]["qa_run_id"]["required"] == "true"
        assert triggers["workflow_dispatch"]["inputs"]["qa_run_attempt"]["required"] == "true"


def _workflow_on(path):
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    return loaded["on"] if "on" in loaded else loaded[True]


def test_prless_workflow_run_skips_qa_before_target_resolution():
    job_if = yaml.load(QA_WF.read_text(), Loader=yaml.BaseLoader)["jobs"]["qa"]["if"]
    assert "github.event_name != 'workflow_run' || github.event.workflow_run.pull_requests[0]" in job_if
    assert "WOW_QA_INDEPENDENT_REVIEW_ACTIVE != 'true'" in job_if
    script = _step_script(QA_WF, "Resolve one exact-head QA target")
    assert "QA_TARGETS_UNRESOLVED: workflow_run PR identity is missing" in script


def test_release_skips_policy_resolution_until_upstream_qa_is_applicable():
    workflow = yaml.load(REL_WF.read_text(), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["release"]
    assert "github.event.workflow_run.conclusion != 'skipped'" in job["if"]
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Classify upstream QA applicability"]["if"] == "success() && github.event_name == 'workflow_run'"
    assert steps["Decide at exact head (read-only)"]["if"] == (
        "success() && (github.event_name != 'workflow_run' || steps.upstream.outputs.applicable == 'true')"
    )
    assert steps["Publish exact-head check"]["if"] == "steps.decide.outputs.count == '1'"
    assert steps["Mint checks-only App token"]["if"] == "steps.decide.outputs.count == '1'"
    script = _step_script(REL_WF, "Classify upstream QA applicability")
    assert "resolve-qa-policy" not in script
    assert "check-runs" not in script
    assert "classify-upstream-qa" in script


def test_source_qa_release_certifier_dag_has_no_cycle():
    qa = _workflow_on(QA_WF)["workflow_run"]["workflows"]
    release = _workflow_on(REL_WF)["workflow_run"]["workflows"]
    certifier = _workflow_on(ROOT / ".github/workflows/wow-v17-existing-pr-governance-review.yml")
    gate = _workflow_on(ROOT / ".github/workflows/wow-v17-trusted-governance-gate.yml")
    assert release == ["wow-v17-independent-qa-check"]
    assert "wow-v17-release-authority-check" not in qa
    assert "wow-v17-existing-pr-governance-review" not in qa
    assert certifier["workflow_run"]["workflows"] == ["wow-v17-independent-qa-check"]
    assert gate["workflow_run"]["workflows"] == ["wow-v17-existing-pr-governance-review"]
    qa_if = yaml.load(QA_WF.read_text(), Loader=yaml.BaseLoader)["jobs"]["qa"]["if"]
    assert "wow-v17-trusted-governance-gate" in qa_if
    assert "WOW_QA_INDEPENDENT_REVIEW_ACTIVE != 'true'" in qa_if
    assert "github.event.workflow_run.pull_requests[0]" in qa_if


# Copied from GET /repos/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/40107281706.
# `name` is the workflow run-name, not the workflow's static name.
REAL_QA_RUN_40107281706 = {
    "id": 40107281706,
    "name": "wow-v17-independent-qa-check event=workflow_run pr=AUTO head=AUTO",
    "workflow_id": 380562324,
    "path": ".github/workflows/wow-v17-independent-qa-check.yml",
    "head_branch": "main",
    "head_sha": "00f3bc37aeed8007e062b993112221623b19acd3",
    "event": "workflow_run",
    "run_attempt": 1,
    "status": "completed",
    "conclusion": "failure",
    "repository": {"full_name": REPO},
    "head_repository": {"full_name": REPO},
}
# Copied from run 40104744926, the successful QA execution for PR 1977, attempt 2.
REAL_QA_RUN_40104744926 = {
    "id": 40104744926,
    "name": "wow-v17-independent-qa-check event=workflow_run pr=AUTO head=AUTO",
    "workflow_id": 380562324,
    "path": ".github/workflows/wow-v17-independent-qa-check.yml",
    "head_branch": "main",
    "head_sha": "1c4ef99ab3ea8cbd8c9a07cdd3e500257f055acc",
    "event": "workflow_run",
    "run_attempt": 2,
    "status": "completed",
    "conclusion": "success",
    "repository": {"full_name": REPO},
    "head_repository": {"full_name": REPO},
}


def _qa_run(**overrides):
    body = dict(REAL_QA_RUN_40107281706)
    body["conclusion"] = "success"
    body.update(overrides)
    return body


def _qa_jobs(conclusion="success", *, status="completed", extra=()):
    jobs = [{"name": "qa", "status": status, "conclusion": conclusion}, *extra]
    return {"total_count": len(jobs), "jobs": jobs}


def _classify_fetch(run, jobs=None, *, run_status=200, jobs_status=200):
    def fetch(path):
        if path.endswith("/jobs"):
            return jobs_status, jobs
        return run_status, run
    return fetch


def test_pull_request_associated_qa_run_is_applicable_before_release_policy():
    for conclusion in ("success", "failure"):
        classified = m.classify_upstream_qa(
            REPO, run_id=40107281706, run_attempt=1,
            fetch=_classify_fetch(_qa_run(conclusion=conclusion), _qa_jobs(conclusion)),
        )
        assert classified == {
            "disposition": "applicable", "reason": "RELEASE_UPSTREAM_QA_APPLICABLE",
        }
        assert "can_execute" not in classified


@pytest.mark.parametrize("run_over, jobs, reason", [
    ({"conclusion": "skipped"}, _qa_jobs("skipped"), "RELEASE_UPSTREAM_QA_SKIPPED"),
    ({}, _qa_jobs("skipped"), "RELEASE_UPSTREAM_QA_NO_TARGET"),
])
def test_prless_or_skipped_upstream_qa_is_neutral(run_over, jobs, reason):
    classified = m.classify_upstream_qa(
        REPO, run_id=40107281706, run_attempt=1,
        fetch=_classify_fetch(_qa_run(**run_over), jobs),
    )
    assert classified == {"disposition": "neutral", "reason": reason}


@pytest.mark.parametrize("run_over, jobs, jobs_body_status, code", [
    ({"head_repository": {"full_name": "other/WOW-Dashboard"}}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"repository": {"full_name": "other/WOW-Dashboard"}}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"path": ".github/workflows/other.yml"}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"workflow_id": 380562325}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"workflow_id": "380562324"}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check", "workflow_id": 1}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check", "path": ".github/workflows/other.yml"}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check", "head_sha": "not-a-sha"}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check", "run_attempt": 2}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check",
      "repository": {"full_name": "other/WOW-Dashboard"}}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check",
      "head_repository": {"full_name": "other/WOW-Dashboard"}}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check "}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check extra"}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check event=push pr=AUTO head=AUTO"}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "other-workflow"}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check event=workflow_run pr=AUTO head=AUTO extra"}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check event=workflow_dispatch pr=AUTO head=AUTO"}, None, 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"name": "wow-v17-independent-qa-check event=workflow_run pr=AUTO head=AUTO",
      "workflow_id": 1}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"head_sha": "not-a-sha"}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"head_sha": "A" * 40}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"head_branch": "feature"}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"run_attempt": 2}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"event": "schedule"}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"conclusion": "cancelled"}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({"id": 1}, None, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({}, _qa_jobs("success", extra=({"name": "qa", "status": "completed", "conclusion": "success"},)), 200,
     "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({}, {"total_count": 0, "jobs": []}, 200, "RELEASE_UPSTREAM_QA_UNTRUSTED"),
    ({}, _qa_jobs("cancelled"), 200, "RELEASE_UPSTREAM_QA_JOB_INCOMPLETE"),
    ({}, _qa_jobs("success", status="in_progress"), 200, "RELEASE_UPSTREAM_QA_JOB_INCOMPLETE"),
    ({}, {"jobs": [{"name": "qa", "status": "completed", "conclusion": "success"}]}, 200,
     "RELEASE_UPSTREAM_QA_UNREADABLE"),
    ({}, None, 500, "RELEASE_UPSTREAM_QA_UNREADABLE"),
])
def test_malformed_foreign_or_incomplete_upstream_qa_fails_closed(run_over, jobs, jobs_body_status, code):
    with pytest.raises(m.UpstreamQaError, match=f"^{code}$"):
        m.classify_upstream_qa(
            REPO, run_id=40107281706, run_attempt=1,
            fetch=_classify_fetch(_qa_run(**run_over), jobs, jobs_status=jobs_body_status, run_status=200),
        )


def test_unreadable_upstream_qa_run_fails_closed():
    with pytest.raises(m.UpstreamQaError, match="^RELEASE_UPSTREAM_QA_UNREADABLE$"):
        m.classify_upstream_qa(
            REPO, run_id=40107281706, run_attempt=1,
            fetch=_classify_fetch(None, None, run_status=404),
        )


def test_non_integer_upstream_identity_fails_closed():
    with pytest.raises(m.UpstreamQaError, match="^RELEASE_UPSTREAM_QA_UNTRUSTED$"):
        m.classify_upstream_qa(REPO, run_id=True, run_attempt=1, fetch=_classify_fetch(_qa_run(), _qa_jobs()))


def test_static_qa_run_name_is_applicable_with_pinned_workflow_identity():
    assert m.QA_WORKFLOW_ID == 380562324
    assert m.QA_WORKFLOW_PATH == ".github/workflows/wow-v17-independent-qa-check.yml"
    for conclusion in ("success", "failure"):
        run = _qa_run(
            name=m.QA_WORKFLOW_NAME,
            workflow_id=m.QA_WORKFLOW_ID,
            path=m.QA_WORKFLOW_PATH,
            conclusion=conclusion,
        )
        classified = m.classify_upstream_qa(
            REPO, run_id=40107281706, run_attempt=1,
            fetch=_classify_fetch(run, _qa_jobs(conclusion)),
        )
        assert classified == {
            "disposition": "applicable", "reason": "RELEASE_UPSTREAM_QA_APPLICABLE",
        }
        assert "can_execute" not in classified


def test_real_successful_qa_run_with_dynamic_name_is_applicable():
    classified = m.classify_upstream_qa(
        REPO, run_id=40104744926, run_attempt=2,
        fetch=_classify_fetch(dict(REAL_QA_RUN_40104744926), _qa_jobs("success")),
    )
    assert classified == {"disposition": "applicable", "reason": "RELEASE_UPSTREAM_QA_APPLICABLE"}
    assert "can_execute" not in classified


def test_real_failed_qa_run_is_not_rejected_for_its_dynamic_name():
    classified = m.classify_upstream_qa(
        REPO, run_id=40107281706, run_attempt=1,
        fetch=_classify_fetch(dict(REAL_QA_RUN_40107281706), _qa_jobs("failure")),
    )
    assert classified == {"disposition": "applicable", "reason": "RELEASE_UPSTREAM_QA_APPLICABLE"}


def test_real_qa_run_with_skipped_job_stays_neutral():
    classified = m.classify_upstream_qa(
        REPO, run_id=40107281706, run_attempt=1,
        fetch=_classify_fetch(dict(REAL_QA_RUN_40107281706, conclusion="success"), _qa_jobs("skipped")),
    )
    assert classified == {"disposition": "neutral", "reason": "RELEASE_UPSTREAM_QA_NO_TARGET"}


def test_dispatch_run_name_shape_is_trusted_only_with_the_workflow_id():
    dispatched = _qa_run(
        event="workflow_dispatch",
        name="wow-v17-independent-qa-check event=workflow_dispatch pr=1977 head=" + ("a" * 40),
    )
    classified = m.classify_upstream_qa(
        REPO, run_id=40107281706, run_attempt=1,
        fetch=_classify_fetch(dispatched, _qa_jobs("success")),
    )
    assert classified["disposition"] == "applicable"
    spoofed = dict(dispatched, workflow_id=1)
    with pytest.raises(m.UpstreamQaError, match="^RELEASE_UPSTREAM_QA_UNTRUSTED$"):
        m.classify_upstream_qa(
            REPO, run_id=40107281706, run_attempt=1,
            fetch=_classify_fetch(spoofed, _qa_jobs("success")),
        )


# --- workflows: execute the real Bash against a fake gh ------------------------------


def _step_script(wf, name):
    body = wf.read_text().split(f"      - name: {name}\n", 1)[1]
    lines = []
    for line in body.splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= 6:
            break
        lines.append(line)
    return dedent("\n".join(lines).split("        run: |\n", 1)[1]) + "\n"


FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_GH_LOG"]
if "--method" in args:
    payload = sys.stdin.read()
    with open(log, "a") as fh:
        fh.write(json.dumps({"args": args, "payload": json.loads(payload)}) + "\n")
    sys.exit(int(os.environ.get("FAKE_GH_POST_RC", "0")))
path = args[-1]
routes = json.load(open(os.environ["FAKE_GH_ROUTES"]))
status, body = routes.get(path, [404, {"message": "Not Found"}])
print(f"HTTP/2.0 {status} OK\r\n\r\n" + json.dumps(body))
'''


@pytest.fixture
def fake_gh(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "sitecustomize.py").write_text(dedent("""
        import importlib.util
        import json
        import os
        _module_from_spec = importlib.util.module_from_spec
        def _patched_module_from_spec(spec):
            module = _module_from_spec(spec)
            if spec.name == "_independent_source_review_receipt":
                loader = spec.loader
                original_exec = loader.exec_module
                def _exec_module(target):
                    original_exec(target)
                    def _resolve(**kwargs):
                        if os.environ.get("FAKE_SOURCE_REVIEW_MODE") == "missing":
                            raise target.SourceReviewError("SOURCE_REVIEW_ARTIFACT_DUPLICATE_OR_MISSING")
                        return json.loads(os.environ["FAKE_SOURCE_REVIEW"])
                    target.resolve_current_source_review = _resolve
                    def _resolve_policy(**kwargs):
                        if os.environ.get("FAKE_QA_POLICY_MODE") == "missing":
                            raise target.SourceReviewError("QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING")
                        return json.loads(os.environ["FAKE_QA_POLICY"])
                    target.resolve_qa_review_policy = _resolve_policy
                loader.exec_module = _exec_module
            return module
        importlib.util.module_from_spec = _patched_module_from_spec
    """))
    return tmp_path


def _routes_for_pr(runs):
    routes = _gather_routes(runs=runs)
    routes[f"repos/{REPO}/pulls/7"][1]["user"] = {"id": 9001, "login": "wow-engineering[bot]"}
    return {k: list(v) for k, v in routes.items()}


def _run_decide(tmp, wf, routes, env_extra):
    (tmp / "routes.json").write_text(json.dumps(routes))
    runner = tmp / "runner"
    runner.mkdir(exist_ok=True)
    source = source_review_evidence(["artifacts/x.py"], author="wow-engineering[bot]")
    (runner / "wow-v17-independent-source-review.json").write_text(json.dumps(source["artifact"]))
    (runner / "source-review-run.json").write_text(json.dumps(source["run"]))
    qa_policy = {
        "artifact_id": 88,
        "policy": {
            "mode": "independent",
            "target": {"pr_number": 7, "head_sha": HEAD, "head_ref": "fix/issue-1830"},
            "canary_scope": None,
            "source": {"run_id": "12345", "run_attempt": 1},
        },
        "run": {"id": 12345, "run_attempt": 1, "conclusion": "success"},
    }
    review_active = env_extra.get("REVIEW_ACTIVE", "true") == "true"
    review_mode = env_extra.get("REVIEW_MODE", "independent" if review_active else "legacy")
    qa_policy["policy"].update(mode=review_mode)
    qa_policy["policy"]["canary_scope"] = (
        {"pr_number": 7, "head_sha": HEAD} if review_mode == "canary" else None
    )
    (runner / "wow-v17-qa-review-policy.json").write_text(json.dumps(qa_policy["policy"]))
    env = dict(os.environ, PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", FAKE_GH_ROUTES=str(tmp / "routes.json"),
               FAKE_GH_LOG=str(tmp / "gh.log"), GITHUB_REPOSITORY=REPO, RUNNER_TEMP=str(runner),
               GITHUB_OUTPUT=str(tmp / "out.txt"), GITHUB_STEP_SUMMARY=str(tmp / "summary.md"), GH_TOKEN="read-only",
               PR_NUMBER="7", HEAD_SHA=HEAD, REVIEW_ACTIVE="true", REVIEW_ACTIVATION="false",
               SOURCE_REVIEW_ARTIFACT_ID="99", SOURCE_QA_RUN_ID="12345", SOURCE_QA_RUN_ATTEMPT="1",
               PYTHONPATH=f"{tmp}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
               FAKE_SOURCE_REVIEW=json.dumps(source), FAKE_QA_POLICY=json.dumps(qa_policy))
    env.update(env_extra)
    proc = subprocess.run(["bash", "-c", _step_script(wf, "Decide at exact head (read-only)")],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    return proc, runner, env


def _evidence_runs(extra=()):
    runs = [{"id": i, "name": n, "head_sha": HEAD, "status": "completed", "conclusion": "success",
             "app": {"id": GHA}, "completed_at": "2026-10-09T03:00:00Z"}
            for i, n in enumerate(m.QA_EVIDENCE_CHECKS)]
    return runs + list(extra)


@pytest.mark.parametrize("event", ["workflow_run", "workflow_dispatch"])
def test_qa_workflow_end_to_end_publishes_success_with_its_own_token(fake_gh, event):
    prs = json.dumps([{"number": 7, "head": {"sha": HEAD}, "base": {"ref": "main"}}])
    proc, runner, env = _run_decide(fake_gh, QA_WF, _routes_for_pr(_evidence_runs()),
                                    {"EVENT": event, "WR_PRS": prs if event == "workflow_run" else "null",
                                     "INPUT_PR": "7" if event == "workflow_dispatch" else ""})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "success" and decision["check_name"] == m.QA_CHECK
    env.update({
        "PR_NUMBER": "7",
        "HEAD_SHA": HEAD,
        "POLICY_OUTCOME": "success",
        "POLICY_RECEIPT_OUTCOME": "success",
        "POLICY_ARTIFACT_OUTCOME": "success",
        "CAPTURE_OUTCOME": "success",
        "REVIEW_OUTCOME": "success",
        "RECHECK_OUTCOME": "success",
        "SOURCE_REVIEW_OUTCOME": "success",
        "SOURCE_REVIEW_ARTIFACT_OUTCOME": "success",
        "DECIDE_OUTCOME": "success",
    })
    terminal = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Preserve terminal QA decision for every resolved target")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert terminal.returncode == 0, terminal.stdout + terminal.stderr
    env["GH_TOKEN"] = "qa-app-token"
    pub = subprocess.run(["bash", "-c", _step_script(QA_WF, "Publish exact-head check")], cwd=ROOT, env=env,
                         capture_output=True, text=True)
    assert pub.returncode == 0, pub.stdout + pub.stderr
    posted = [json.loads(line) for line in (fake_gh / "gh.log").read_text().splitlines()]
    assert len(posted) == 1
    assert posted[0]["args"][:3] == ["api", "--method", "POST"] and posted[0]["args"][3] == f"repos/{REPO}/check-runs"
    assert posted[0]["payload"] == {"name": m.QA_CHECK, "head_sha": HEAD, "status": "completed", "conclusion": "success",
                                    "output": {"title": decision["title"], "summary": decision["summary"]}}


def test_qa_workflow_publishes_failure_when_evidence_is_missing(fake_gh):
    proc, runner, _ = _run_decide(
        fake_gh,
        QA_WF,
        _routes_for_pr(_evidence_runs()),
        {
            "EVENT": "workflow_dispatch",
            "INPUT_PR": "7",
            "WR_PRS": "null",
            "REVIEW_ACTIVE": "false",
        },
    )
    assert proc.returncode == 0, proc.stderr
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure"
    assert f"QA_EVIDENCE_MISSING:{m.TRUSTED_GOVERNANCE_CHECK}" in decision["findings"]


def test_invalid_dispatch_target_fails_without_publishing(fake_gh):
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT,
        env=dict(os.environ, PATH=f"{fake_gh / 'bin'}:{os.environ['PATH']}",
                 FAKE_GH_ROUTES=str(fake_gh / "routes.json"),
                 GITHUB_REPOSITORY=REPO, GITHUB_OUTPUT=str(fake_gh / "out"),
                 GH_TOKEN="read-only", EVENT="workflow_dispatch", INPUT_PR="7;id", WR_PRS="null"),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "::error::QA_TARGETS_UNRESOLVED" in proc.stdout


def _live_dispatch_pr(sha=HEAD, repo=REPO, number=7):
    return {
        "number": number,
        "head": {"sha": sha, "ref": "fix/issue-1830", "repo": {"full_name": repo}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }


def _qa_target_output_enables_publish(pr_number):
    """Publish uses always(), so a failed resolver still reaches that if.

    An empty pr_number leaves the condition false, so no App token is minted
    and no check run is posted.
    """
    workflow = yaml.load(QA_WF.read_text(), Loader=yaml.BaseLoader)
    steps = {step["name"]: step for step in workflow["jobs"]["qa"]["steps"]}
    assert steps["Mint checks-only App token"]["if"] == "steps.target.outputs.pr_number != ''"
    assert steps["Publish exact-head check"]["if"] == "always() && steps.target.outputs.pr_number != ''"
    return pr_number != ""


def _run_qa_target(fake_gh, *, input_pr="7", input_head="", wr_prs="null", live=None):
    pr = live if live is not None else _live_dispatch_pr()
    routes = {f"repos/{REPO}/pulls/7": [200, pr]}
    (fake_gh / "routes.json").write_text(json.dumps(routes))
    out = fake_gh / "resolved.txt"
    env = dict(
        os.environ,
        PATH=f"{fake_gh / 'bin'}:{os.environ['PATH']}",
        FAKE_GH_ROUTES=str(fake_gh / "routes.json"),
        FAKE_GH_LOG=str(fake_gh / "gh.log"),
        GITHUB_REPOSITORY=REPO,
        GITHUB_OUTPUT=str(out),
        GH_TOKEN="read-only",
        EVENT="workflow_dispatch",
        INPUT_PR=input_pr,
        INPUT_HEAD_SHA=input_head,
        WR_PRS=wr_prs,
    )
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    return proc, out.read_text() if out.exists() else ""


def test_dispatch_matching_head_resolves_that_pr_and_posts_no_check(fake_gh):
    other_pr = json.dumps([{"number": 8, "head": {"sha": OLD}, "base": {"ref": "main"}}])
    proc, outputs = _run_qa_target(fake_gh, input_head=HEAD, wr_prs=other_pr)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs.splitlines() == [
        "pr_number=7",
        f"head_sha={HEAD}",
        "head_ref=fix/issue-1830",
    ]
    assert _qa_target_output_enables_publish("7") is True
    assert not (fake_gh / "gh.log").exists()


@pytest.mark.parametrize("head, code", [
    ("not-a-sha", "QA_INPUT_HEAD_SHA_INVALID"),
    ("A" * 40, "QA_INPUT_HEAD_SHA_INVALID"),
    (OLD, "QA_INPUT_HEAD_SHA_STALE"),
    ("c" * 40, "QA_INPUT_HEAD_SHA_STALE"),
])
def test_dispatch_stale_or_malformed_head_publishes_no_qa_check(fake_gh, head, code):
    proc, outputs = _run_qa_target(fake_gh, input_head=head)
    assert proc.returncode != 0
    assert f"::error::{code}" in proc.stdout
    assert "pr_number=" not in outputs
    assert _qa_target_output_enables_publish("") is False
    assert not (fake_gh / "gh.log").exists()


def test_dispatch_foreign_repository_head_publishes_no_qa_check(fake_gh):
    proc, outputs = _run_qa_target(
        fake_gh, input_head=HEAD, live=_live_dispatch_pr(repo="fork/WOW-Dashboard"),
    )
    assert proc.returncode != 0
    assert "::error::QA_INPUT_HEAD_SHA_STALE" in proc.stdout
    assert "pr_number=" not in outputs
    assert not (fake_gh / "gh.log").exists()


def test_release_dispatch_matching_head_writes_one_decision(fake_gh):
    qa_run = {"id": 900, "name": m.QA_CHECK, "head_sha": HEAD, "status": "completed",
              "conclusion": "success", "app": {"id": int(QA_APP)},
              "completed_at": "2026-10-09T03:30:00Z"}
    proc, runner, _ = _run_decide(
        fake_gh, REL_WF, _routes_for_pr(_evidence_runs([qa_run])),
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "INPUT_HEAD_SHA": HEAD, "WR_PRS": "null",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "count=1" in (fake_gh / "out.txt").read_text()
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "success", decision.get("findings")
    assert decision["can_execute"] is False
    assert _release_count_enables_token_and_publish("1") is True


@pytest.mark.parametrize("head, code", [
    ("not-a-sha", "RELEASE_INPUT_HEAD_SHA_INVALID"),
    ("A" * 40, "RELEASE_INPUT_HEAD_SHA_INVALID"),
    (OLD, "RELEASE_INPUT_HEAD_SHA_STALE"),
    ("c" * 40, "RELEASE_INPUT_HEAD_SHA_STALE"),
])
def test_release_dispatch_stale_or_malformed_head_publishes_nothing(fake_gh, head, code):
    proc, runner, _ = _run_decide(
        fake_gh, REL_WF, _routes_for_pr(_evidence_runs()),
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "INPUT_HEAD_SHA": head, "WR_PRS": "null",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode != 0
    assert f"::error::{code}" in proc.stdout
    assert not (runner / "decisions" / "decision-7.json").exists()
    written = (fake_gh / "out.txt").read_text() if (fake_gh / "out.txt").exists() else ""
    assert "count=1" not in written
    assert _release_count_enables_token_and_publish("") is False
    assert not (fake_gh / "gh.log").exists()


def test_empty_workflow_run_pr_array_still_fails_closed_inside_the_resolver(fake_gh):
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT,
        env=dict(os.environ, PATH=f"{fake_gh / 'bin'}:{os.environ['PATH']}",
                 FAKE_GH_ROUTES=str(fake_gh / "routes.json"),
                 GITHUB_REPOSITORY=REPO, GITHUB_OUTPUT=str(fake_gh / "out"),
                 GH_TOKEN="read-only", EVENT="workflow_run", INPUT_PR="", WR_PRS="[]"),
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "QA_TARGETS_UNRESOLVED: workflow_run PR identity is missing" in proc.stdout
    assert not (fake_gh / "gh.log").exists()


def _run_classify(fake_gh, routes, run_id="40107281706"):
    (fake_gh / "routes.json").write_text(json.dumps(routes))
    runner = fake_gh / "runner"
    runner.mkdir()
    out = fake_gh / "out"
    env = dict(
        os.environ,
        PATH=f"{fake_gh / 'bin'}:{os.environ['PATH']}",
        FAKE_GH_ROUTES=str(fake_gh / "routes.json"),
        FAKE_GH_LOG=str(fake_gh / "gh.log"),
        GITHUB_REPOSITORY=REPO,
        GITHUB_OUTPUT=str(out),
        GITHUB_STEP_SUMMARY=str(fake_gh / "summary.md"),
        GH_TOKEN="read-only",
        SOURCE_QA_RUN_ID=run_id,
        SOURCE_QA_RUN_ATTEMPT="1",
        RUNNER_TEMP=str(runner),
    )
    proc = subprocess.run(
        ["bash", "-c", _step_script(REL_WF, "Classify upstream QA applicability")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    return proc, out.read_text() if out.exists() else ""


def test_release_classify_step_treats_skipped_qa_job_as_neutral(fake_gh):
    run_id = "40107281706"
    routes = {
        f"repos/{REPO}/actions/runs/{run_id}": [200, _qa_run(id=int(run_id))],
        f"repos/{REPO}/actions/runs/{run_id}/attempts/1/jobs": [200, _qa_jobs("skipped")],
    }
    proc, outputs = _run_classify(fake_gh, routes, run_id)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "applicable=false" in outputs
    assert "reason=RELEASE_UPSTREAM_QA_NO_TARGET" in outputs
    assert "no check published" in proc.stdout
    assert not (fake_gh / "gh.log").exists()


def _release_count_enables_token_and_publish(count):
    """A skipped Decide step leaves count as GitHub's empty string.

    Empty != '0' is true, which would mint the Release App token and run the
    publisher with no decision file. Only count == '1' may continue.
    """
    workflow = yaml.load(REL_WF.read_text(), Loader=yaml.BaseLoader)
    steps = {step["name"]: step for step in workflow["jobs"]["release"]["steps"]}
    for name in ("Mint checks-only App token", "Publish exact-head check"):
        assert steps[name]["if"] == "steps.decide.outputs.count == '1'"
    return count == "1"


def test_neutral_release_skips_token_and_publishes_nothing(fake_gh):
    run_id = "40107281706"
    routes = {
        f"repos/{REPO}/actions/runs/{run_id}": [200, _qa_run(id=int(run_id))],
        f"repos/{REPO}/actions/runs/{run_id}/attempts/1/jobs": [200, _qa_jobs("skipped")],
    }
    proc, outputs = _run_classify(fake_gh, routes, run_id)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "applicable=false" in outputs
    # The old inequality treats this empty output as permission to publish.
    assert ("" != "0") is True
    assert _release_count_enables_token_and_publish("") is False
    assert _release_count_enables_token_and_publish("0") is False
    assert _release_count_enables_token_and_publish("1") is True
    assert not (fake_gh / "gh.log").exists()


def test_release_classify_step_rejects_a_foreign_upstream_run(fake_gh):
    run_id = "40107281706"
    foreign = _qa_run(id=int(run_id), head_repository={"full_name": "other/WOW-Dashboard"})
    routes = {f"repos/{REPO}/actions/runs/{run_id}": [200, foreign]}
    proc, outputs = _run_classify(fake_gh, routes, run_id)
    assert proc.returncode != 0
    assert "RELEASE_UPSTREAM_QA_UNTRUSTED" in proc.stdout
    assert "applicable=true" not in outputs
    assert not (fake_gh / "gh.log").exists()


def test_provider_failure_publishes_exact_head_qa_app_failure_after_prior_pass(fake_gh):
    runner = fake_gh / "runner"
    runner.mkdir()
    (fake_gh / "routes.json").write_text(json.dumps(_routes_for_pr(_evidence_runs([
        run_(m.QA_CHECK, app=QA_APP, rid=900),
    ]))))
    env = dict(
        os.environ,
        PATH=f"{fake_gh / 'bin'}:{os.environ['PATH']}",
        FAKE_GH_ROUTES=str(fake_gh / "routes.json"),
        FAKE_GH_LOG=str(fake_gh / "gh.log"),
        GITHUB_REPOSITORY=REPO,
        RUNNER_TEMP=str(runner),
        GITHUB_OUTPUT=str(fake_gh / "out"),
        GITHUB_STEP_SUMMARY=str(fake_gh / "summary"),
        GH_TOKEN="qa-app-token",
        PR_NUMBER="7",
        HEAD_SHA=HEAD,
        POLICY_OUTCOME="success",
        POLICY_RECEIPT_OUTCOME="success",
        POLICY_ARTIFACT_OUTCOME="success",
        CAPTURE_OUTCOME="success",
        REVIEW_OUTCOME="failure",
        RECHECK_OUTCOME="skipped",
        SOURCE_REVIEW_OUTCOME="skipped",
        SOURCE_REVIEW_ARTIFACT_OUTCOME="skipped",
        DECIDE_OUTCOME="skipped",
    )
    terminal = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Preserve terminal QA decision for every resolved target")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert terminal.returncode == 0, terminal.stdout + terminal.stderr
    result = json.loads((runner / "wow-v17-qa-terminal-decision.json").read_text())
    assert result["head_sha"] == HEAD and result["conclusion"] == "failure"
    assert "QA_REVIEW_EXECUTION_FAILED:review" in result["findings"]

    publish = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Publish exact-head check")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert publish.returncode == 0, publish.stdout + publish.stderr
    posted = [json.loads(line) for line in (fake_gh / "gh.log").read_text().splitlines()]
    assert len(posted) == 1
    assert posted[0]["payload"]["head_sha"] == HEAD
    assert posted[0]["payload"]["conclusion"] == "failure"


def test_qa_workflow_resolves_minimal_workflow_run_repositories_against_live_pr(fake_gh):
    minimal_pr = {
        "number": 7,
        "head": {"sha": HEAD, "repo": {"id": 1, "name": "WOW-Dashboard",
                  "url": f"https://api.github.com/repos/{REPO}"}},
        "base": {"ref": "main", "repo": {"id": 1, "name": "WOW-Dashboard",
                  "url": f"https://api.github.com/repos/{REPO}"}},
    }
    live_pr = {
        "number": 7,
        "head": {"sha": HEAD, "ref": "fix/issue-1830", "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }
    routes = {f"repos/{REPO}/pulls/7": [200, live_pr]}
    (fake_gh / "routes.json").write_text(json.dumps(routes))
    env = dict(os.environ, PATH=f"{fake_gh / 'bin'}:{os.environ['PATH']}",
               FAKE_GH_ROUTES=str(fake_gh / "routes.json"), GITHUB_REPOSITORY=REPO,
               FAKE_GH_LOG=str(fake_gh / "gh.log"),
               GITHUB_OUTPUT=str(fake_gh / "resolved.txt"), GH_TOKEN="read-only",
               EVENT="workflow_run", INPUT_PR="", WR_PRS=json.dumps([minimal_pr]))

    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pr_number=7" in (fake_gh / "resolved.txt").read_text()
    assert f"head_sha={HEAD}" in (fake_gh / "resolved.txt").read_text()
    assert "head_ref=fix/issue-1830" in (fake_gh / "resolved.txt").read_text()
    routes[f"repos/{REPO}/pulls/7"][1]["head"]["sha"] = OLD
    (fake_gh / "routes.json").write_text(json.dumps(routes))
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Resolve one exact-head QA target")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "TARGET_PR_IDENTITY_MISMATCH:7" in proc.stdout


def test_release_workflow_end_to_end_requires_qa_from_qa_app(fake_gh):
    qa_run = {"id": 900, "name": m.QA_CHECK, "head_sha": HEAD, "status": "completed", "conclusion": "success",
              "app": {"id": int(QA_APP)}, "completed_at": "2026-10-09T03:30:00Z"}
    routes = _routes_for_pr(_evidence_runs([qa_run]))
    routes[f"repos/{REPO}/pulls?state=open&base=main&per_page=100"] = [200, [
        {"number": 7, "draft": False, "head": {"sha": HEAD, "repo": {"full_name": REPO}}}]]
    proc, runner, _ = _run_decide(fake_gh, REL_WF, routes, {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "WR_PRS": "null",
                                                           "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((runner / "decisions" / "decision-7.json").read_text())["conclusion"] == "success"
    # Same evidence, but the QA check came from the Release App itself: must fail.
    routes[f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1"][1]["check_runs"][-1]["app"]["id"] = int(REL_APP)
    proc, runner, _ = _run_decide(fake_gh, REL_WF, routes, {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "WR_PRS": "null",
                                                           "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP})
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure" and "RELEASE_QA_FOREIGN_SOURCE" in decision["findings"]


def test_release_defers_without_publishing_while_exact_head_regression_is_running_then_rechecks(fake_gh):
    qa_run = {"id": 900, "name": m.QA_CHECK, "head_sha": HEAD, "status": "completed",
              "conclusion": "success", "app": {"id": int(QA_APP)},
              "completed_at": "2026-10-09T03:30:00Z"}
    routes = _routes_for_pr(_evidence_runs([qa_run]))
    checks = routes[f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1"][1]["check_runs"]
    routes[f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1"][1]["check_runs"] = [
        check for check in checks
        if check["name"] != m.REQUIRED_REGRESSION_CHECKS[2]
    ]
    routes[f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1"][1]["total_count"] -= 1
    native_run = _native_verify_run()
    native_run.update({
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
        "pull_requests": [{"number": 7, "head": {"sha": HEAD, "ref": "fix/issue-1830"}}],
    })
    # The Actions API run record carries provenance; its separate jobs endpoint
    # proves the exact required aggregate is queued before GitHub creates a check run.
    del native_run["jobs"]
    routes[f"repos/{REPO}/actions/runs?head_sha={HEAD}&event=pull_request&per_page=100&page=1"] = [
        200, {"total_count": 1, "workflow_runs": [native_run]},
    ]
    routes[f"repos/{REPO}/actions/runs/{native_run['id']}/attempts/1/jobs?per_page=100&page=1"] = [
        200, {"total_count": 1, "jobs": [{"name": "WOW required-three regression", "status": "queued"}]},
    ]

    proc, runner, env = _run_decide(
        fake_gh, REL_WF, routes,
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "WR_PRS": "null",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "count=0" in Path(env["GITHUB_OUTPUT"]).read_text()
    assert "RELEASE_QA_EVIDENCE_PENDING" in proc.stdout
    assert not list((runner / "decisions").glob("decision-*.json"))
    assert _release_count_enables_token_and_publish("0") is False
    assert not (fake_gh / "gh.log").exists()

    aggregate = run_(m.REQUIRED_REGRESSION_CHECKS[2], app=GHA, rid=903)
    routes = _routes_for_pr(_evidence_runs([qa_run, aggregate]))
    proc, runner, env = _run_decide(
        fake_gh, REL_WF, routes,
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "WR_PRS": "null",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "count=1" in Path(env["GITHUB_OUTPUT"]).read_text()
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "success"
    assert decision["head_sha"] == HEAD and decision["can_execute"] is False
    env["GH_TOKEN"] = "release-app-token"
    published = subprocess.run(
        ["bash", "-c", _step_script(REL_WF, "Publish exact-head check")],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert published.returncode == 0, published.stdout + published.stderr
    posted = [json.loads(line) for line in (fake_gh / "gh.log").read_text().splitlines()]
    assert len(posted) == 1
    assert posted[0]["payload"]["name"] == m.RELEASE_CHECK
    assert posted[0]["payload"]["head_sha"] == HEAD
    assert posted[0]["payload"]["conclusion"] == "success"


def test_missing_aggregate_without_matching_active_native_work_is_terminal_hold(fake_gh):
    qa_run = {"id": 900, "name": m.QA_CHECK, "head_sha": HEAD, "status": "completed",
              "conclusion": "success", "app": {"id": int(QA_APP)},
              "completed_at": "2026-10-09T03:30:00Z"}
    routes = _routes_for_pr(_evidence_runs([qa_run]))
    check_path = f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1"
    routes[check_path][1]["check_runs"] = [
        check for check in routes[check_path][1]["check_runs"]
        if check["name"] != m.REQUIRED_REGRESSION_CHECKS[2]
    ]
    routes[check_path][1]["total_count"] -= 1

    proc, runner, env = _run_decide(
        fake_gh, REL_WF, routes,
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "WR_PRS": "null",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "count=1" in Path(env["GITHUB_OUTPUT"]).read_text()
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure"
    assert f"RELEASE_QA_EVIDENCE_MISSING:{m.REQUIRED_REGRESSION_CHECKS[2]}" in decision["findings"]
    assert "RELEASE_QA_EVIDENCE_PENDING" not in proc.stdout
    assert decision["can_execute"] is False


def test_missing_aggregate_with_completed_native_failure_is_terminal_hold(fake_gh):
    qa_run = {"id": 900, "name": m.QA_CHECK, "head_sha": HEAD, "status": "completed",
              "conclusion": "success", "app": {"id": int(QA_APP)},
              "completed_at": "2026-10-09T03:30:00Z"}
    routes = _routes_for_pr(_evidence_runs([qa_run]))
    check_path = f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1"
    routes[check_path][1]["check_runs"] = [
        check for check in routes[check_path][1]["check_runs"]
        if check["name"] != m.REQUIRED_REGRESSION_CHECKS[2]
    ]
    routes[check_path][1]["total_count"] -= 1
    failed = _native_verify_run(status="completed", conclusion="failure")
    routes[f"repos/{REPO}/actions/runs?head_sha={HEAD}&event=pull_request&per_page=100&page=1"] = [
        200, {"total_count": 1, "workflow_runs": [failed]},
    ]

    proc, runner, env = _run_decide(
        fake_gh, REL_WF, routes,
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "WR_PRS": "null",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "count=1" in Path(env["GITHUB_OUTPUT"]).read_text()
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure"
    assert f"RELEASE_QA_EVIDENCE_MISSING:{m.REQUIRED_REGRESSION_CHECKS[2]}" in decision["findings"]
    assert "RELEASE_QA_EVIDENCE_PENDING" not in proc.stdout
    assert decision["can_execute"] is False


def test_completed_required_regression_failure_is_terminal_not_pending(fake_gh):
    qa_run = {"id": 900, "name": m.QA_CHECK, "head_sha": HEAD, "status": "completed",
              "conclusion": "success", "app": {"id": int(QA_APP)},
              "completed_at": "2026-10-09T03:30:00Z"}
    failed = {"id": 901, "name": m.REQUIRED_REGRESSION_CHECKS[0], "head_sha": HEAD,
              "status": "completed", "conclusion": "failure", "app": {"id": GHA},
              "completed_at": "2026-10-09T03:40:00Z"}
    routes = _routes_for_pr(_evidence_runs([qa_run, failed]))

    proc, runner, env = _run_decide(
        fake_gh, REL_WF, routes,
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "WR_PRS": "null",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "count=1" in Path(env["GITHUB_OUTPUT"]).read_text()
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure"
    assert f"RELEASE_QA_EVIDENCE_FAILED:{m.REQUIRED_REGRESSION_CHECKS[0]}" in decision["findings"]
    assert decision["can_execute"] is False


def test_automatic_release_uses_only_the_authenticated_qa_run_target(fake_gh):
    routes = _routes_for_pr(_evidence_runs([run_(m.QA_CHECK, app=QA_APP)]))
    routes[f"repos/{REPO}/pulls?state=open&base=main&per_page=100"] = [200, [
        {"number": 7, "draft": False, "head": {"sha": HEAD, "repo": {"full_name": REPO}}},
        {"number": 8, "draft": False, "head": {"sha": OLD, "repo": {"full_name": REPO}}},
    ]]

    proc, runner, env = _run_decide(
        fake_gh,
        REL_WF,
        routes,
        {
            "EVENT": "workflow_run",
            "INPUT_PR": "",
            "WR_PRS": "null",
            "SOURCE_QA_RUN_ID": "12345",
            "SOURCE_QA_RUN_ATTEMPT": "1",
            "QA_APP_ID": QA_APP,
            "RELEASE_APP_ID": REL_APP,
        },
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "count=1" in Path(env["GITHUB_OUTPUT"]).read_text()
    decisions = sorted(path.name for path in (runner / "decisions").glob("decision-*.json"))
    assert decisions == ["decision-7.json"]
    assert json.loads((runner / "decisions" / "decision-7.json").read_text())["head_sha"] == HEAD
    assert json.loads((runner / "decisions" / "decision-7.json").read_text())["can_execute"] is False


def test_applicable_release_still_fails_closed_when_qa_policy_is_missing(fake_gh):
    proc, runner, _ = _run_decide(
        fake_gh, REL_WF, _routes_for_pr(_evidence_runs()),
        {"EVENT": "workflow_run", "FAKE_QA_POLICY_MODE": "missing",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode != 0
    assert "QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING" in proc.stderr
    assert list((runner / "decisions").glob("decision-*.json")) == []


def test_release_binds_mode_to_qa_run_across_activation_changes(fake_gh):
    legacy = {
        "artifact_id": 88,
        "policy": {
            "mode": "legacy",
            "target": {"pr_number": 7, "head_sha": HEAD, "head_ref": "fix/issue-1830"},
            "canary_scope": None,
            "source": {"run_id": "12345", "run_attempt": 1},
        },
        "run": {"id": 12345, "run_attempt": 1, "conclusion": "success"},
    }
    routes = _routes_for_pr(_evidence_runs([run_(m.QA_CHECK, app=QA_APP)]))
    routes[f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1"][1]["check_runs"].append(
        run_(m.TRUSTED_GOVERNANCE_CHECK, rid=90)
    )

    # OFF -> ON: a genuine legacy QA pass cannot satisfy the now-active policy.
    proc, runner, _ = _run_decide(
        fake_gh, REL_WF, routes,
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "REVIEW_ACTIVATION": "true",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP, "FAKE_QA_POLICY": json.dumps(legacy)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure"
    assert "RELEASE_QA_REVIEW_POLICY_LEGACY_AFTER_ACTIVATION" in decision["findings"]

    # ON -> OFF: a later flag change cannot downgrade the producing QA run.
    proc, runner, _ = _run_decide(
        fake_gh, REL_WF, routes,
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "REVIEW_ACTIVATION": "false",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP, "FAKE_SOURCE_REVIEW_MODE": "missing"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure"
    assert any("SOURCE_REVIEW" in finding for finding in decision["findings"])


@pytest.mark.parametrize("wf, code", [(QA_WF, "QA_APP_CREDENTIAL_MISSING"), (REL_WF, "RELEASE_APP_CREDENTIAL_MISSING")])
@pytest.mark.parametrize("app_id, present", [("", "true"), ("1002", "false"), ("12;x", "true")])
def test_missing_app_identity_fails_closed_and_publishes_nothing(wf, code, app_id, present):
    proc = subprocess.run(["bash", "-c", _step_script(wf, "Require this role's App identity")],
                          env=dict(os.environ, APP_ID=app_id, KEY_PRESENT=present), capture_output=True, text=True)
    assert proc.returncode != 0 and f"::error::{code}:" in proc.stdout


def test_activated_qa_review_requires_its_separate_provider_credential():
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Require isolated adversarial-review credential")],
        env=dict(os.environ, KEY_PRESENT="false"),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "::error::QA_REVIEW_PROVIDER_CREDENTIAL_MISSING" in proc.stdout


@pytest.mark.parametrize(
    ("global_active", "canary_requested", "canary_pr", "canary_sha", "event", "expected"),
    [
        ("false", "false", "", "", "workflow_dispatch", ("legacy", "false")),
        ("false", "true", "7", HEAD, "workflow_dispatch", ("canary", "true")),
        ("true", "false", "", "", "workflow_run", ("independent", "true")),
        ("true", "true", "7", HEAD, "workflow_dispatch", ("independent", "true")),
    ],
)
def test_qa_review_policy_selector_is_scoped_and_staged(
    tmp_path, global_active, canary_requested, canary_pr, canary_sha, event, expected,
):
    output = tmp_path / "outputs"
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Select exact-head review policy")],
        env=dict(
            os.environ,
            REVIEW_ACTIVATION=global_active,
            CANARY_REQUESTED=canary_requested,
            CANARY_PR=canary_pr,
            CANARY_SHA=canary_sha,
            EVENT=event,
            PR_NUMBER="7",
            HEAD_SHA=HEAD,
            GITHUB_OUTPUT=str(output),
        ),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert (values["mode"], values["review_active"]) == expected


@pytest.mark.parametrize(("event", "pr", "sha"), [
    ("workflow_run", "7", HEAD),
    ("workflow_dispatch", "8", HEAD),
    ("workflow_dispatch", "7", OLD),
    ("workflow_dispatch", "7", "not-a-sha"),
])
def test_qa_canary_policy_selector_rejects_unapproved_target(tmp_path, event, pr, sha):
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Select exact-head review policy")],
        env=dict(
            os.environ,
            REVIEW_ACTIVATION="false",
            CANARY_REQUESTED="true",
            CANARY_PR="7",
            CANARY_SHA=HEAD,
            EVENT=event,
            PR_NUMBER=pr,
            HEAD_SHA=sha,
            GITHUB_OUTPUT=str(tmp_path / "outputs"),
        ),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "::error::QA_REVIEW_CANARY_NOT_AUTHORIZED_FOR_EXACT_HEAD" in proc.stdout \
        or "::error::QA_REVIEW_CANARY_REQUIRES_DISPATCH" in proc.stdout


@pytest.mark.parametrize("activation", ["True", "enabled", "false "])
def test_qa_review_policy_selector_rejects_unknown_global_activation(tmp_path, activation):
    proc = subprocess.run(
        ["bash", "-c", _step_script(QA_WF, "Select exact-head review policy")],
        env=dict(
            os.environ,
            REVIEW_ACTIVATION=activation,
            CANARY_REQUESTED="false",
            EVENT="workflow_dispatch",
            PR_NUMBER="7",
            HEAD_SHA=HEAD,
            GITHUB_OUTPUT=str(tmp_path / "outputs"),
        ),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0 and "::error::QA_REVIEW_POLICY_INVALID" in proc.stdout


def test_release_review_policy_selector_rejects_unknown_activation(fake_gh):
    proc, runner, _ = _run_decide(
        fake_gh, REL_WF, _routes_for_pr(_evidence_runs()),
        {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "REVIEW_ACTIVATION": "enabled",
         "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP},
    )
    assert proc.returncode != 0
    assert "::error::QA_REVIEW_POLICY_INVALID" in proc.stdout
    assert not (runner / "decisions" / "decision-7.json").exists()


def test_publish_refuses_a_decision_that_claims_authority(fake_gh):
    decisions = fake_gh / "runner" / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "decision-7.json").write_text(json.dumps({**m.decision("qa", [], evidence()), "can_execute": True}))
    env = dict(os.environ, PATH=f"{fake_gh / 'bin'}:{os.environ['PATH']}", FAKE_GH_LOG=str(fake_gh / "gh.log"),
               GITHUB_REPOSITORY=REPO, RUNNER_TEMP=str(fake_gh / "runner"), GITHUB_STEP_SUMMARY=str(fake_gh / "s.md"))
    proc = subprocess.run(["bash", "-c", _step_script(QA_WF, "Publish exact-head check")], env=env,
                          capture_output=True, text=True)
    assert proc.returncode != 0 and "::error::QA_DECISION_INVARIANT_VIOLATED" in proc.stdout
    assert not (fake_gh / "gh.log").exists()


def test_publish_failure_is_typed(fake_gh):
    decisions = fake_gh / "runner" / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "decision-7.json").write_text(json.dumps(m.decision("release", [], evidence())))
    env = dict(os.environ, PATH=f"{fake_gh / 'bin'}:{os.environ['PATH']}", FAKE_GH_LOG=str(fake_gh / "gh.log"),
               FAKE_GH_POST_RC="1", GITHUB_REPOSITORY=REPO, RUNNER_TEMP=str(fake_gh / "runner"),
               GITHUB_STEP_SUMMARY=str(fake_gh / "s.md"))
    proc = subprocess.run(["bash", "-c", _step_script(REL_WF, "Publish exact-head check")], env=env,
                          capture_output=True, text=True)
    assert proc.returncode != 0 and "::error::RELEASE_CHECK_PUBLISH_FAILED" in proc.stdout


# --- registry -----------------------------------------------------------------------


def test_every_phase_c_code_is_registered():
    emitted = set(re.findall(r'"((?:QA|RELEASE|TARGET)_[A-Z_]+)', MODULE.read_text()))
    emitted |= set(re.findall(r'f"((?:QA|RELEASE|TARGET)_[A-Z_]+)[:"]', MODULE.read_text()))
    for wf in (QA_WF, REL_WF):
        text = wf.read_text()
        emitted |= set(re.findall(r"::error::((?:QA|RELEASE)_[A-Z_]+)", text))
        emitted |= set(re.findall(r"::notice::(RELEASE_QA_EVIDENCE_PENDING)", text))
    # RELEASE_ prefixes a re-derived QA finding; register the prefix rule, not each product.
    emitted = {c for c in emitted if c not in {"QA_EVIDENCE_CHECKS"}}
    registry = (ROOT / "artifacts/wow-engine/docs/failure_codes.md").read_text()
    section = registry.split("## Independent QA and Release Authority check codes", 1)[1].split("\n## ", 1)[0]
    registered = set(re.findall(r"^\| `([A-Z_]+)` \|", section, re.M))
    assert emitted == registered, (emitted - registered, registered - emitted)


# Credential isolation regression: GitHub Environments must be owner-configured
# to allow only main. The workflow binds the job to that environment; the
# environment's actual branch restriction is verified separately in GitHub.
def test_each_app_secret_is_isolated_by_main_only_environment():
    for workflow, environment, app_id, secret in (
        (QA_WF, "wow-qa", "WOW_QA_APP_ID", "WOW_QA_APP_PRIVATE_KEY"),
        (REL_WF, "wow-release", "WOW_RELEASE_APP_ID", "WOW_RELEASE_APP_PRIVATE_KEY"),
    ):
        source = workflow.read_text()
        assert ("    environment: " + environment) in source
        assert "ref: ${{ github.sha }}" in source
        assert "vars." + app_id in source
        assert "secrets." + secret in source
        other_secret = "WOW_RELEASE_APP_PRIVATE_KEY" if environment == "wow-qa" else "WOW_QA_APP_PRIVATE_KEY"
        assert "secrets." + other_secret not in source


@pytest.mark.parametrize("wf, var", [(QA_WF, "WOW_QA_APP_ID"), (REL_WF, "WOW_RELEASE_APP_ID")])
def test_unactivated_role_is_skipped_not_red(wf, var):
    job_if = re.search(r"^    if: (.+)$", wf.read_text(), re.M).group(1)
    assert f"vars.{var} != ''" in job_if and "github.repository ==" in job_if


# Adversarial governance: the *source* path of a rename is also a changed path.
# A rename out of a trust root cannot turn a protected mutation into an
# ordinary (autonomously approvable) PR.
@pytest.mark.parametrize("source", [
    ".github/workflows/wow-v17-independent-qa-check.yml",
    ".agents/skills/wow-engineering-independent-review-agent/SKILL.md",
    "artifacts/wow-engine/v17/independent_release_checks.py",
    "artifacts/wow-engine/v17/agent_identity_policy.py",
])
def test_rename_out_of_trust_root_holds_both_qa_and_release(source):
    renamed = [{"filename": "docs/relocated-unprotected-file.md",
                "status": "renamed", "previous_filename": source}]
    ev = m.gather(
        REPO,
        7,
        HEAD,
        fetch=Fake(_gather_routes(files=renamed, changed=1)),
        source_review_resolver=_source_review_resolver,
    )
    assert ev["evidence_errors"] == []
    assert ev["files"] == ["docs/relocated-unprotected-file.md", source]
    ev["check_runs"] = evidence()["check_runs"]
    assert m.qa_findings(evidence(files=("docs/relocated-unprotected-file.md",))) == []
    assert "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in m.qa_findings(ev)
    with_qa(ev)
    findings = m.release_findings(ev, qa_app_id=QA_APP, release_app_id=REL_APP)
    assert "RELEASE_QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in findings


def test_rename_origin_missing_or_invalid_fails_closed():
    for origin in (None, "", 24):
        files = [{"filename": "docs/unprotected.md", "status": "renamed",
                  "previous_filename": origin}]
        ev = m.gather(REPO, 7, HEAD, fetch=Fake(_gather_routes(files=files)))
        assert "FILES_RENAME_ORIGIN_MISSING" in ev["evidence_errors"]
        ev["check_runs"] = evidence()["check_runs"]
        assert "QA_EVIDENCE_INCOMPLETE:FILES_RENAME_ORIGIN_MISSING" in m.qa_findings(ev)
        with_qa(ev)
        assert "RELEASE_QA_EVIDENCE_INCOMPLETE:FILES_RENAME_ORIGIN_MISSING" in m.release_findings(
            ev, qa_app_id=QA_APP, release_app_id=REL_APP)


def test_rename_origin_does_not_hide_truncated_file_listing():
    files = [{"filename": "docs/outside.md", "status": "renamed",
              "previous_filename": ".github/workflows/secure.yml"}]
    ev = m.gather(REPO, 7, HEAD, fetch=Fake(_gather_routes(files=files, changed=2)))
    assert len(ev["files"]) == 2
    assert "FILES_TRUNCATED" in ev["evidence_errors"]
    assert "QA_EVIDENCE_INCOMPLETE:FILES_TRUNCATED" in m.qa_findings(ev)


def test_malformed_file_record_fails_closed():
    ev = m.gather(REPO, 7, HEAD, fetch=Fake(_gather_routes(files=[{"status": "modified"}])))
    assert "FILES_PATH_INVALID" in ev["evidence_errors"]
    assert "QA_EVIDENCE_INCOMPLETE:FILES_PATH_INVALID" in m.qa_findings(ev)


@pytest.mark.parametrize("body", [
    "Incident: #2136", "Incident: 2136",
    "Parent: #1688\nIncident: #2136",
    "Incident: `2136`", "Incident:\t#2136\t",
])
def test_required_qa_canonical_incident_accepts_single_exact_identity(body):
    assert m._canonical_incident_present(body) is True
    assert "QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS" not in m.qa_findings(
        evidence(body=body)
    )


@pytest.mark.parametrize("body", [
    None, "", "Parent: #2136",
    "Fixes #2136", "Discussion of Incident: #2136",
    "Incident: #0", "Incident: #abc",
    "Incident: #2136\nIncident: #2136",
    "Incident: #2136\nIncident: #2076",
    "Incident: #2136 extra",
    "  Incident: #2136", "\tIncident:\t#2136\t",
    {"body": "Incident: #2136"},
])
def test_missing_or_ambiguous_incident_blocks_required_qa_and_release(body):
    ev = evidence(body=body)
    assert m._canonical_incident_present(body) is False
    assert "QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS" in m.qa_findings(ev)
    # Release is an independent principal and recomputes QA predicates.
    findings = m.release_findings(ev, qa_app_id=QA_APP, release_app_id=REL_APP)
    assert "RELEASE_QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS" in findings


def test_real_gather_includes_pr_body_for_required_incident_gate():
    routes = _gather_routes()
    ev = m.gather(REPO, 7, HEAD, fetch=Fake(routes), source_review_resolver=_source_review_resolver)
    assert ev["pr"]["body"] == "Incident: #2136"
    routes[f"repos/{REPO}/pulls/7"][1]["body"] = "Parent: #1688"
    bad = m.gather(REPO, 7, HEAD, fetch=Fake(routes), source_review_resolver=_source_review_resolver)
    assert "QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS" in m.qa_findings(bad)
