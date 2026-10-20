"""Regression tests for governance issue #1899 (jq ARG_MAX in exact-head evidence capture).

The workflow shell is executed against a fake ``gh`` binary. No network, secrets, or GitHub API.
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

ROOT = Path(os.getenv("WOW_REPO_ROOT", Path(__file__).resolve().parents[2]))
WORKFLOW = ROOT / ".github/workflows/wow-v17-existing-pr-governance-review.yml"
FILTER = 'if (length == 2 and all(.[]; type == "array")) then .[0] + .[1] else error("EXISTING_PR_API_ARRAY_INVALID") end'
ARG_STRLEN = 128 * 1024
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
HEAD = "a" * 40
REQUIRED = [
    ("wow-verify", ".github/workflows/wow-verify.yml"),
    ("wow-engine-verify", ".github/workflows/wow-engine-verify.yml"),
    ("wow-v17-rapid-repair", ".github/workflows/wow-v17-rapid-repair.yml"),
    ("wow-v17-change-impact-gate", ".github/workflows/wow-v17-change-impact-gate.yml"),
    ("wow-v17-engineering-auditor-code-health", ".github/workflows/wow-v17-engineering-auditor-code-health.yml"),
    ("wow-v17-spread-forward-shadow", ".github/workflows/wow-v17-spread-forward-shadow.yml"),
    ("wow-v17-release-production-verification-agent", ".github/workflows/wow-v17-release-production-verification-agent.yml"),
]

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from urllib.parse import urlsplit, parse_qs
args = sys.argv[1:]
assert args[0] == "api", args
endpoint = args[1]
fixture = json.load(open(os.environ["FAKE_GH_FIXTURE"]))
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(endpoint + "\n")
parts = urlsplit(endpoint)
route = fixture["routes"].get(parts.path)
if route is None:
    sys.exit("fake gh: unknown route " + parts.path)
if "raw" in route:
    body = route["raw"]
elif "object" in route:
    body = json.dumps(route["object"])
else:
    query = parse_qs(parts.query)
    page, per_page = int(query["page"][0]), int(query["per_page"][0])
    if "head_sha" in route and query.get("head_sha", [""])[0] != route["head_sha"]:
        items = []
    else:
        items = route["items"][(page - 1) * per_page: page * per_page]
    body = json.dumps({route["wrap"]: items} if route.get("wrap") else items)
if "--jq" in args:
    body = subprocess.run(["jq", "-r", args[args.index("--jq") + 1]], input=body, capture_output=True, text=True, check=True).stdout
sys.stdout.write(body)
'''


def _steps():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {step.get("name"): step for job in workflow["jobs"].values() for step in job["steps"]}


def _api_array_functions():
    functions = []
    for step in _steps().values():
        match = re.search(r"^( *)api_array\(\) \{\n.*?^\1\}\n", step.get("run", ""), re.S | re.M)
        if match:
            functions.append(match.group(0))
    return functions


class FakeGitHub:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        sleep = self.bin / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for path in (gh, sleep):
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.fixture = tmp_path / "fixture.json"
        self.log = tmp_path / "gh.log"
        self.work = tmp_path / "work"
        (self.work / ".governance-review-evidence").mkdir(parents=True)
        self.runner_temp = tmp_path / "runner"
        self.runner_temp.mkdir()
        self.output = tmp_path / "github_output"
        self.routes: dict = {}

    def run(self, script: str, extra_env: dict | None = None, args: list[str] | None = None):
        self.fixture.write_text(json.dumps({"routes": self.routes}), encoding="utf-8")
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_FIXTURE": str(self.fixture),
            "FAKE_GH_LOG": str(self.log),
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_OUTPUT": str(self.output),
            "RUNNER_TEMP": str(self.runner_temp),
            "HEAD_SHA": HEAD,
            "PR_NUMBER": "1899",
            **(extra_env or {}),
        }
        return subprocess.run(["bash", "-c", script, "bash", *(args or [])], cwd=self.work, env=env, capture_output=True, text=True, timeout=300)


def _padded(i: int, **fields) -> dict:
    return {"id": i, "pad": "x" * 400, **fields}


def test_all_three_collectors_are_stdin_only_and_fail_closed():
    body = WORKFLOW.read_text(encoding="utf-8")
    assert body.count("jq -cse " + "'" + FILTER + "'") == 3
    assert body.count("printf '%s\\n%s\\n' \"$accumulated\" \"$items\"") == 3
    assert len(_api_array_functions()) == 3
    assert '--argjson current "$accumulated"' not in body


def test_no_unbounded_json_is_passed_through_process_arguments():
    body = WORKFLOW.read_text(encoding="utf-8")
    # The only remaining --argjson binds a small integer GitHub App id.
    assert re.findall(r"--argjson \w+ \"[^\"]*\"", body) == ['--argjson app "$app_id"']
    assert '"$(cat ' not in body
    assert '--slurpfile workflow_runs ".governance-review-evidence/workflow-runs.json"' in body
    assert "EXISTING_PR_EVIDENCE_INVALID" in body
    assert "EXISTING_PR_BOOTSTRAP_EVIDENCE_INVALID" in body


def test_seven_required_workflow_identities_and_paths_are_unchanged():
    run = _steps()["Verify exact-head deterministic CI"]["run"]
    block = re.search(r"required=\(\n(.*?)\n\s*\)", run, re.S).group(1)
    specs = [line.strip().strip('"') for line in block.splitlines()]
    assert specs == [f"{name}|{path}" for name, path in REQUIRED]


@needs_jq
@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_collector_preserves_large_paginated_history_in_order(tmp_path, index):
    gh = FakeGitHub(tmp_path)
    items = [_padded(i) for i in range(950)]
    assert len(json.dumps(items)) > ARG_STRLEN
    gh.routes["/r"] = {"wrap": "check_runs", "items": items}
    sep = "&" if index == 0 else "?"
    endpoint = "/r?head_sha=x" if index == 0 else "/r"
    script = "set -euo pipefail\n" + _api_array_functions()[index] + f'api_array "{endpoint}" ".check_runs" > "$1"\n'
    out = tmp_path / "out.json"
    result = gh.run(script, args=[str(out)])
    assert result.returncode == 0, result.stderr
    assert "Argument list too long" not in result.stderr
    assert [row["id"] for row in json.loads(out.read_text())] == list(range(950))
    assert gh.log.read_text().count(f"{sep}per_page=100&page=") == 10


@needs_jq
@pytest.mark.parametrize("index", [0, 1, 2])
def test_each_collector_enforces_the_inventory_bound(tmp_path, index):
    gh = FakeGitHub(tmp_path)
    gh.routes["/r"] = {"wrap": "check_runs", "items": [{"id": i} for i in range(1000)]}
    endpoint = "/r?head_sha=x" if index == 0 else "/r"
    result = gh.run("set -euo pipefail\n" + _api_array_functions()[index] + f'api_array "{endpoint}" ".check_runs"\n')
    assert result.returncode != 0
    assert "EXISTING_PR_API_INVENTORY_LIMIT_EXCEEDED" in result.stderr


@needs_jq
@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("raw", ["", "{not-json", '{"check_runs": {}}', '{"check_runs": null}', '{"check_runs": false}', "{}"])
def test_each_collector_rejects_invalid_or_missing_pages(tmp_path, index, raw):
    gh = FakeGitHub(tmp_path)
    gh.routes["/r"] = {"raw": raw}
    endpoint = "/r?head_sha=x" if index == 0 else "/r"
    result = gh.run("set -euo pipefail\n" + _api_array_functions()[index] + f'api_array "{endpoint}" ".check_runs"\n')
    assert result.returncode != 0
    assert result.stdout == ""


@needs_jq
def test_accumulator_rejects_non_array_inputs_directly():
    for a, b in [("{not-json", "[]"), ("{}", "[]"), ("[]", "false"), ("[1]", "null"), ("[]", ""), ("", "")]:
        result = subprocess.run(["jq", "-cse", FILTER], input=a + "\n" + b + "\n", capture_output=True, text=True)
        assert result.returncode != 0, (a, b)


def _ci_runs(conclusions: dict[str, str], *, path_override: dict[str, str] | None = None, padding: int = 300) -> list[dict]:
    runs = [_padded(10_000 + i, name="unrelated", path=".github/workflows/unrelated.yml", status="completed", conclusion="success", created_at="2026-10-20T00:00:00Z") for i in range(padding)]
    for i, (name, path) in enumerate(REQUIRED):
        if name not in conclusions:
            continue
        status, conclusion = ("completed", conclusions[name]) if conclusions[name] != "in_progress" else ("in_progress", None)
        runs.append(_padded(i, name=name, path=(path_override or {}).get(name, path), status=status, conclusion=conclusion, created_at="2026-10-20T01:00:00Z"))
    return runs


def _run_ci_step(gh: FakeGitHub, runs: list[dict], head_sha: str = HEAD):
    gh.routes[f"/repos/{REPO}/actions/runs"] = {"wrap": "workflow_runs", "head_sha": head_sha, "items": runs}
    return gh.run(_steps()["Verify exact-head deterministic CI"]["run"])


@needs_jq
def test_ci_step_accepts_large_exact_head_history_and_records_it(tmp_path):
    gh = FakeGitHub(tmp_path)
    runs = _ci_runs({name: "success" for name, _ in REQUIRED})
    assert len(json.dumps(runs)) > ARG_STRLEN
    result = _run_ci_step(gh, runs)
    assert result.returncode == 0, result.stderr
    recorded = json.loads((gh.work / ".governance-review-evidence/workflow-runs.json").read_text())
    assert [row["id"] for row in recorded] == [row["id"] for row in runs]
    assert "status=0" in gh.output.read_text()
    assert all(f"head_sha={HEAD}&" in line for line in gh.log.read_text().splitlines())


@needs_jq
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required"])
def test_ci_step_fails_closed_on_failed_required_workflow(tmp_path, conclusion):
    gh = FakeGitHub(tmp_path)
    conclusions = {name: "success" for name, _ in REQUIRED}
    conclusions["wow-engine-verify"] = conclusion
    result = _run_ci_step(gh, _ci_runs(conclusions))
    assert result.returncode != 0
    assert "EXISTING_PR_REQUIRED_CI_FAILED:wow-engine-verify" in result.stderr
    assert not (gh.work / ".governance-review-evidence/workflow-runs.json").exists()


@needs_jq
@pytest.mark.parametrize(
    "case",
    ["absent", "in_progress", "wrong_path", "stale_head"],
)
def test_ci_step_fails_closed_when_required_evidence_is_missing_or_stale(tmp_path, case):
    gh = FakeGitHub(tmp_path)
    conclusions = {name: "success" for name, _ in REQUIRED}
    path_override = None
    head = HEAD
    if case == "absent":
        del conclusions["wow-v17-spread-forward-shadow"]
    elif case == "in_progress":
        conclusions["wow-v17-spread-forward-shadow"] = "in_progress"
    elif case == "wrong_path":
        path_override = {"wow-v17-spread-forward-shadow": ".github/workflows/impostor.yml"}
    else:
        head = "b" * 40
    result = _run_ci_step(gh, _ci_runs(conclusions, path_override=path_override, padding=5), head_sha=head)
    assert result.returncode != 0
    assert "EXISTING_PR_REQUIRED_CI_INCOMPLETE_AFTER_BOUNDED_WAIT" in result.stderr
    assert not (gh.work / ".governance-review-evidence/workflow-runs.json").exists()


def _evidence_routes(gh: FakeGitHub, *, pr_head: str = HEAD, checks: int = 400):
    api = f"/repos/{REPO}"
    pr = {"state": "open", "draft": False, "head": {"sha": pr_head, "repo": {"full_name": REPO}}, "base": {"ref": "main", "repo": {"full_name": REPO}}}
    gh.routes[f"{api}/pulls/1899"] = {"object": pr}
    gh.routes[f"{api}/pulls/1899/files"] = {"items": [{"filename": f"f{i}.py", "previous_filename": None, "status": "modified", "patch": "p"} for i in range(150)]}
    gh.routes[f"{api}/pulls/1899/reviews"] = {"items": [{"id": 1, "state": "APPROVED", "commit_id": HEAD}]}
    gh.routes[f"{api}/commits/{HEAD}/check-runs"] = {"wrap": "check_runs", "items": [_padded(i, head_sha=HEAD) for i in range(checks)]}


@needs_jq
def test_evidence_assembly_handles_large_inventories_without_argv(tmp_path):
    gh = FakeGitHub(tmp_path)
    _evidence_routes(gh)
    runs = _ci_runs({name: "success" for name, _ in REQUIRED})
    (gh.work / ".governance-review-evidence/workflow-runs.json").write_text(json.dumps(runs) + "\n")
    result = gh.run(_steps()["Collect complete exact-head GitHub evidence"]["run"])
    assert result.returncode == 0, result.stderr
    evidence = json.loads((gh.work / ".governance-review-evidence/evidence.json").read_text())
    assert evidence["complete_inventory"] is True
    assert evidence["pr"]["head"]["sha"] == HEAD
    assert [f["filename"] for f in evidence["changed_files"]] == [f"f{i}.py" for i in range(150)]
    assert set(evidence["changed_files"][0]) == {"filename", "previous_filename", "status"}
    assert [c["id"] for c in evidence["check_runs"]] == list(range(400))
    assert len(json.dumps(evidence["check_runs"])) > ARG_STRLEN
    assert evidence["workflow_runs"] == runs
    assert len(evidence["reviews"]) == 1


@needs_jq
@pytest.mark.parametrize("content", [None, "", "{not-json", "{}", "[] []", "null"])
def test_evidence_assembly_rejects_missing_or_malformed_workflow_runs(tmp_path, content):
    gh = FakeGitHub(tmp_path)
    _evidence_routes(gh, checks=3)
    if content is not None:
        (gh.work / ".governance-review-evidence/workflow-runs.json").write_text(content)
    result = gh.run(_steps()["Collect complete exact-head GitHub evidence"]["run"])
    assert result.returncode != 0
    evidence = gh.work / ".governance-review-evidence/evidence.json"
    assert not evidence.exists() or evidence.read_text().strip() == ""


@needs_jq
def test_evidence_assembly_rejects_pr_whose_head_is_not_the_certified_sha(tmp_path):
    gh = FakeGitHub(tmp_path)
    _evidence_routes(gh, pr_head="c" * 40, checks=3)
    (gh.work / ".governance-review-evidence/workflow-runs.json").write_text("[]\n")
    result = gh.run(_steps()["Collect complete exact-head GitHub evidence"]["run"])
    assert result.returncode != 0
    assert "EXISTING_PR_HEAD_MOVED_OR_NOT_READY" in result.stderr
    assert not (gh.work / ".governance-review-evidence/evidence.json").exists()


@needs_jq
def test_bootstrap_merge_replaces_check_runs_from_file_and_validates(tmp_path):
    run = _steps()["Build and verify review-evidence receipt"]["run"]
    merge = re.search(r"( *)printf '%s\\n' \"\$checks\" > \"\$RUNNER_TEMP/wow-bootstrap-check-runs.json\"\n.*?mv \"\$\{evidence\}.tmp\" \"\$evidence\"\n", run, re.S).group(0)
    gh = FakeGitHub(tmp_path)
    evidence = gh.work / "evidence.json"
    script = 'set -euo pipefail\nevidence="$1"\nchecks=$(cat "$2")\n' + merge
    big = [_padded(i) for i in range(400)]
    checks_file = tmp_path / "checks.json"
    checks_file.write_text(json.dumps(big))
    evidence.write_text(json.dumps({"complete_inventory": True, "check_runs": [], "workflow_runs": [1]}))
    result = gh.run(script, args=[str(evidence), str(checks_file)])
    assert result.returncode == 0, result.stderr
    merged = json.loads(evidence.read_text())
    assert merged["check_runs"] == big and merged["workflow_runs"] == [1]
    assert not (gh.runner_temp / "wow-bootstrap-check-runs.json").exists()
    for bad in ("{}", "null", "{not-json"):
        evidence.write_text(json.dumps({"check_runs": []}))
        checks_file.write_text(bad)
        result = gh.run(script, args=[str(evidence), str(checks_file)])
        assert result.returncode != 0, bad
        assert json.loads(evidence.read_text()) == {"check_runs": []}
