"""No paid reviewer/no retired receipt required for routine R0/R1/R2 merge.

Execute the real Morning-Green V4 shell gate offline against a fake GitHub API.
This tests only the deterministic evidence gate, not live branch protection.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows/wow-v17-morning-green-continuation.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
HEAD = "a" * 40
CHECKS = [
    ("WOW required-three regression", 15368),
    ("WOW governed probability backend", 15368),
    ("WOW additional required regression", 15368),
    ("WOW Independent QA exact-head", 5245832),
    ("WOW Release Authority exact-head", 5245853),
]


def _gate_script():
    workflow = yaml.load(WF.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    step = next(
        s for s in workflow["jobs"]["continue-repair"]["steps"]
        if s["id"] == "governance"
    )
    assert "Verify V4 exact-head QA and Release checks" in step["name"]
    assert "wow-v17-engineering-governance" not in step["run"]
    assert "gh pr merge" not in step["run"]
    return step["run"]


def _run(tmp_path, *, changed_name=None, changed_app=None, changed_status=None,
         moved_head=False, omit=None):
    pr = {
        "state": "open", "draft": False,
        "head": {"sha": "b" * 40 if moved_head else HEAD,
                 "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }
    checks = [
        {
            "id": i + 1, "name": name, "head_sha": HEAD,
            "app": {"id": app}, "status": "completed", "conclusion": "success",
        }
        for i, (name, app) in enumerate(CHECKS)
        if name != omit
    ]
    if changed_name:
        check = next(c for c in checks if c["name"] == changed_name)
        if changed_app is not None:
            check["app"]["id"] = changed_app
        if changed_status is not None:
            check["conclusion"] = changed_status
    routes = {
        f"/repos/{REPO}/pulls/11": pr,
        f"/repos/{REPO}/commits/{HEAD}/check-runs?per_page=100&page=1":
            {"check_runs": checks},
    }
    (tmp_path / "routes.json").write_text(json.dumps(routes), encoding="utf-8")
    script = tmp_path / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, json\n"
        "routes=json.load(open(os.environ['FAKE_GITHUB_ROUTES'],encoding='utf-8'))\n"
        "print(json.dumps(routes[sys.argv[-1]]))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    output = tmp_path / "out"
    summary = tmp_path / "summary"
    env = dict(
        os.environ,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        FAKE_GITHUB_ROUTES=str(tmp_path / "routes.json"),
        GITHUB_REPOSITORY=REPO,
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
        HEAD_SHA=HEAD,
        PR_NUMBER="11",
        GH_TOKEN="read-only-test",
    )
    proc = subprocess.run(
        ["bash", "-c", _gate_script()], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=15, check=False,
    )
    return proc, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8")


def test_all_five_authentic_latest_checks_approve_only_routine_transition(tmp_path):
    proc, output, summary = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert output.splitlines()[-1] == "approved=true"
    assert "routine scope only" in summary


@pytest.mark.parametrize("failure", [
    {"omit": CHECKS[0][0]},
    {"omit": CHECKS[3][0]},
    {"omit": CHECKS[4][0]},
    {"changed_name": CHECKS[3][0], "changed_app": 15368},
    {"changed_name": CHECKS[4][0], "changed_app": 5245832},
    {"changed_name": CHECKS[2][0], "changed_status": "failure"},
    {"moved_head": True},
])
def test_missing_foreign_failed_or_moved_head_never_approves(tmp_path, failure):
    proc, output, summary = _run(tmp_path, **failure)
    assert proc.returncode == 0, proc.stderr
    assert "approved=true" not in output
    assert output.splitlines()[-1] == "approved=false"
    assert "V4_" in summary
