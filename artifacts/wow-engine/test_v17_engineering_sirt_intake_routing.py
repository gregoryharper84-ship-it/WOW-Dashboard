"""Existing SIRT intake must admit exact-head existing PRs, never approve them.

These tests execute the trusted workflow's resolver shell using a deterministic
fake GH API. They do not create a GitHub issue, approve PRs or run PR source.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-sirt-intake.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = "a" * 40
OLD_SHA = "b" * 40


def _wf():
    return yaml.safe_load(WORKFLOW.read_text())


def _on():
    wf = _wf()
    return wf.get("on") or wf.get(True) or {}


def _resolver():
    return next(step for step in _wf()["jobs"]["independent-intake"]["steps"]
                if step.get("id") == "resolve")["run"]


def _fake_pr(**over):
    pr = {
        "number": 2547, "state": "open", "draft": False,
        "body": "Incident: #2076",
        "head": {"sha": SHA, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }
    for key, value in over.items():
        pr[key] = value
    return pr


def _run(tmp_path, event="workflow_dispatch", pr=None, requested_sha=SHA,
         labels=None, worker_gate="PASS"):
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env python3\nimport json,os,sys\n"
        "if sys.argv[1:3] == ['api', 'repos/'+os.environ['GITHUB_REPOSITORY']+'/pulls/2547']:\n"
        "  print(os.environ['FAKE_PR_JSON'])\n"
        "elif sys.argv[1:3] == ['pr', 'list']:\n"
        "  print(os.environ['FAKE_PRS_JSON'])\n"
        "else:\n"
        "  print('unexpected gh call',sys.argv,file=sys.stderr);sys.exit(41)\n"
    )
    fake_gh.chmod(0o755)
    # Each GitHub Actions invocation starts with fresh runner output files.
    # Reused pytest tmp_path across scenarios must not preserve prior status.
    output = tmp_path / "out.txt"
    output.write_text("")
    summary = tmp_path / "summary.txt"
    summary.write_text("")
    env = {**os.environ, "PATH": str(tmp_path)+os.pathsep+os.environ.get("PATH", ""),
           "GITHUB_REPOSITORY": REPO, "GITHUB_OUTPUT": str(output),
           "GITHUB_STEP_SUMMARY": str(summary), "GH_TOKEN": "test-only",
           "SOURCE_EVENT": event,
           "SOURCE_WORKFLOW": "wow-v17-chatgpt-engineering-worker" if event == "workflow_run" else "",
           "SOURCE_ID": "23456", "SOURCE_ATTEMPT": "1",
           "INPUT_PR": "2547", "INPUT_HEAD_SHA": requested_sha,
           "LABELS_JSON": json.dumps(labels if labels is not None else
                                      [{"name": "sirt-review-required"}]),
           "FAKE_PR_JSON": json.dumps(pr if pr is not None else _fake_pr()),
           "FAKE_PRS_JSON": json.dumps([{
               "number": 2547, "headRefOid": SHA, "state": "OPEN",
               "isDraft": False, "body": (
                   f"Pre-PR agent gates: {worker_gate}\nIncident: `#2076`")
           }])}
    process = subprocess.run(["bash", "-e", "-c", _resolver()], env=env,
                             text=True, capture_output=True, timeout=20)
    return process, output.read_text()


def test_existing_pr_manual_entry_requires_exact_pr_and_head(tmp_path):
    inputs = _on()["workflow_dispatch"]["inputs"]
    assert inputs["pr_number"]["required"] is True
    assert inputs["head_sha"]["required"] is True
    process, output = _run(tmp_path)
    assert process.returncode == 0, process.stderr
    assert "found=true" in output
    assert "pr=2547" in output
    assert f"sha={SHA}" in output
    assert "incident=2076" in output


@pytest.mark.parametrize("bad", ["", OLD_SHA, "bad-sha"])
def test_manual_entry_denies_stale_or_missing_exact_sha(tmp_path, bad):
    proc, output = _run(tmp_path, requested_sha=bad)
    assert proc.returncode != 0
    assert "found=true" not in output


@pytest.mark.parametrize("changed", [
    {"draft": True}, {"state": "closed"},
    {"base": {"ref": "feature", "repo": {"full_name": REPO}}},
    {"head": {"sha": SHA, "repo": {"full_name": "untrusted/fork"}}},
    {"body": "See issue #2076"}, {"body": "Incident: #2076\nIncident: #2020"},
])
def test_manual_entry_denies_draft_closed_fork_or_ambiguous_incident(tmp_path, changed):
    proc, output = _run(tmp_path, pr=_fake_pr(**changed))
    assert proc.returncode != 0
    assert "found=true" not in output


def test_labeled_pr_target_routes_only_explicit_sirt_label(tmp_path):
    on = _on()
    assert "pull_request_target" in on
    assert "labeled" in on["pull_request_target"]["types"]
    accepted, output = _run(tmp_path, event="pull_request_target")
    assert accepted.returncode == 0, accepted.stderr
    assert "found=true" in output
    unlabelled, output = _run(tmp_path, event="pull_request_target", labels=[])
    assert unlabelled.returncode == 0, unlabelled.stderr
    assert "found=false" in output
    assert "found=true" not in output


def test_intake_remains_not_review_approval_and_keeps_original_worker_guard():
    source = WORKFLOW.read_text()
    assert "Pre-PR agent gates:" in source
    assert "WOW_SIRT_INTAKE_PR_${PR}_HEAD_${SHA}" in source
    assert "SIRT_REVIEW_REQUIRED" in source
    assert "NOT_YET_PERFORMED" in source
    assert "Merge authorization: NONE" in source
    assert "Production deployment authorization: NONE" in source
    assert "pull_request_target" in source
    assert "actions/checkout" not in source
    assert "can_execute=false" in source


def test_worker_origin_incident_backticks_keep_legacy_admission(tmp_path):
    process, output = _run(tmp_path, event="workflow_run")
    assert process.returncode == 0, process.stderr
    assert "found=true" in output
    assert "pr=2547" in output
    assert f"sha={SHA}" in output
    assert "incident=2076" in output


def test_all_intake_entrypoints_serialize_one_issue_writer_group():
    wf = _wf()
    group = wf["concurrency"]["group"]
    # Runtime PR and worker-run IDs cannot be used in the group: they differ
    # across routes for the same exact PR/head, allowing duplicate issue races.
    assert "workflow_run.id" not in group
    assert "pull_request.number" not in group
    assert "inputs.pr_number" not in group
    assert group == "sirt-intake-${{ github.repository }}"
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert wf["concurrency"]["queue"] == "max"


def _writer():
    """Use the real workflow writer, not a test-only implementation."""
    return next(step for step in _wf()["jobs"]["independent-intake"]["steps"]
                if step.get("name") == "Write SIRT independent-intake issue and cross-link")["run"]


def _matching_issue(number, author, incident="2076", generated=True):
    """A matching marker must also have incident-specific intake evidence."""
    header = "## Independent SIRT intake — NOT verification approval" if generated else "Unverified hand-edited body"
    return {"number": number,
            "body": (
                "<!-- WOW_SIRT_INTAKE_PR_2547_HEAD_" + SHA + " -->\n" +
                header + "\n" + "- Engineering incident: #" + incident + "\n" +
                "- Engineering PR: #2547\n- Exact PR head: " + SHA + "\n" +
                "- Intake status: SIRT_REVIEW_REQUIRED\n"),
            "author": {"login": author}}


def _run_writer(tmp_path, *, initial_issues=None, initial_comments=None,
                source_id="dispatch-1"):
    """Execute the writer against a persistent fake issue and PR-comment API."""
    issues_file = tmp_path / "issues.json"
    if initial_issues is not None:
        issues_file.write_text(json.dumps(initial_issues))
    elif not issues_file.exists():
        issues_file.write_text("[]")
    comments_file = tmp_path / "comments.json"
    if initial_comments is not None:
        comments_file.write_text(json.dumps(initial_comments))
    elif not comments_file.exists():
        comments_file.write_text("[]")
    calls_file = tmp_path / "calls.log"
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\n"
        "issues=Path(os.environ['FAKE_ISSUES_FILE'])\n"
        "comments=Path(os.environ['FAKE_COMMENTS_FILE'])\n"
        "calls=Path(os.environ['FAKE_CALLS_FILE'])\n"
        "args=sys.argv[1:]\n"
        "if args[:2]==['issue','list']:\n"
        "  print(issues.read_text())\n"
        "elif args[:2]==['issue','create']:\n"
        "  data=json.loads(issues.read_text())\n"
        "  ix=args.index('--body-file')\n"
        "  content=Path(args[ix+1]).read_text()\n"
        "  data.append({'number':9000,'body':content,'author':{'login':'app/github-actions'}})\n"
        "  issues.write_text(json.dumps(data))\n"
        "  calls.open('a').write('issue:create\\n')\n"
        "  print('https://github.com/'+os.environ['GITHUB_REPOSITORY']+'/issues/9000')\n"
        "elif args[0]=='api' and 'issues/2547/comments' in args[1]:\n"
        "  calls.open('a').write('api:comments\\n')\n"
        "  print(comments.read_text())\n"
        "elif args[:2]==['pr','comment']:\n"
        "  body=args[args.index('--body')+1]\n"
        "  data=json.loads(comments.read_text())\n"
        "  data.append({'body':body,'user':{'login':'app/github-actions'}})\n"
        "  comments.write_text(json.dumps(data))\n"
        "  calls.open('a').write('pr:comment\\n')\n"
        "else:\n"
        "  print('unexpected GitHub operation',args,file=sys.stderr);sys.exit(51)\n"
    )
    fake_gh.chmod(0o755)
    summary = tmp_path / "writer-summary.txt"
    summary.write_text("")
    env = {**os.environ,
           "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
           "GITHUB_REPOSITORY": REPO,
           "GITHUB_STEP_SUMMARY": str(summary), "GH_TOKEN": "test-only",
           "RUNNER_TEMP": str(tmp_path), "PR": "2547", "SHA": SHA,
           "INCIDENT": "2076", "SOURCE_ID": source_id,
           "SOURCE_ATTEMPT": "1", "SOURCE_CONCLUSION": "INTAKE_ONLY_NOT_A_REVIEW_VERDICT",
           "FAKE_ISSUES_FILE": str(issues_file),
           "FAKE_COMMENTS_FILE": str(comments_file),
           "FAKE_CALLS_FILE": str(calls_file)}
    process = subprocess.run(["bash", "-e", "-c", _writer()],
                             env=env, capture_output=True, text=True, timeout=20)
    calls = calls_file.read_text().splitlines() if calls_file.exists() else []
    return process, calls, json.loads(issues_file.read_text()), summary.read_text()


def test_forged_marker_from_untrusted_creator_cannot_suppress_sirt_intake(tmp_path):
    """The prior writer incorrectly trusted a public issue containing the marker."""
    forged = _matching_issue(77, "untrusted-issue-writer")
    run, calls, issues, _ = _run_writer(tmp_path, initial_issues=[forged])
    assert run.returncode == 0, run.stderr
    assert calls == ["api:comments", "issue:create", "pr:comment"]
    assert len(issues) == 2
    assert issues[-1]["author"]["login"] == "app/github-actions"


@pytest.mark.parametrize("creator", ["github-actions[bot]", "app/github-actions", "gregoryharper84-ship-it"])
def test_marker_reuse_requires_authentic_existing_intake_creator(tmp_path, creator):
    trusted = _matching_issue(75, creator)
    run, calls, issues, summary = _run_writer(tmp_path, initial_issues=[trusted])
    assert run.returncode == 0, run.stderr
    assert calls == ["api:comments", "pr:comment"]
    assert len(issues) == 1
    assert "Existing SIRT intake #75" in summary


def test_two_admitted_routes_same_head_create_exactly_one_intake_and_crosslink(tmp_path):
    first, first_calls, _, _ = _run_writer(tmp_path, initial_issues=[],
                                             source_id="workflow-run-1")
    second, second_calls, issues, _ = _run_writer(tmp_path,
                                                   source_id="labeled-pr-2")
    assert first.returncode == second.returncode == 0
    assert first_calls == ["api:comments", "issue:create", "pr:comment"]
    assert second_calls == ["api:comments", "issue:create", "pr:comment", "api:comments"]
    assert len(issues) == 1


def test_rejected_resolver_never_reaches_issue_writer(tmp_path):
    """The actual workflow step guard must exclude any rejected resolver."""
    writer_step = next(step for step in _wf()["jobs"]["independent-intake"]["steps"]
                       if step.get("name") == "Write SIRT independent-intake issue and cross-link")
    assert writer_step["if"] == "steps.resolve.outputs.found == 'true'"
    bad, output = _run(tmp_path, requested_sha=OLD_SHA)
    assert bad.returncode != 0
    assert "found=true" not in output


@pytest.mark.parametrize("gate,allowed", [("PASS", True), ("HOLD", False),
                                         ("REJECT", False)])
def test_worker_origin_preserves_real_pre_pr_gate_enforcement(tmp_path, gate, allowed):
    """The original trusted worker lane may never admit HOLD or REJECT."""
    process, output = _run(tmp_path, event="workflow_run", worker_gate=gate)
    if allowed:
        assert process.returncode == 0, process.stderr
        assert "found=true" in output
    else:
        assert process.returncode != 0
        assert "found=true" not in output
        assert "SIRT_HANDOFF_BLOCKED_PRE_PR_GATES_NOT_PASS" in process.stdout


def test_reuse_wrong_incident_must_create_new_matching_intake(tmp_path):
    stale = _matching_issue(75, "app/github-actions", incident="2020")
    proc, calls, issues, _ = _run_writer(tmp_path, initial_issues=[stale])
    assert proc.returncode == 0, proc.stderr
    assert calls == ["api:comments", "issue:create", "pr:comment"]
    assert len(issues) == 2
    assert "- Engineering incident: #2076" in issues[-1]["body"]


def test_reuse_rejects_unverified_marker_body_even_with_trusted_creator(tmp_path):
    manually_edited = _matching_issue(75, "app/github-actions", generated=False)
    proc, calls, issues, _ = _run_writer(tmp_path, initial_issues=[manually_edited])
    assert proc.returncode == 0, proc.stderr
    assert "issue:create" in calls
    assert len(issues) == 2


def test_retry_repairs_missing_crosslink_without_recreating_issue(tmp_path):
    existing = _matching_issue(75, "app/github-actions")
    proc, calls, issues, _ = _run_writer(tmp_path, initial_issues=[existing])
    assert proc.returncode == 0, proc.stderr
    assert calls == ["api:comments", "pr:comment"]
    assert len(issues) == 1


def test_retry_does_not_duplicate_authenticated_existing_crosslink(tmp_path):
    existing = _matching_issue(75, "app/github-actions")
    link = ("Independent SIRT intake created: "
            "https://github.com/" + REPO + "/issues/75. "
            "Exact head: " + SHA)
    proc, calls, issues, _ = _run_writer(
        tmp_path, initial_issues=[existing],
        initial_comments=[{"body":link, "user":{"login":"app/github-actions"}}])
    assert proc.returncode == 0, proc.stderr
    assert calls == ["api:comments"]
    assert len(issues) == 1
