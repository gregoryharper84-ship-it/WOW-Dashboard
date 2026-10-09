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
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "artifacts/wow-engine/v17/independent_release_checks.py"
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


def run_(name, conclusion="success", head=HEAD, app=GHA, at="2026-10-09T03:00:00Z", rid=1):
    return {"id": rid, "name": name, "head_sha": head, "status": "completed", "conclusion": conclusion,
            "app_id": app, "completed_at": at}


def evidence(files=("artifacts/wow-engine/v17/x.py",), author="wow-engineering[bot]", **over):
    ev = {
        "repository": REPO, "pr_number": 7, "expected_head_sha": HEAD, "evidence_errors": [],
        "owner_login": OWNER,
        "pr": {"state": "open", "draft": False, "merged": False, "head_sha": HEAD, "head_repo": REPO,
               "base_ref": "main", "author": author, "changed_files": len(files)},
        "files": list(files),
        "check_runs": [run_(n, rid=i) for i, n in enumerate((*m.QA_EVIDENCE_CHECKS, m.TRUSTED_GOVERNANCE_CHECK))],
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


@pytest.mark.parametrize("name", [*m.QA_EVIDENCE_CHECKS, m.TRUSTED_GOVERNANCE_CHECK])
def test_each_missing_or_failed_evidence_check_holds(name):
    ev = evidence()
    ev["check_runs"] = [r for r in ev["check_runs"] if r["name"] != name]
    assert f"QA_EVIDENCE_MISSING:{name}" in m.qa_findings(ev)
    ev["check_runs"].append(run_(name, "failure", rid=99))
    assert f"QA_EVIDENCE_FAILED:{name}" in m.qa_findings(ev)


def test_same_named_check_from_another_app_is_ignored():
    ev = evidence()
    name = m.TRUSTED_GOVERNANCE_CHECK
    ev["check_runs"] = [r for r in ev["check_runs"] if r["name"] != name] + [run_(name, app=424242, rid=50)]
    assert f"QA_EVIDENCE_MISSING:{name}" in m.qa_findings(ev)


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
    assert m.qa_findings(ev) == []  # trusted gate rejects trust roots by design; owner approval replaces it


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


def test_release_passes_only_on_qa_app_success_at_exact_head():
    assert m.release_findings(with_qa(evidence()), qa_app_id=QA_APP, release_app_id=REL_APP) == []


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
    prs = [{"number": 7, "head": {"sha": HEAD}, "base": {"ref": "main"}},
           {"number": 8, "head": {"sha": OLD}, "base": {"ref": "release"}},
           {"number": 9, "head": {"sha": "zz"}, "base": {"ref": "main"}}]
    out = m.targets(REPO, workflow_run_prs=prs, fetch=Fake({}))
    assert out["targets"] == [{"pr": 7, "head": HEAD}]
    assert out["errors"] == ["TARGET_HEAD_INVALID:9"]


def test_dispatch_target_is_validated_and_resolved():
    assert m.targets(REPO, pr="7;rm", fetch=Fake({}))["errors"] == ["TARGET_PR_INVALID"]
    out = m.targets(REPO, pr="7", fetch=Fake({f"repos/{REPO}/pulls/7": (200, {"head": {"sha": HEAD}})}))
    assert out == {"targets": [{"pr": 7, "head": HEAD}], "errors": []}
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
        f"repos/{REPO}/pulls/7": (200, {"state": "open", "draft": False, "head": {"sha": HEAD, "repo": {"full_name": REPO}},
                                        "base": {"ref": "main"}, "user": {"login": "bot"},
                                        "changed_files": changed if changed is not None else len(files)}),
        f"repos/{REPO}/pulls/7/files?per_page=100&page=1": (200, files),
        f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1": (200, {"total_count": len(runs), "check_runs": runs}),
        f"repos/{REPO}/pulls/7/reviews?per_page=100&page=1": (200, []),
    }


def test_gather_collects_exact_head_evidence():
    ev = m.gather(REPO, 7, HEAD, fetch=Fake(_gather_routes()))
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
    assert error in m.gather(REPO, 7, HEAD, fetch=Fake(routes))["evidence_errors"]


def test_gather_detects_file_list_shorter_than_changed_files():
    ev = m.gather(REPO, 7, HEAD, fetch=Fake(_gather_routes(changed=3)))
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
    assert "ref: main" in text and "persist-credentials: false" in text
    assert "github.event.pull_request" not in text and "head_ref" not in text
    for use in re.findall(r"uses:\s*(\S+)", text):
        assert re.search(r"@[0-9a-f]{40}$", use), f"unpinned action {use}"
    assert "permission-checks: write" in text
    for step in text.split("      - name: ")[1:]:
        if "run: |" in step:
            assert "${{" not in step.split("run: |", 1)[1], "no expression interpolation inside run scripts"
        if "steps.app_token.outputs.token" in step:
            assert step.startswith("Publish exact-head check"), "App token only reaches the publish step"
    assert re.search(r"group: wow-v17-[a-z-]+-check\n  cancel-in-progress: false", text)


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
    return tmp_path


def _routes_for_pr(runs):
    routes = _gather_routes(runs=runs)
    routes[f"repos/{REPO}/pulls/7"][1]["user"] = {"login": "wow-engineering[bot]"}
    return {k: list(v) for k, v in routes.items()}


