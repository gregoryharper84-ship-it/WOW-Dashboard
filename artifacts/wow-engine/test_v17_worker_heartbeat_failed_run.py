"""Executed regression for #1021: a failed engineering worker run must not leave a
RUNNING heartbeat or keep its lease until expiry.

Runs the real "Persist dual-stream heartbeat and receipt" step script from both
worker workflows with a stubbed ``gh`` and checks the receipt and heartbeat body.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = {
    "openai": ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
    "anthropic": ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml",
}
STEP_NAME = "Persist dual-stream heartbeat and receipt"
CLAIMED_EXPIRY = "2099-01-01T00:00:00Z"

GH_STUB = """#!/usr/bin/env bash
# Records PATCH/POST bodies; returns no existing comments for GET.
for arg in "$@"; do
  case "$arg" in
    body=*) printf '%s' "${arg#body=}" > "$GH_BODY_OUT" ;;
  esac
done
if [ "${2:-}" != "--method" ]; then echo '[]'; fi
"""


def _step(workflow: Path) -> dict:
    doc = yaml.safe_load(workflow.read_text())
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == STEP_NAME:
                return step
    raise AssertionError(f"{STEP_NAME} missing from {workflow.name}")


def _run(workflow: Path, job_status: str, tmp_path: Path) -> tuple[dict, str]:
    if shutil.which("jq") is None:
        pytest.skip("jq not installed")
    step = _step(workflow)
    assert step["env"]["JOB_STATUS"] == "${{ job.status }}"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    body_out = tmp_path / "body.md"
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_RUN_ID": "123",
        "GITHUB_REPOSITORY": "o/r",
        "GH_BODY_OUT": str(body_out),
        "RESTORATION": "823",
        "LEASE_EPOCH": "34",
        "WORKER_ID": "github-actions:123:1",
        "LEASE_EXPIRES_AT": CLAIMED_EXPIRY,
        "WORKER_MODE": "RUNNING",
        "LEASE_GROUP": "GLOBAL",
        "JOB_STATUS": job_status,
    }
    # Unrendered ${{ }} expressions are not present in this step's script.
    assert "${{" not in step["run"]
    subprocess.run(["bash", "-c", step["run"]], env=env, check=True, cwd=tmp_path)
    receipt = json.loads((tmp_path / "wow-dual-stream-receipt.json").read_text())
    return receipt, body_out.read_text()


@pytest.mark.parametrize("provider", sorted(WORKFLOWS))
@pytest.mark.parametrize("job_status", ["failure", "cancelled"])
def test_failed_run_releases_lease_and_reports_safe_hold(provider, job_status, tmp_path):
    receipt, body = _run(WORKFLOWS[provider], job_status, tmp_path)
    assert receipt["worker_mode"] == "SAFE_HOLD"
    assert receipt["safe_hold_state"] == "SAFE_HOLD"
    assert receipt["job_status"] == job_status
    assert receipt["lease_state"] == "RELEASED_ON_FAILURE"
    assert receipt["lease_claimed_expires_at"] == CLAIMED_EXPIRY
    assert receipt["lease_expires_at"] != CLAIMED_EXPIRY
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", receipt["lease_expires_at"])
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert "- worker_mode: RUNNING" not in body
    assert "- worker_mode: SAFE_HOLD" in body
    assert f"- job_status: {job_status}" in body
    assert "- lease_state: RELEASED_ON_FAILURE" in body
    assert f"- lease_expires_at: {CLAIMED_EXPIRY}" not in body


@pytest.mark.parametrize("provider", sorted(WORKFLOWS))
def test_successful_run_keeps_claimed_lease(provider, tmp_path):
    receipt, body = _run(WORKFLOWS[provider], "success", tmp_path)
    assert receipt["worker_mode"] == "RUNNING"
    assert receipt["lease_state"] == "HELD"
    assert receipt["lease_expires_at"] == CLAIMED_EXPIRY
    assert f"- lease_expires_at: {CLAIMED_EXPIRY}" in body
    assert receipt["can_execute"] is False
