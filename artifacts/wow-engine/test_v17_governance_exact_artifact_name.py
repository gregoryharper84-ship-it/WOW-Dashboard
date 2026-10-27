"""Exact-head protected-governance artifact inventory: no global scan and no false-green.

A deliberately adversarial bounded shell replay of the actual workflow step.
No credentials, governance bypass, or production writes.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-existing-pr-governance-review.yml"
HEAD = "b" * 40
NAME = f"wow-v17-engineering-governance-{HEAD}"


def _script() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    step = text.split("      - name: Check for an existing exact-head receipt\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    assert "artifacts?name=${artifact_name}&per_page=100&page=1" in script
    assert 'for page in $(seq 1 20)' not in script
    return script


@pytest.mark.skipif(shutil.which("jq") is None, reason="GitHub runner has jq")
@pytest.mark.parametrize("inventory,total,expected_return,expected_status", [
    ([], 0, 0, "exists=false"),
    ([{"name": NAME, "id": 4, "expired": True}], 1, 0, "exists=false"),
    ([{"name": NAME, "id": 5, "expired": False}], 1, 0, "exists=true"),
    ([{"name": NAME, "id": 5, "expired": False},
      {"name": NAME, "id": 6, "expired": False}], 2, 1, "EXISTING_PR_RECEIPT_ARTIFACT_DUPLICATE"),
    ([{"name": NAME + "-wrong", "id": 5, "expired": False}], 1, 1, "EXISTING_PR_RECEIPT_ARTIFACT_INVENTORY_INVALID"),
    ([{"name": NAME, "id": 5}], 1, 1, "EXISTING_PR_RECEIPT_ARTIFACT_INVENTORY_INVALID"),
    ([{"name": NAME, "id": 5, "expired": False}], 2, 1, "EXISTING_PR_RECEIPT_ARTIFACT_INVENTORY_INVALID"),
    ([], 101, 1, "EXISTING_PR_RECEIPT_ARTIFACT_INVENTORY_LIMIT_EXCEEDED"),
])
def test_exact_name_filter_replays_fail_closed_inventory(tmp_path, inventory, total, expected_return, expected_status):
    script = _script()
    stub = r'''
gh() {
  [ "$1" = "api" ] || return 1
  [ "$2" = "/repos/${GITHUB_REPOSITORY}/actions/artifacts?name=wow-v17-engineering-governance-${HEAD_SHA}&per_page=100&page=1" ] || return 1
  printf '%s\n' "$FAKE_ARTIFACTS"
}
'''
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    res = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", stub + script],
                         env=os.environ | {
                             "HEAD_SHA": HEAD, "GITHUB_REPOSITORY": "gregoryharper84-ship-it/WOW-Dashboard",
                             "GITHUB_OUTPUT": str(output),
                             "FAKE_ARTIFACTS": json.dumps({"total_count": total, "artifacts": inventory}),
                         }, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert (res.returncode == 0) == (expected_return == 0), (res.stderr, res.stdout)
    assert expected_status in (output.read_text() if expected_return == 0 else res.stderr)


def test_reconciled_source_preserves_main_artifact_fix_and_release_event_filter():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/artifacts?name=${artifact_name}&per_page=100&page=1" in workflow
    assert "actions/artifacts?per_page=100&page=" not in workflow
    section = workflow.split("  certify-existing-pr:\n", 1)[1].split("    runs-on: ubuntu-latest", 1)[0]
    assert "github.event_name != 'check_run'" in section
    assert "github.event.check_run.app.id == 5245853" in section
    assert "github.event.check_run.name == 'WOW Release Authority exact-head'" in section
    assert "github.event.check_run.conclusion == 'success'" in section
