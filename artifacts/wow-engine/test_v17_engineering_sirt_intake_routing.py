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
         labels=None):
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
                   "Pre-PR agent gates: PASS\nIncident: `#2076`")
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
