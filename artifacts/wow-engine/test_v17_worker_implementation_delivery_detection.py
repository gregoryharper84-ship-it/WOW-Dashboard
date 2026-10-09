"""Executed regression for #1021: the worker's "Normalize Implementation result" step
must treat a repair that only adds new (untracked) files as a real change.

``git diff --quiet`` ignores untracked files, so an implementation that created
a new test or module was rejected with "reported changes but returned no
working-tree diff". This runs the real step script from both worker workflows in
a throwaway repository whose push target is a local bare repository.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = {
    "openai": (ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml", "chatgpt"),
    "anthropic": (ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml", "claude"),
}
STEP_NAME = "Normalize Implementation result"
TOKEN = "test-token"
REPO = "o/r"


def _step_script(workflow: Path) -> str:
    doc = yaml.safe_load(workflow.read_text())
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == STEP_NAME:
                assert "${{" not in step["run"]
                return step["run"]
    raise AssertionError(f"{STEP_NAME} missing from {workflow.name}")


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def sandbox(tmp_path: Path):
    if shutil.which("jq") is None or shutil.which("git") is None:
        pytest.skip("jq and git are required")
    gitconfig = tmp_path / "gitconfig"
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env=env)
    gitconfig.write_text(
        "[user]\n\tname = t\n\temail = t@example.com\n"
        "[init]\n\tdefaultBranch = main\n"
        f'[url "{bare}"]\n\tinsteadOf = https://x-access-token:{TOKEN}@github.com/{REPO}.git\n'
    )
    work.mkdir()
    _git(work, "init", "-q", env=env)
    (work / ".gitignore").write_text("__pycache__/\n/artifacts/wow-engine/v17/.agent-handoff/\n")
    (work / "module.py").write_text("x = 1\n")
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-q", "-m", "init", env=env)
    _git(work, "remote", "add", "origin", str(bare), env=env)
    return work, bare, env


def _run(provider: str, work: Path, env: dict, changed: bool):
    workflow, prefix = WORKFLOWS[provider]
    out = work.parent / "github_output"
    out.write_text("")
    step_env = dict(env)
    step_env.update(
        {
            "RESULT": json.dumps({"changed": changed, "incident_id": "823", "risk_class": "R1"}),
            "GITHUB_TOKEN": TOKEN,
            "EXPECTED_INCIDENT": "823",
            "EXPECTED_RISK": "R1",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "99",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(out),
        }
    )
    proc = subprocess.run(
        ["bash", "-c", _step_script(workflow)],
        cwd=work, env=step_env, capture_output=True, text=True,
    )
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return proc, outputs, f"{prefix}/engineering/99-1"


@pytest.mark.parametrize("provider", sorted(WORKFLOWS))
def test_untracked_only_repair_is_delivered(provider, sandbox):
    work, bare, env = sandbox
    (work / "test_new_regression.py").write_text("def test_x():\n    assert True\n")
    proc, outputs, branch = _run(provider, work, env, changed=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["changed"] == "true"
    assert outputs["branch"] == branch
    assert len(outputs["head_sha"]) == 40
    pushed = _git(bare, "ls-tree", "--name-only", "-r", branch, env=env).split()
    assert "test_new_regression.py" in pushed


@pytest.mark.parametrize("provider", sorted(WORKFLOWS))
def test_tracked_modification_still_delivered(provider, sandbox):
    work, bare, env = sandbox
    (work / "module.py").write_text("x = 2\n")
    proc, outputs, branch = _run(provider, work, env, changed=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(bare, "show", f"{branch}:module.py", env=env) == "x = 2\n"


@pytest.mark.parametrize("provider", sorted(WORKFLOWS))
def test_claimed_change_without_any_change_fails_closed(provider, sandbox):
    work, bare, env = sandbox
    proc, outputs, branch = _run(provider, work, env, changed=True)
    assert proc.returncode != 0
    assert "reported changes but returned no working-tree diff" in proc.stdout
    assert "branch" not in outputs
    assert _git(bare, "branch", "--list", branch, env=env).strip() == ""


@pytest.mark.parametrize("provider", sorted(WORKFLOWS))
def test_ignored_files_only_are_not_a_change(provider, sandbox):
    work, bare, env = sandbox
    (work / "__pycache__").mkdir()
    (work / "__pycache__" / "module.cpython-311.pyc").write_bytes(b"\0")
    handoff = work / "artifacts/wow-engine/v17/.agent-handoff"
    handoff.mkdir(parents=True)
    (handoff / "implementation.json").write_text("{}")
    proc, _, branch = _run(provider, work, env, changed=True)
    assert proc.returncode != 0
    assert "reported changes but returned no working-tree diff" in proc.stdout
    assert _git(bare, "branch", "--list", branch, env=env).strip() == ""


@pytest.mark.parametrize("provider", sorted(WORKFLOWS))
def test_reported_no_change_does_not_push(provider, sandbox):
    work, bare, env = sandbox
    (work / "scratch.txt").write_text("not delivered\n")
    proc, outputs, branch = _run(provider, work, env, changed=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["changed"] == "false"
    assert outputs["branch"] == ""
    assert _git(bare, "branch", "--list", branch, env=env).strip() == ""
