"""Adversarial tests for the V17 completion-only governance gate.

A successful workflow on main is NOT an exact-head approval. Check publication
must follow real receipt verification, never precede it, and must be fail-closed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / ".github/workflows/wow-v17-trusted-governance-gate.yml"
SHA = "a" * 40


def workflow():
    return yaml.load(GATE.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def publish_script() -> str:
    steps = workflow()["jobs"]["trusted-governance"]["steps"]
    return next(s["run"] for s in steps if s["name"] == "Publish exact-head verified decision")


def test_only_certifier_completion_triggers_trusted_gate():
    doc = workflow()
    assert set(doc["on"]) == {"workflow_run"}
    assert doc["on"]["workflow_run"]["workflows"] == ["wow-v17-existing-pr-governance-review"]
    assert "github.event.workflow_run.conclusion == 'success'" in doc["jobs"]["trusted-governance"]["if"]
    assert "github.event_name == 'workflow_run'" in doc["jobs"]["trusted-governance"]["if"]
    s = GATE.read_text(encoding="utf-8")
    assert 'actions/runs/${SOURCE_RUN_ID}/artifacts?per_page=100' in s
    assert "seq 1 180" not in s
    assert "actions/artifacts?per_page=100&page=" not in s
    assert "checks: write" in s


def test_exact_head_check_is_never_published_before_proof():
    doc = workflow()
    steps = doc["jobs"]["trusted-governance"]["steps"]
    check = next(s for s in steps if s["name"] == "Publish exact-head verified decision")
    verify = next(s for s in steps if s["name"] == "Download and verify exact-head receipt")
    assert verify["id"] == "verify_receipt"
    assert check["if"] == "always() && steps.artifact.outputs.applicable == 'true'"
    assert check["env"]["RECEIPT_VERIFY_OUTCOME"] == "${{ steps.verify_receipt.outcome }}"
    assert check["env"]["SOURCE_VERIFY_OUTCOME"] == "${{ steps.workflow.outcome }}"
    assert 'conclusion=failure' in check["run"]
    assert 'if [ "$SOURCE_VERIFY_OUTCOME" = "success" ] && [ "$RECEIPT_VERIFY_OUTCOME" = "success" ]; then' in check["run"]
    assert 'conclusion=success' in check["run"]
    assert "GOVERNANCE_PR_HEAD_CHECK_PUBLICATION_FAILED" in check["run"]


def run_publish(tmp_path, source="success", receipt="success", head=SHA):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "posted.txt"
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args=sys.argv[1:]\n"
        "if args[:2] == ['api','/repos/owner/repo/pulls/42']:\n"
        "  print(json.dumps({'state':'open','head':{'sha':os.environ['LIVE_SHA'],'repo':{'full_name':'owner/repo'}},'base':{'ref':'main','repo':{'full_name':'owner/repo'}}}))\n"
        "elif args[:4] == ['api','--method','POST','/repos/owner/repo/check-runs']:\n"
        "  fields=[args[i+1] for i,a in enumerate(args[:-1]) if a == '-f']\n"
        "  values=dict(x.split('=',1) for x in fields)\n"
        "  pathlib.Path(os.environ['POST_LOG']).write_text(values['conclusion'])\n"
        "  print(json.dumps({'id':123,'head_sha':values['head_sha'],'name':values['name'],'status':values['status'],'conclusion':values['conclusion']}))\n"
        "else:\n"
        "  print('unexpected command '+repr(args),file=sys.stderr);sys.exit(9)\n"
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "owner/repo",
        "PR_NUMBER": "42", "PR_HEAD_SHA": SHA,
        "PRODUCER_RUN_ID": "998",
        "SOURCE_VERIFY_OUTCOME": source, "RECEIPT_VERIFY_OUTCOME": receipt,
        "LIVE_SHA": head, "POST_LOG": str(log),
    }
    proc = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", publish_script()],
                          env=env, capture_output=True, text=True, check=False, timeout=10)
    return proc, log.read_text() if log.exists() else None


@pytest.mark.parametrize("source,receipt,conclusion", [
    ("success","success","success"), ("success","failure","failure"),
    ("failure","success","failure"), ("failure","failure","failure")
])
def test_publishing_is_truthful(tmp_path, source, receipt, conclusion):
    proc, published = run_publish(tmp_path, source, receipt)
    assert proc.returncode == 0, proc.stderr
    assert published == conclusion


def test_stale_head_prevents_check_publication(tmp_path):
    proc, published = run_publish(tmp_path, head="b" * 40)
    assert proc.returncode != 0
    assert "GOVERNANCE_PR_HEAD_MOVED_OR_NOT_AUTHENTIC" in proc.stderr
    assert published is None
