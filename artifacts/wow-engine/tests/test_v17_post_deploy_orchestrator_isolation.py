from pathlib import Path

import os
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-post-deploy-verification-orchestrator.yml"


def _load():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_post_deploy_heavy_lanes_are_serialized_but_failure_independent():
    jobs = _load()["jobs"]
    assert jobs["spread-certification"]["needs"] == "spread-forward"
    assert jobs["priority-props"]["needs"] == ["spread-forward", "spread-certification"]
    assert jobs["golden-full-slate"]["needs"] == "priority-props"
    assert "always()" in jobs["priority-props"]["if"]
    assert "always()" in jobs["golden-full-slate"]["if"]


def test_post_deploy_receipt_aggregator_observes_all_lanes():
    jobs = _load()["jobs"]
    assert jobs["acceptance-receipts"]["needs"] == [
        "spread-forward",
        "spread-certification",
        "priority-props",
        "golden-full-slate",
    ]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Serialized domain-isolated post-deploy acceptance" in text
    assert "V17_TERMINAL_REDUCER" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text


def test_spread_certification_stays_within_spread_domain():
    jobs = _load()["jobs"]
    condition = jobs["spread-certification"]["if"]
    assert "always()" in condition
    assert "needs.spread-forward.result == 'success'" in condition


def test_unrelated_lane_failure_cannot_suppress_later_acceptance():
    jobs = _load()["jobs"]
    assert "always()" in jobs["priority-props"]["if"]
    assert "always()" in jobs["golden-full-slate"]["if"]


def _receipt_script() -> str:
    jobs = _load()["jobs"]
    return jobs["acceptance-receipts"]["steps"][0]["run"]


@pytest.mark.parametrize(
    ("event_name", "spread", "cert", "props", "golden", "expected"),
    [
        ("workflow_run", "success", "success", "success", "success", 0),
        ("workflow_run", "success", "success", "success", "skipped", 1),
        ("workflow_run", "success", "skipped", "success", "success", 1),
        ("workflow_run", "failure", "skipped", "success", "success", 1),
        ("workflow_dispatch", "success", "success", "success", "skipped", 0),
        ("workflow_dispatch", "success", "success", "skipped", "skipped", 1),
        ("workflow_dispatch", "success", "success", "success", "failure", 1),
    ],
)
def test_receipt_aggregator_fail_closed_behavior(
    tmp_path, event_name, spread, cert, props, golden, expected
):
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "WOW_CAN_EXECUTE": "false",
        "WOW_DRY_RUN_ONLY": "true",
        "SPREAD_FORWARD": spread,
        "SPREAD_CERTIFICATION": cert,
        "PRIORITY_PROPS": props,
        "GOLDEN_FULL_SLATE": golden,
        "ORCHESTRATOR_EVENT_NAME": event_name,
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    completed = subprocess.run(
        ["bash", "-c", _receipt_script()],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == expected
