"""Execute the protected certifier's Bash/JQ with offline, oversized evidence."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-existing-pr-governance-review.yml"
STEPS = {
    step["name"]: step.get("run", "")
    for step in yaml.safe_load(WORKFLOW.read_text())["jobs"]["certify-existing-pr"]["steps"]
}
CI = STEPS["Verify exact-head deterministic CI"]
CAPTURE = STEPS["Collect complete exact-head GitHub evidence"]
RECEIPT = STEPS["Build and verify review-evidence receipt"]
COLLECTORS = [
    re.search(r"(?ms)^(\s*)api_array\(\) \{.*?^\1\}", source).group(0)
    for source in (CI, CAPTURE, RECEIPT)
]
SHA = "a" * 40
REQUIRED = [
    "WOW required-three regression",
    "WOW governed probability backend",
    "WOW additional required regression",
]
MOCK_PAGES = """
gh() {
  printf '%s\\n' "$*" >> calls
  local page="${2##*page=}"
  cat "pages/$page.json"
}
"""


def _run(source, tmp_path, prelude=""):
    assert shutil.which("bash") and shutil.which("jq"), "GitHub runner tools required"
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", prelude + "\n" + source],
        cwd=tmp_path,
        env={
            **os.environ, "GITHUB_REPOSITORY": "owner/repo", "HEAD_SHA": SHA,
            "HEAD_REF": "fix/1899", "PR_NUMBER": "1900", "INCIDENT": "1899",
            "GITHUB_OUTPUT": str(tmp_path / "output"),
        },
        text=True, capture_output=True, timeout=20, check=False,
    )


def _pages(tmp_path, rows, key="items"):
    directory = tmp_path / "pages"
    directory.mkdir()
    for page, start in enumerate(range(0, len(rows) + 1, 100), 1):
        chunk = rows[start:start + 100]
        (directory / f"{page}.json").write_text(
            json.dumps({key: chunk} if key else chunk)
        )


@pytest.mark.parametrize("index", range(3))
def test_all_collectors_conserve_large_arrays_in_order(index, tmp_path):
    rows = [{"id": i, "padding": "x" * 1500} for i in range(901)]
    assert len(json.dumps(rows[:100])) > 128 * 1024
    _pages(tmp_path, rows)
    result = _run(
        COLLECTORS[index] + '\nresult=$(api_array "/api?test=1" ".items")\nprintf "%s" "$result"',
        tmp_path, MOCK_PAGES,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == rows
    assert len((tmp_path / "calls").read_text().splitlines()) == 10


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("invocation", ["direct", "substitution"])
@pytest.mark.parametrize("payload", [
    '{"items":null}', '{"items":{}}', '{"items":"bad"}', '{"items":false}',
    '{"items":true}', '{"items":1}', '{"items":[', '{"items":[]}\n{"items":[]}',
])
def test_all_collectors_fail_closed_on_invalid_pages(index, invocation, payload, tmp_path):
    _pages(tmp_path, [])
    (tmp_path / "pages/1.json").write_text(payload)
    command = 'api_array "/api?test=1" ".items"'
    if invocation == "substitution":
        command = f"result=$({command})"
    result = _run(
        COLLECTORS[index] + "\n" + command + "\necho ACCEPTED",
        tmp_path, MOCK_PAGES,
    )
    assert result.returncode != 0
    assert "EXISTING_PR_API_ARRAY_INVALID" in result.stderr
    assert "ACCEPTED" not in result.stdout
    assert len((tmp_path / "calls").read_text().splitlines()) == 1


@pytest.mark.parametrize("index", range(3))
def test_all_collectors_bound_pagination_and_reject_truncation(index, tmp_path):
    _pages(tmp_path, list(range(1100)))
    result = _run(
        COLLECTORS[index] + '\nresult=$(api_array "/api?test=1" ".items")\necho ACCEPTED',
        tmp_path, MOCK_PAGES,
    )
    assert result.returncode != 0
    assert "EXISTING_PR_API_INVENTORY_LIMIT_EXCEEDED" in result.stderr
    assert "ACCEPTED" not in result.stdout
    calls = (tmp_path / "calls").read_text().splitlines()
    assert len(calls) == 10
    assert calls[-1].endswith("per_page=100&page=10")


@pytest.mark.parametrize("index", range(3))
def test_all_collectors_propagate_api_failure(index, tmp_path):
    result = _run(
        COLLECTORS[index] + '\nresult=$(api_array "/api?test=1" ".items")\necho ACCEPTED',
        tmp_path, "gh() { return 1; }",
    )
    assert result.returncode != 0
    assert "ACCEPTED" not in result.stdout


@pytest.mark.parametrize("name", REQUIRED)
@pytest.mark.parametrize("state", ["success", "absent", "cancelled", "failure", "wrong_sha", "wrong_app"])
def test_three_native_checks_remain_exact_head_and_fail_closed(name, state, tmp_path):
    rows = [
        {"id": index + 1, "name": required, "app": {"id": 15368},
         "head_sha": SHA, "started_at": "2026-10-20T00:00:00Z",
         "status": "completed", "conclusion": "success"}
        for index, required in enumerate(REQUIRED)
    ]
    selected = next(row for row in rows if row["name"] == name)
    if state == "absent":
        rows.remove(selected)
    elif state == "wrong_sha":
        selected["head_sha"] = "b" * 40
    elif state == "wrong_app":
        selected["app"]["id"] = 5245832
    else:
        selected["conclusion"] = state
    rows = [{"name": "unrelated", "padding": "x" * 2000} for _ in range(101)] + rows
    _pages(tmp_path, rows, "check_runs")
    (tmp_path / ".governance-review-evidence").mkdir()
    result = _run(CI, tmp_path, MOCK_PAGES + '\nsleep() { :; }\nseq() { echo 1; }\n')
    calls = (tmp_path / "calls").read_text().splitlines()
    assert all(f"/commits/{SHA}/check-runs?per_page=100&page=" in call for call in calls)
    if state == "success":
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "output").read_text() == "status=0\n"
    else:
        assert result.returncode != 0
        expected = ("EXISTING_PR_REQUIRED_CI_FAILED" if state in ("cancelled", "failure")
                    else "EXISTING_PR_REQUIRED_CI_INCOMPLETE_AFTER_BOUNDED_WAIT")
        assert expected in result.stderr
        assert not (tmp_path / "output").exists()


def _evidence_files(tmp_path):
    directory = tmp_path / ".governance-review-evidence"
    directory.mkdir()
    payload = [{"id": i, "padding": "x" * 2000} for i in range(101)]
    pr = {
        "state": "open", "draft": False,
        "head": {"sha": SHA, "repo": {"full_name": "owner/repo"}},
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
        "body": "x" * (140 * 1024),
    }
    for name, value in (("pr", pr), ("changed-files", payload), ("reviews", payload),
                        ("check-runs", payload), ("workflow-runs", payload)):
        (directory / f"{name}.json").write_text(json.dumps(value))
    return directory, pr, payload


@pytest.mark.parametrize("invalid", [None, "pr", "changed-files", "reviews", "check-runs",
                                   "workflow-runs", "empty", "multiple", "malformed"])
def test_evidence_assembly_uses_validated_files_not_argv(invalid, tmp_path):
    directory, pr, payload = _evidence_files(tmp_path)
    if invalid:
        name = invalid if invalid in ("pr", "changed-files", "reviews", "check-runs") else "workflow-runs"
        replacement = {"empty": "", "multiple": "[]\n[]", "malformed": "["}.get(
            invalid, "[]" if invalid == "pr" else "{}"
        )
        (directory / f"{name}.json").write_text(replacement)
    assembly = CAPTURE[CAPTURE.index("jq -n"):]
    result = _run(assembly, tmp_path)
    if invalid:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads((directory / "evidence.json").read_text()) == {
            "complete_inventory": True, "pr": pr, "changed_files": payload,
            "reviews": payload, "check_runs": payload, "workflow_runs": payload,
        }


@pytest.mark.parametrize("invalid", [
    None, "evidence", "checks", "empty", "multiple", "malformed",
    "checks_empty", "checks_multiple", "checks_malformed",
])
def test_bootstrap_merge_uses_validated_files_not_argv(invalid, tmp_path):
    directory, pr, payload = _evidence_files(tmp_path)
    evidence = {"complete_inventory": True, "pr": pr, "reviews": payload, "check_runs": []}
    path = directory / "evidence.json"
    path.write_text(json.dumps(evidence))
    replacement = {"empty": "", "multiple": "{}\n{}", "malformed": "["}
    checks = {
        "checks": "{}", "checks_empty": "", "checks_multiple": "[]\n[]",
        "checks_malformed": "[",
    }.get(invalid, json.dumps(payload))
    (directory / "bootstrap-check-runs.json").write_text(checks)
    if invalid and not invalid.startswith("checks"):
        path.write_text(replacement.get(invalid, "[]"))
    original = path.read_text()
    start = RECEIPT.index("jq -n")
    end = RECEIPT.index('mv "${evidence}.tmp" "$evidence"', start) + len('mv "${evidence}.tmp" "$evidence"')
    result = _run(
        'evidence=".governance-review-evidence/evidence.json"\n' + RECEIPT[start:end],
        tmp_path,
        'checks=$(cat ".governance-review-evidence/bootstrap-check-runs.json")',
    )
    if invalid:
        assert result.returncode != 0
        assert path.read_text() == original
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(path.read_text()) == {**evidence, "check_runs": payload}


@pytest.mark.parametrize("state", ["exact", "stale", "moved", "draft"])
def test_full_capture_preserves_large_evidence_and_rejects_head_drift(state, tmp_path):
    directory, pr, payload = _evidence_files(tmp_path)
    files = [
        {"filename": f"{i}-" + "x" * 1500, "previous_filename": f"old-{i}", "status": "renamed"}
        for i in range(101)
    ]
    if state == "stale":
        pr["head"]["sha"] = "b" * 40
    elif state == "draft":
        pr["draft"] = True
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name, value in (("pr", pr), ("files", files), ("reviews", payload),
                        ("check-runs", payload), ("workflow-runs", payload)):
        (inputs / f"{name}.json").write_text(json.dumps(value))
    current_sha = "b" * 40 if state == "moved" else SHA
    mock = """
