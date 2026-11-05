"""Regression: a ready source is not a worker admission or a completed App PR."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-auto-repair.yml"
SHA = "a" * 40


def _steps():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["adopt-reviewed-owner-source"]["steps"]


def _step(name: str):
    return next(step for step in _steps() if step["name"] == name)


def test_worker_markers_follow_real_guard_and_verified_app_result():
    write = _step("Re-propose immutable source through authenticated Engineering App")
    script = write["run"]
    assert write["id"] == "propose"
    assert script.index("engineering_targeted_adoption_guard.py") < script.index(
        "wow-writer-admitted.marker"
    ) < script.index("engineering_authorized_adoption.py integrate")
    assert script.index("required_owner_approval_head == .sha") < script.index(
        "wow-writer-completed.marker"
    )
    assert subprocess.run(
        ["bash", "-n"], input=script, text=True, capture_output=True, check=False,
    ).returncode == 0


def test_receipts_share_the_existing_worker_and_never_grant_release():
    steps = _steps()
    receipt = _step("Record actual writer admission and completion")
    upload = _step("Upload actual writer receipt")
    assert receipt["if"] == "always()"
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["if-no-files-found"] == "warn"
    script = receipt["run"]
    assert "independent_approval: false" in script
    assert "production_verified: false" in script
    assert "can_execute: false" in script
    assert "writer_completed: $completed" in script
    assert len([s for s in steps if s.get("id") == "propose"]) == 1
    assert subprocess.run(
        ["bash", "-n"], input=script, text=True, capture_output=True, check=False,
    ).returncode == 0


@pytest.mark.parametrize(
    "admitted,completed,expected_stage",
    [
        (False, False, "NOT_ADMITTED"),
        (True, False, "ADMITTED_NOT_COMPLETED"),
        (True, True, "APP_PR_VERIFIED"),
    ],
)
def test_writer_receipt_counts_only_observed_markers(
    tmp_path: Path, admitted: bool, completed: bool, expected_stage: str,
):
    if admitted:
        (tmp_path / "wow-writer-admitted.marker").write_text("admitted\n")
    if completed:
        (tmp_path / "wow-writer-completed.marker").write_text("completed\n")
        (tmp_path / "wow-app-adoption-result.json").write_text(
            json.dumps({"pr": 2460, "sha": SHA}), encoding="utf-8"
        )

    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "2",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary),
        "SOURCE_PR": "2459",
        "SOURCE_SHA": SHA,
        "PROPOSE_OUTCOME": "success" if completed else "failure",
    }
    result = subprocess.run(
        ["bash", "-e"], input=_step("Record actual writer admission and completion")["run"],
        text=True, capture_output=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((tmp_path / "wow-writer-receipt.json").read_text())
    assert receipt["writer_admitted"] is admitted
    assert receipt["writer_completed"] is completed
    assert receipt["writer_stage"] == expected_stage
    assert receipt["integration_pr"] == (
        {"pr": 2460, "sha": SHA} if completed else None
    )
    assert receipt["can_execute"] is False
    assert receipt["independent_approval"] is False
    assert receipt["production_verified"] is False
    assert "actual writer receipt" in summary.read_text()


def test_preflight_exposes_review_capacity_reason_without_claiming_admission():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    source = next(
        step for step in workflow["jobs"]["adoption-readonly-preflight"]["steps"]
        if step.get("id") == "source"
    )
    script = source["run"]
    assert "Selection detail: " in script
    assert '{status, detail, pr_number, source_sha, can_execute}' in script
    assert "Writer jobs admitted | 0 (not yet admitted at preflight)" in script
    assert "Writer jobs completed | 0 (not yet completed at preflight)" in script
    assert subprocess.run(
        ["bash", "-n"], input=script, text=True, capture_output=True, check=False,
    ).returncode == 0
