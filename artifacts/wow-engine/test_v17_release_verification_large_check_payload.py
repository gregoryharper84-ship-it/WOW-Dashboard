"""Regression coverage for the current three-check release policy.

The production failure passed a >128 KiB check-run page through a single jq
argv value. These tests execute the workflow step with bounded fake GitHub
responses and keep every required evidence path fail-closed.
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
REQUIRED = (
    ("WOW required-three regression", 15368),
    ("WOW governed probability backend", 15368),
    ("WOW additional required regression", 15368),
)
OPTIONAL = (
    ("WOW Independent QA exact-head", 5245832),
    ("WOW Release Authority exact-head", 5245853),
)
WORKFLOWS = (
    "wow-verify",
    "wow-engine-verify",
    "wow-v17-rapid-repair",
    "wow-v17-change-impact-gate",
    "wow-v17-engineering-auditor-code-health",
    "wow-v17-spread-forward-shadow",
)


def _check(id_number, name, app_id, *, head=HEAD, status="completed", conclusion="success"):
    return {
        "id": id_number,
        "name": name,
        "head_sha": head,
        "app": {"id": app_id},
        "status": status,
        "conclusion": conclusion,
    }


def _workflow_runs(failed=False):
    return [
        {
            "name": name,
            "path": f".github/workflows/{name}.yml",
            "created_at": "2026-11-24T07:00:00Z",
            "status": "completed",
            "conclusion": "failure" if failed and index == 0 else "success",
        }
        for index, name in enumerate(WORKFLOWS)
    ]


def _run(tmp_path, mode="success", target=0):
    step = next(
        item
        for item in yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["release-verification"]["steps"]
        if item["name"] == "Verify required exact-head CI"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "routes = json.load(open(os.environ['FAKE_GH_ROUTES'], encoding='utf-8'))\n"
        "path = next(arg for arg in sys.argv if arg.startswith('/repos/'))\n"
        "value = routes[path]\n"
        "if '--jq' in sys.argv:\n"
        "    value = value['workflow_runs']\n"
        "print(json.dumps(value))\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    checks = [
        {**_check(index + 1, "unrelated", 42), "output": {"text": "x" * 3800}}
        for index in range(40)
    ]
    checks.extend(_check(50 + index, name, app_id) for index, (name, app_id) in enumerate(REQUIRED + OPTIONAL))

    state = mode.split(":", 1)[1] if mode.startswith("state:") else mode
    if state in {"foreign", "failed", "pending", "stale", "missing"}:
        name, _ = REQUIRED[target]
        selected = next(check for check in checks if check["name"] == name)
        if state == "foreign":
            selected["app"] = {"id": 999}
        elif state == "failed":
            selected["conclusion"] = "failure"
        elif state == "pending":
            selected["status"] = "in_progress"
            selected["conclusion"] = None
        elif state == "stale":
            selected["head_sha"] = "b" * 40
        else:
            checks.remove(selected)

    routes = {
        f"/repos/test/repo/actions/runs?head_sha={HEAD}&per_page=100": {
            "workflow_runs": _workflow_runs(failed=state == "workflow_failed"),
        },
    }
    if state == "page2":
        routes[f"/repos/test/repo/commits/{HEAD}/check-runs?per_page=100&page=1"] = {"check_runs": checks[:100]}
        routes[f"/repos/test/repo/commits/{HEAD}/check-runs?per_page=100&page=2"] = {"check_runs": checks[100:]}
    elif state == "page_limit":
        for page in range(1, 11):
            routes[f"/repos/test/repo/commits/{HEAD}/check-runs?per_page=100&page={page}"] = {
                "check_runs": [_check(page * 100 + index, "unrelated", 42) for index in range(100)]
            }
    else:
        routes[f"/repos/test/repo/commits/{HEAD}/check-runs?per_page=100&page=1"] = {
            "check_runs": None if state == "malformed" else checks
        }

    if state == "latest":
        name, app_id = REQUIRED[0]
        checks = [check for check in checks if check["name"] != name]
        checks.extend([
            _check(500, name, app_id, conclusion="failure"),
            _check(600, name, app_id, conclusion="success"),
        ])
        routes[f"/repos/test/repo/commits/{HEAD}/check-runs?per_page=100&page=1"] = {
            "check_runs": list(reversed(checks)),
        }

    routes_file = tmp_path / "routes.json"
    routes_file.write_text(json.dumps(routes), encoding="utf-8")
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    return subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=ROOT,
        env={
            **os.environ,
            "FAKE_GH_ROUTES": str(routes_file),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "GITHUB_REPOSITORY": "test/repo",
            "HEAD_SHA": HEAD,
            "RUNNER_TEMP": str(runner_temp),
            "GH_TOKEN": "fixture-only",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        },
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("state", ["success", "foreign", "failed", "pending", "stale", "missing"])
@pytest.mark.parametrize("target", range(len(REQUIRED)))
def test_current_three_required_checks_are_exact_and_fail_closed(tmp_path, state, target):
    proc = _run(tmp_path, f"state:{state}", target)
    if state == "success":
        assert proc.returncode == 0, proc.stderr
        assert "three-required-check evidence verified" in (tmp_path / "summary.md").read_text()
    else:
        assert proc.returncode != 0
        assert "RELEASE_V4_REQUIRED_CHECK_MISSING_FOREIGN_OR_FAILED" in proc.stderr


def test_required_checks_can_span_pages_without_argv_overflow(tmp_path):
    proc = _run(tmp_path, "page2")
    assert proc.returncode == 0, proc.stderr


def test_latest_success_wins_by_check_id_not_response_order(tmp_path):
    proc = _run(tmp_path, "latest")
    assert proc.returncode == 0, proc.stderr


def test_ten_full_pages_fail_closed(tmp_path):
    proc = _run(tmp_path, "page_limit")
    assert proc.returncode != 0
    assert "RELEASE_CHECK_PAGE_LIMIT" in proc.stderr


def test_malformed_check_page_fails_closed(tmp_path):
    proc = _run(tmp_path, "malformed")
    assert proc.returncode != 0
    assert "RELEASE_CHECK_PAGE_INVALID" in proc.stderr


def test_failed_diagnostic_workflow_remains_diagnostic_under_three_check_policy(tmp_path):
    proc = _run(tmp_path, "workflow_failed")
    assert proc.returncode == 0, proc.stderr
    summary = (tmp_path / "summary.md").read_text()
    assert "Diagnostic workflow: wow-verify" in summary
    assert "completed|failure" in summary


def test_workflow_uses_stdin_for_large_json_accumulation():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert '--argjson old "$all_checks"' not in source
    assert "printf '%s\\n%s\\n' \"$all_checks\" \"$page_checks\"" in source
