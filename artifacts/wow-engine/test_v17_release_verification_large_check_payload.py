"""Regression for GitHub post-merge check evidence larger than Linux argv limits.

The production failure on PR #2555 used >128 KiB of check JSON as one
jq --argjson argument, exiting 126 before release verification. Exercise
the actual workflow shell step against bounded fake GitHub responses.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"
HEAD = "a" * 40
NAMES = (
    ("WOW required-three regression", 15368),
    ("WOW governed probability backend", 15368),
    ("WOW additional required regression", 15368),
)
WORKFLOWS = (
    "wow-verify",
    "wow-engine-verify",
    "wow-v17-rapid-repair",
    "wow-v17-change-impact-gate",
    "wow-v17-engineering-auditor-code-health",
    "wow-v17-spread-forward-shadow",
)


def _check(id_number, name, app_id):
    return {
        "id": id_number, "name": name, "head_sha": HEAD,
        "app": {"id": app_id}, "status": "completed", "conclusion": "success",
    }


def _run(tmp_path, mode):
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        item for item in doc["jobs"]["release-verification"]["steps"]
        if item["name"] == "Verify required exact-head CI"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,sys\n"
        "routes=json.load(open(os.environ['FAKE_GH_ROUTES']))\n"
        "key=next(arg for arg in sys.argv if arg.startswith('/repos/'))\n"
        "value=routes[key]\n"
        "if '--jq' in sys.argv:\n"
        "    assert sys.argv[sys.argv.index('--jq')+1]=='.workflow_runs'\n"
        "    value=value['workflow_runs']\n"
        "print(json.dumps(value))\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    checks = [
        {**_check(i + 1, "unrelated", 42), "output": {"text": "x" * 3800}}
        for i in range(40)
    ]
    checks.extend(_check(50 + i, name, app_id) for i, (name, app_id) in enumerate(NAMES))
    assert len(json.dumps(checks)) > 131072

    if mode in ("foreign", "failed"):
        latest = dict(checks[-2], id=100)
        if mode == "foreign":
            latest["app"] = {"id": 999}
        else:
            latest["conclusion"] = "failure"
        checks.append(latest)
    elif mode == "stale":
        checks[-2]["head_sha"] = "b" * 40
    elif mode == "optional_qa_failed":
        qa = _check(100, "WOW Independent QA exact-head", 5245832)
        checks.append(dict(qa, conclusion="failure"))
    elif mode == "missing":
        checks.pop()
    page = {"check_runs": None if mode == "malformed" else checks}
    runs = [{
        "name": name,
        "path": ".github/workflows/" + name + ".yml",
        "created_at": "2026-11-10T07:00:00Z",
        "status": "completed", "conclusion": "success",
    } for name in WORKFLOWS]
    if mode == "workflow_failed":
        runs[0]["conclusion"] = "failure"
    routes = {
        "/repos/test/repo/actions/runs?head_sha=" + HEAD + "&per_page=100":
            {"workflow_runs": runs},
        "/repos/test/repo/commits/" + HEAD + "/check-runs?per_page=100&page=1":
            page,
    }
    routes_file = tmp_path / "routes.json"
    routes_file.write_text(json.dumps(routes), encoding="utf-8")
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    return subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=ROOT,
        env={
            **os.environ, "FAKE_GH_ROUTES": str(routes_file),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "GITHUB_REPOSITORY": "test/repo", "HEAD_SHA": HEAD,
            "RUNNER_TEMP": str(runner_temp), "GH_TOKEN": "fixture-only",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        },
        text=True, capture_output=True, check=False,
    )


def test_large_page_exact_head_and_app_ids_pass_without_argv_overflow(tmp_path):
    proc = _run(tmp_path, "success")
    assert proc.returncode == 0, proc.stderr
    assert "native three-required-check evidence verified" in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize(
    ("mode", "diagnostic"),
    [
        ("workflow_failed", "wow-verify.yml -> completed|failure"),
        ("optional_qa_failed", "WOW Independent QA exact-head -> completed|failure"),
    ],
)
def test_failed_diagnostics_do_not_block_native_required_checks(tmp_path, mode, diagnostic):
    proc = _run(tmp_path, mode)
    assert proc.returncode == 0, proc.stderr
    assert diagnostic in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("foreign", "RELEASE_V4_REQUIRED_CHECK_MISSING_FOREIGN_OR_FAILED:WOW governed probability backend"),
        ("failed", "RELEASE_V4_REQUIRED_CHECK_MISSING_FOREIGN_OR_FAILED:WOW governed probability backend"),
        ("stale", "RELEASE_V4_REQUIRED_CHECK_MISSING_FOREIGN_OR_FAILED:WOW governed probability backend"),
        ("missing", "RELEASE_V4_REQUIRED_CHECK_MISSING_FOREIGN_OR_FAILED:WOW additional required regression"),
        ("malformed", "RELEASE_CHECK_PAGE_INVALID"),
    ],
)
def test_large_payload_keeps_release_failing_closed(tmp_path, mode, error):
    proc = _run(tmp_path, mode)
    assert proc.returncode != 0
    assert error in proc.stderr


def test_workflow_never_passes_unbounded_json_through_jq_argv():
    """Retain a focused regression for the exact argv failure class."""
    source = WORKFLOW.read_text(encoding="utf-8")
    assert '--argjson new "$page_checks"' not in source