def _run_decide(tmp, wf, routes, env_extra):
    (tmp / "routes.json").write_text(json.dumps(routes))
    runner = tmp / "runner"
    runner.mkdir(exist_ok=True)
    env = dict(os.environ, PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", FAKE_GH_ROUTES=str(tmp / "routes.json"),
               FAKE_GH_LOG=str(tmp / "gh.log"), GITHUB_REPOSITORY=REPO, RUNNER_TEMP=str(runner),
               GITHUB_OUTPUT=str(tmp / "out.txt"), GITHUB_STEP_SUMMARY=str(tmp / "summary.md"), GH_TOKEN="read-only")
    env.update(env_extra)
    proc = subprocess.run(["bash", "-c", _step_script(wf, "Decide at exact head (read-only)")],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    return proc, runner, env


def _evidence_runs(extra=()):
    runs = [{"id": i, "name": n, "head_sha": HEAD, "status": "completed", "conclusion": "success",
             "app": {"id": GHA}, "completed_at": "2026-10-09T03:00:00Z"}
            for i, n in enumerate((*m.QA_EVIDENCE_CHECKS, m.TRUSTED_GOVERNANCE_CHECK))]
    return runs + list(extra)


def test_qa_workflow_end_to_end_publishes_success_with_its_own_token(fake_gh):
    prs = json.dumps([{"number": 7, "head": {"sha": HEAD}, "base": {"ref": "main"}}])
    proc, runner, env = _run_decide(fake_gh, QA_WF, _routes_for_pr(_evidence_runs()),
                                    {"EVENT": "workflow_run", "WR_PRS": prs, "INPUT_PR": ""})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "success" and decision["check_name"] == m.QA_CHECK
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
    runs = [r for r in _evidence_runs() if r["name"] != m.TRUSTED_GOVERNANCE_CHECK]
    proc, runner, _ = _run_decide(fake_gh, QA_WF, _routes_for_pr(runs), {"EVENT": "workflow_dispatch", "INPUT_PR": "7", "WR_PRS": "null"})
    assert proc.returncode == 0, proc.stderr
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure"
    assert f"QA_EVIDENCE_MISSING:{m.TRUSTED_GOVERNANCE_CHECK}" in decision["findings"]


def test_invalid_dispatch_target_fails_without_publishing(fake_gh):
    proc, runner, _ = _run_decide(fake_gh, QA_WF, _routes_for_pr(_evidence_runs()),
                                  {"EVENT": "workflow_dispatch", "INPUT_PR": "7;id", "WR_PRS": "null"})
    assert proc.returncode != 0
    assert "::error::QA_TARGETS_UNRESOLVED" in proc.stdout
    assert not (runner / "decisions" / "decision-7.json").exists()


def test_release_workflow_end_to_end_requires_qa_from_qa_app(fake_gh):
    qa_run = {"id": 900, "name": m.QA_CHECK, "head_sha": HEAD, "status": "completed", "conclusion": "success",
              "app": {"id": int(QA_APP)}, "completed_at": "2026-10-09T03:30:00Z"}
    routes = _routes_for_pr(_evidence_runs([qa_run]))
    routes[f"repos/{REPO}/pulls?state=open&base=main&per_page=100"] = [200, [
        {"number": 7, "draft": False, "head": {"sha": HEAD, "repo": {"full_name": REPO}}}]]
    proc, runner, _ = _run_decide(fake_gh, REL_WF, routes, {"EVENT": "schedule", "INPUT_PR": "", "WR_PRS": "null",
                                                           "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((runner / "decisions" / "decision-7.json").read_text())["conclusion"] == "success"
    # Same evidence, but the QA check came from the Release App itself: must fail.
    routes[f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1"][1]["check_runs"][-1]["app"]["id"] = int(REL_APP)
    proc, runner, _ = _run_decide(fake_gh, REL_WF, routes, {"EVENT": "schedule", "INPUT_PR": "", "WR_PRS": "null",
                                                           "QA_APP_ID": QA_APP, "RELEASE_APP_ID": REL_APP})
    decision = json.loads((runner / "decisions" / "decision-7.json").read_text())
    assert decision["conclusion"] == "failure" and "RELEASE_QA_FOREIGN_SOURCE" in decision["findings"]


@pytest.mark.parametrize("wf, code", [(QA_WF, "QA_APP_CREDENTIAL_MISSING"), (REL_WF, "RELEASE_APP_CREDENTIAL_MISSING")])
@pytest.mark.parametrize("app_id, present", [("", "true"), ("1002", "false"), ("12;x", "true")])
def test_missing_app_identity_fails_closed_and_publishes_nothing(wf, code, app_id, present):
    proc = subprocess.run(["bash", "-c", _step_script(wf, "Require this role's App identity")],
                          env=dict(os.environ, APP_ID=app_id, KEY_PRESENT=present), capture_output=True, text=True)
    assert proc.returncode != 0 and f"::error::{code}:" in proc.stdout


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
        emitted |= set(re.findall(r"::error::((?:QA|RELEASE)_[A-Z_]+)", wf.read_text()))
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
        assert "ref: main" in source
        assert "vars." + app_id in source
        assert "secrets." + secret in source
        other_secret = "WOW_RELEASE_APP_PRIVATE_KEY" if environment == "wow-qa" else "WOW_QA_APP_PRIVATE_KEY"
        assert "secrets." + other_secret not in source
