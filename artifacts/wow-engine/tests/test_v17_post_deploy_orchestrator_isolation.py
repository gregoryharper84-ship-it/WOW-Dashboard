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
    assert jobs["spread-forward"]["needs"] == "deployment-readback"
    assert jobs["spread-certification"]["needs"] == ["deployment-readback", "spread-forward"]
    assert jobs["priority-props"]["needs"] == ["deployment-readback", "spread-forward", "spread-certification"]
    assert jobs["golden-full-slate"]["needs"] == ["deployment-readback", "priority-props"]
    assert "always()" in jobs["priority-props"]["if"]
    assert "always()" in jobs["golden-full-slate"]["if"]


def test_post_deploy_receipt_aggregator_observes_all_lanes():
    jobs = _load()["jobs"]
    assert jobs["acceptance-receipts"]["needs"] == [
        "deployment-readback",
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
        ("workflow_dispatch", "success", "success", "success", "success", 0),
        ("workflow_dispatch", "success", "success", "success", "skipped", 1),
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
        "DEPLOYMENT_READBACK": "success",
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


@pytest.mark.parametrize(("readback", "expected"), [("success", 0), ("failure", 1), ("skipped", 1)])
def test_repeat_acceptance_skip_requires_successful_fresh_readback(tmp_path, readback, expected):
    env = {
        **os.environ,
        "WOW_CAN_EXECUTE": "false",
        "WOW_DRY_RUN_ONLY": "true",
        "SPREAD_FORWARD": "skipped",
        "SPREAD_CERTIFICATION": "skipped",
        "PRIORITY_PROPS": "skipped",
        "GOLDEN_FULL_SLATE": "skipped",
        "DEPLOYMENT_READBACK": readback,
        "ALREADY_ACCEPTED": "true",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    completed = subprocess.run(["bash", "-c", _receipt_script()], cwd=ROOT, env=env,
                               text=True, capture_output=True, check=False)
    assert completed.returncode == expected


def test_expensive_lanes_are_gated_on_not_already_accepted():
    jobs = _load()["jobs"]
    for name in ("spread-forward", "spread-certification", "priority-props", "golden-full-slate"):
        assert "already_accepted != 'true'" in jobs[name]["if"]


def test_post_deploy_native_trigger_and_all_lane_admission_require_direct_readback():
    workflow = _load()
    assert workflow["on"]["workflow_run"]["workflows"] == ["wow-v17-lean-production-readback"]
    job = workflow["jobs"]["deployment-readback"]
    assert job["environment"] == "wow-release"
    assert job["env"]["SOURCE_RUN_KIND"] == "production_observer"
    assert "RENDER_API_KEY" in job["env"]
    step = next(s for s in job["steps"] if s.get("id") == "readback")
    assert "lean_production_readback.py" in step["run"]
    assert "continue-on-error" not in step
    for name in ("spread-forward", "spread-certification", "priority-props", "golden-full-slate"):
        assert "needs.deployment-readback.result == 'success'" in workflow["jobs"][name]["if"]


def test_missing_deployment_readback_fails_receipt_even_when_all_lanes_claim_success(tmp_path):
    env = {**os.environ, "WOW_CAN_EXECUTE": "false", "WOW_DRY_RUN_ONLY": "true",
           "SPREAD_FORWARD": "success", "SPREAD_CERTIFICATION": "success",
           "PRIORITY_PROPS": "success", "GOLDEN_FULL_SLATE": "success",
           "DEPLOYMENT_READBACK": "skipped", "ORCHESTRATOR_EVENT_NAME": "workflow_dispatch",
           "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}
    result = subprocess.run(["bash", "-c", _receipt_script()], env=env, text=True, capture_output=True)
    assert result.returncode == 1


def test_acceptance_repeats_exact_sha_readback_after_the_long_running_lanes():
    job = _load()["jobs"]["acceptance-receipts"]
    steps = job["steps"]
    final_readback = next((s for s in steps if s.get("id") == "final-readback"), None)
    assert final_readback is not None, "a deployment may change while the acceptance lanes run"
    assert "lean_production_readback.py" in final_readback["run"]
    assert "continue-on-error" not in final_readback
    assert job["environment"] == "wow-release"
    assert job["env"]["EXPECTED_SHA"] == "${{ inputs.expected_sha || github.event.workflow_run.head_sha }}"
    assert job["env"]["SOURCE_RUN_KIND"] == "production_observer"
    artifact = next(s for s in steps if "upload-artifact@" in s.get("uses", ""))
    assert artifact["with"]["if-no-files-found"] == "error"


def _dedupe():
    import importlib.util
    path = ROOT / "artifacts" / "wow-engine" / "v17" / "post_deploy_dedupe.py"
    spec = importlib.util.spec_from_file_location("post_deploy_dedupe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _receipt(sha="a" * 40, web="dep-web-1", worker="dep-wrk-1", status="WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED"):
    return {"status": status, "target_sha": sha, "web_deploy_id": web, "worker_deploy_id": worker}


def test_dedupe_skips_only_identical_deployment():
    d = _dedupe()
    assert d.already_accepted(_receipt(), [_receipt()]) is True


@pytest.mark.parametrize("prior", [
    _receipt(web="dep-web-2"),          # same SHA, web redeployed
    _receipt(worker="dep-wrk-2"),       # same SHA, worker redeployed
    _receipt(sha="b" * 40),             # different SHA
])
def test_dedupe_runs_everything_after_redeploy_of_same_sha_or_new_sha(prior):
    assert _dedupe().already_accepted(_receipt(), [prior]) is False


@pytest.mark.parametrize("bad", [
    {}, None, "x", _receipt(status="READBACK_HOLD"), _receipt(web=""), _receipt(worker=""),
])
def test_dedupe_fails_closed_on_missing_or_malformed_evidence(bad):
    d = _dedupe()
    assert d.already_accepted(bad, [_receipt()]) is False
    assert d.already_accepted(_receipt(), [bad]) is False
    assert d.already_accepted(_receipt(), []) is False


def test_dedupe_cli_fails_closed_when_github_unreachable(tmp_path):
    receipt = tmp_path / "r.json"
    receipt.write_text(__import__("json").dumps(_receipt()), encoding="utf-8")
    out = tmp_path / "out"
    env = {**os.environ, "SOURCE_EVENT": "workflow_run", "READBACK_RECEIPT": str(receipt),
           "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "1", "GITHUB_OUTPUT": str(out),
           "PATH": str(tmp_path)}  # no gh on PATH -> must fail closed, not crash
    done = subprocess.run([__import__("sys").executable, str(ROOT / "artifacts/wow-engine/v17/post_deploy_dedupe.py")],
                          env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0
    assert out.read_text().strip() == "already_accepted=false"


def test_workflow_dedupe_step_uses_deployment_identity_script():
    job = _load()["jobs"]["deployment-readback"]
    step = next(s for s in job["steps"] if s.get("id") == "dedupe")
    assert "post_deploy_dedupe.py" in step["run"]
    assert "head_sha" not in step["run"]