gh() {
  printf '%s\\n' "$*" >> calls
  local page="${2##*page=}"
  case "$2" in
    */files\\?*) jq --argjson p "$page" '.[($p-1)*100:$p*100]' inputs/files.json ;;
    */reviews\\?*) jq --argjson p "$page" '.[($p-1)*100:$p*100]' inputs/reviews.json ;;
    */check-runs\\?*) jq --argjson p "$page" '{check_runs:.[($p-1)*100:$p*100]}' inputs/check-runs.json ;;
    */actions/runs\\?*) jq --argjson p "$page" '{workflow_runs:.[($p-1)*100:$p*100]}' inputs/workflow-runs.json ;;
    *) if [ "${3:-}" = "--jq" ]; then echo CURRENT_SHA; else cat inputs/pr.json; fi ;;
  esac
}
""".replace("CURRENT_SHA", current_sha)
    result = _run(CAPTURE, tmp_path, mock)
    if state == "exact":
        assert result.returncode == 0, result.stderr
        assert json.loads((directory / "evidence.json").read_text()) == {
            "complete_inventory": True, "pr": pr, "changed_files": files,
            "reviews": payload, "check_runs": payload, "workflow_runs": payload,
        }
        assert f"/commits/{SHA}/check-runs?" in (tmp_path / "calls").read_text()
    else:
        assert result.returncode != 0
        expected = ("EXISTING_PR_HEAD_MOVED_DURING_CAPTURE" if state == "moved"
                    else "EXISTING_PR_HEAD_MOVED_OR_NOT_READY")
        assert expected in result.stderr
        assert not (directory / "evidence.json").exists()


@pytest.mark.parametrize("state", ["success", "absent", "cancelled", "failure", "wrong_sha", "wrong_app"])
def test_bootstrap_checks_keep_exact_head_and_app_authority(state, tmp_path):
    directory, pr, payload = _evidence_files(tmp_path)
    evidence = {"pr": pr, "check_runs": [], "reviews": payload}
    path = directory / "evidence.json"
    path.write_text(json.dumps(evidence))
    specifications = [
        ("WOW required-three regression", 15368),
        ("WOW governed probability backend", 15368),
        ("WOW additional required regression", 15368),
    ]
    checks = [
        {"name": name, "app": {"id": app}, "head_sha": SHA, "status": "completed",
         "conclusion": "success", "started_at": "2026-10-20T00:00:00Z", "id": i}
        for i, (name, app) in enumerate(specifications)
    ]
    if state == "absent":
        checks.pop()
    elif state == "wrong_sha":
        checks[-1]["head_sha"] = "b" * 40
    elif state == "wrong_app":
        checks[-1]["app"]["id"] = 5245832
    else:
        checks[-1]["conclusion"] = state
    checks = [{"name": "unrelated", "padding": "x" * 2000} for _ in range(101)] + checks
    _pages(tmp_path, checks, "check_runs")
    start = RECEIPT.index('if [ "$classification" = "TRUST_ROOT_BOOTSTRAP" ]; then')
    end = RECEIPT.index('\nreceipt=', start)
    result = _run(
        'classification=TRUST_ROOT_BOOTSTRAP\nevidence=".governance-review-evidence/evidence.json"\n'
        + RECEIPT[start:end],
        tmp_path, MOCK_PAGES + '\nsleep() { :; }\nseq() { echo 1; }\n',
    )
    assert all(f"/commits/{SHA}/check-runs?" in call
               for call in (tmp_path / "calls").read_text().splitlines())
    if state == "success":
        assert result.returncode == 0, result.stderr
        assert json.loads(path.read_text()) == {**evidence, "check_runs": checks}
    else:
        assert result.returncode != 0
        expected = ("EXISTING_PR_BOOTSTRAP_CHECK_FAILED" if state in ("cancelled", "failure")
                    else "EXISTING_PR_BOOTSTRAP_CHECKS_INCOMPLETE")
        assert expected in result.stderr
        assert json.loads(path.read_text()) == evidence


def test_large_evidence_never_crosses_argv_boundary():
    text = WORKFLOW.read_text()
    for name in ("current", "next", "pr", "changed_files", "reviews",
                 "check_runs", "workflow_runs", "evidence"):
        assert f"--argjson {name} " not in text
    required = re.search(r"required=\(\s*(.*?)\s*\)", CI, re.S).group(1)
    assert re.findall(r'"([^"]+)"', required) == [f"{name}|15368" for name in REQUIRED]
    assert 'select(.name == $name and (.app.id == $app) and .head_sha == $sha)' in RECEIPT
    assert re.findall(r'"([^"]+\|\d+)"', RECEIPT) == [
        "WOW required-three regression|15368",
        "WOW governed probability backend|15368",
        "WOW additional required regression|15368",
    ]
