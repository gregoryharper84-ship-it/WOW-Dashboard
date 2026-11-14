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
        "READBACK_OUTCOME": "success",
        "SOURCE_IDENTITY_OUTCOME": "success",
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
        "READBACK_OUTCOME": readback,
        "SOURCE_IDENTITY_OUTCOME": "success",
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
    assert step["if"] == "steps.source_identity.outcome == 'success'"
    assert job["outputs"]["readback_outcome"] == "${{ steps.readback.outcome }}"
    for name in ("spread-forward", "spread-certification", "priority-props", "golden-full-slate"):
        assert "needs.deployment-readback.result == 'success'" in workflow["jobs"][name]["if"]


def test_missing_deployment_readback_fails_receipt_even_when_all_lanes_claim_success(tmp_path):
    env = {**os.environ, "WOW_CAN_EXECUTE": "false", "WOW_DRY_RUN_ONLY": "true",
           "SPREAD_FORWARD": "success", "SPREAD_CERTIFICATION": "success",
           "PRIORITY_PROPS": "success", "GOLDEN_FULL_SLATE": "success",
           "DEPLOYMENT_READBACK": "skipped", "READBACK_OUTCOME": "skipped",
           "SOURCE_IDENTITY_OUTCOME": "success", "ORCHESTRATOR_EVENT_NAME": "workflow_dispatch",
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
    assert final_readback["if"] == "steps.receipt_identity.outcome == 'success'"
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


# --- Pending deploy must not poison the commit Render is evaluating ---
def test_pending_deploy_defers_every_acceptance_lane_and_dedupe():
    jobs = _load()["jobs"]
    readback = jobs["deployment-readback"]
    assert readback["outputs"]["deploy_pending"] == "${{ steps.readback.outputs.deploy_pending }}"
    dedupe = next(s for s in readback["steps"] if s.get("id") == "dedupe")
    assert "steps.readback.outcome == 'success'" in dedupe["if"]
    assert "steps.readback.outputs.deploy_pending != 'true'" in dedupe["if"]
    for lane in ("spread-forward", "spread-certification", "priority-props", "golden-full-slate"):
        condition = jobs[lane]["if"]
        assert "needs.deployment-readback.outputs.deploy_pending != 'true'" in condition, lane
        assert "needs.deployment-readback.result == 'success'" in condition, lane


def _run_receipt(tmp_path, **overrides):
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "WOW_CAN_EXECUTE": "false", "WOW_DRY_RUN_ONLY": "true",
        "DEPLOYMENT_READBACK": "success", "READBACK_OUTCOME": "success",
        "SOURCE_IDENTITY_OUTCOME": "success", "DEPLOY_PENDING": "true", "ALREADY_ACCEPTED": "",
        "SPREAD_FORWARD": "skipped", "SPREAD_CERTIFICATION": "skipped",
        "PRIORITY_PROPS": "skipped", "GOLDEN_FULL_SLATE": "skipped",
        "GITHUB_STEP_SUMMARY": str(summary),
        **overrides,
    }
    done = subprocess.run(["bash", "-c", _receipt_script()], cwd=ROOT, env=env,
                          text=True, capture_output=True, check=False)
    return done.returncode, summary.read_text() if summary.exists() else ""


def test_pending_receipt_passes_only_with_a_successful_pending_readback(tmp_path):
    code, text = _run_receipt(tmp_path)
    assert code == 0
    assert "READBACK_PENDING_DEPLOY" in text and "production_verified=false" in text
    # A failed readback can never be rescued by the pending flag.
    assert _run_receipt(tmp_path, DEPLOYMENT_READBACK="failure", READBACK_OUTCOME="failure")[0] == 1


def test_pending_flag_cannot_hide_missing_lanes_once_deploy_is_live(tmp_path):
    # deploy_pending absent/false: skipped lanes remain missing evidence (fail closed).
    assert _run_receipt(tmp_path, DEPLOY_PENDING="false")[0] == 1
    assert _run_receipt(tmp_path, DEPLOY_PENDING="")[0] == 1


def test_post_deploy_required_observations_fail_closed_at_job_level():
    """A failed SHA, identity, matrix, or final readback must fail its job."""
    jobs = _load()["jobs"]
    for jobname in ("deployment-readback", "acceptance-receipts"):
        for step in jobs[jobname]["steps"]:
            assert "continue-on-error" not in step, (
                f"{jobname}: {step.get('name')!r} masks a required observation"
            )
    for lane in ("spread-forward", "spread-certification", "priority-props", "golden-full-slate"):
        expr = jobs[lane]["if"]
        assert "needs.deployment-readback.result == 'success'" in expr
        assert "needs.deployment-readback.outputs.source_identity_outcome == 'success'" in expr
        assert "needs.deployment-readback.outputs.readback_outcome == 'success'" in expr
    assert 'WOW_CAN_EXECUTE: "false"' in WORKFLOW.read_text(encoding="utf-8")


def test_stale_sha_hold_cannot_run_lanes_or_advance_acceptance_when_job_is_green(tmp_path):
    """Job-level conclusion is insufficient after optional step isolation."""
    status, summary = _run_receipt(
        tmp_path,
        DEPLOYMENT_READBACK="success",  # isolated observer job's check conclusion
        READBACK_OUTCOME="failure",    # actual failed authenticated deployment readback
        SOURCE_IDENTITY_OUTCOME="success",
        DEPLOY_PENDING="false",
        SPREAD_FORWARD="success",
        SPREAD_CERTIFICATION="success",
        PRIORITY_PROPS="success",
        GOLDEN_FULL_SLATE="success",
    )
    assert status == 1
    assert "POST_DEPLOY_READBACK_HOLD" in summary
    assert "production_verified=false" in summary
    receipt = _load()["jobs"]["acceptance-receipts"]
    matrix = next(step for step in receipt["steps"] if step.get("id") == "receipt_matrix")
    checkout = next(step for step in receipt["steps"] if step.get("id") == "receipt_checkout")
    assert "continue-on-error" not in matrix
    assert "steps.receipt_matrix.outcome == 'success'" in checkout["if"]
    assert "needs.deployment-readback.outputs.deploy_pending != 'true'" in checkout["if"]
    final_readback = next(step for step in receipt["steps"] if step.get("id") == "final-readback")
    assert final_readback["if"] == "steps.receipt_identity.outcome == 'success'"


def test_identity_failure_fails_matrix_even_when_live_readback_claims_success(tmp_path):
    status, summary = _run_receipt(
        tmp_path,
        DEPLOYMENT_READBACK="success",
        READBACK_OUTCOME="success",
        SOURCE_IDENTITY_OUTCOME="failure",
        DEPLOY_PENDING="false",
        SPREAD_FORWARD="success",
        SPREAD_CERTIFICATION="success",
        PRIORITY_PROPS="success",
        GOLDEN_FULL_SLATE="success",
    )
    assert status == 1
    assert "POST_DEPLOY_READBACK_HOLD" in summary



@pytest.mark.parametrize(("final_status", "final_worker", "expected"), [
    ("WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED", "dep-wrk-1", True),
    ("READBACK_PENDING_DEPLOY", "dep-wrk-1", False),
    ("READBACK_HOLD", "dep-wrk-1", False),
    ("WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED", "dep-wrk-2", False),
])
def test_dedupe_requires_final_same_deployment_readback(
    monkeypatch, final_status, final_worker, expected
):
    """Historical success status + initial readback cannot bypass final HOLD."""
    import io
    import json
    import zipfile

    d = _dedupe()
    initial = _receipt()
    final = _receipt(worker=final_worker, status=final_status)

    def zip_artifact(filename, payload):
        output = io.BytesIO()
        with zipfile.ZipFile(output, mode="w") as zf:
            zf.writestr(filename, json.dumps(payload))
        return output.getvalue()

    def fake_gh(args):
        url = args[0]
        if "/actions/workflows/" in url and "/runs?" in url:
            return json.dumps({"workflow_runs": [
                {"id": 123, "run_attempt": 2, "conclusion": "success"}
            ]}).encode()
        if "/actions/runs/123/artifacts?" in url:
            return json.dumps({"artifacts": [
                {"id": 1, "name": "wow-v17-post-deploy-readback-123-2", "expired": False},
                {"id": 2, "name": "wow-v17-post-deploy-final-readback-123-2", "expired": False},
            ]}).encode()
        if url.endswith("/artifacts/1/zip"):
            return zip_artifact("wow-v17-post-deploy-readback.json", initial)
        if url.endswith("/artifacts/2/zip"):
            return zip_artifact("wow-v17-post-deploy-final-readback.json", final)
        raise AssertionError(f"unexpected GitHub read {url}")

    monkeypatch.setattr(d, "_gh", fake_gh)
    accepted = d._prior_receipts("owner/repo", "a" * 40, "999")
    assert d.already_accepted(initial, accepted) is expected


def test_dedupe_missing_final_artifact_does_not_skip(monkeypatch):
    import json

    d = _dedupe()

    def fake_gh(args):
        url = args[0]
        if "/actions/workflows/" in url:
            return json.dumps({"workflow_runs": [
                {"id": 123, "run_attempt": 2, "conclusion": "success"}
            ]}).encode()
        if "/actions/runs/123/artifacts?" in url:
            return json.dumps({"artifacts": [
                {"id": 1, "name": "wow-v17-post-deploy-readback-123-2", "expired": False},
            ]}).encode()
        raise AssertionError("No receipt should be trusted without final acceptance")

    monkeypatch.setattr(d, "_gh", fake_gh)
    assert d._prior_receipts("owner/repo", "a" * 40, "999") == []


@pytest.mark.parametrize(("final_pending", "expected"), [
    ("true", "POST_DEPLOY_ACCEPTANCE_HOLD"),
    ("false", "POST_DEPLOY_LANES_OBSERVED_SIRT_PENDING"),
])
def test_final_readback_pending_cannot_claim_acceptance(tmp_path, final_pending, expected):
    job = _load()["jobs"]["acceptance-receipts"]
    summary_step = next(s for s in job["steps"]
                        if s.get("name") == "Preserve nonauthoritative post-deploy observer status")
    assert summary_step["env"]["FINAL_DEPLOY_PENDING"] == "$" + "{{ steps.final-readback.outputs.deploy_pending }}"
    assert "continue-on-error" not in summary_step
    summary = tmp_path / "status.txt"
    env = {
        **os.environ,
        "MATRIX_OUTCOME": "success",
        "FINAL_READBACK_OUTCOME": "success",
        "DEPLOY_PENDING": "false",
        "FINAL_DEPLOY_PENDING": final_pending,
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    result = subprocess.run(["bash", "-c", summary_step["run"]],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    text = summary.read_text()
    assert expected in text
    assert "production_verified=false can_execute=false" in text
    if final_pending == "true":
        assert "POST_DEPLOY_LANES_OBSERVED_SIRT_PENDING" not in text

def test_pending_deploy_skips_final_artifact_without_masking_live_failures():
    """Verified PENDING has no final receipt; live acceptance must retain missing-file failure."""
    jobs = _load()["jobs"]
    steps = jobs["acceptance-receipts"]["steps"]
    upload = next(s for s in steps if s.get("name", "").startswith("Preserve final observation receipt"))
    assert upload["if"] == "always() && needs.deployment-readback.outputs.deploy_pending != 'true'"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "continue-on-error" not in upload
    checkout = next(s for s in steps if s.get("id") == "receipt_checkout")
    assert "needs.deployment-readback.outputs.deploy_pending != 'true'" in checkout["if"]
    assert _run_receipt(
        __import__("pathlib").Path(__import__("tempfile").mkdtemp()),
        DEPLOY_PENDING="true", DEPLOYMENT_READBACK="success",
        READBACK_OUTCOME="success", SOURCE_IDENTITY_OUTCOME="success",
    )[0] == 0


def test_dedupe_subprocess_failure_on_one_prior_artifact_checks_next_candidate(monkeypatch):
    import json
    d = _dedupe()
    sha = "a" * 40
    def fake_gh(args):
        endpoint = args[0]
        if "/workflows/" in endpoint:
            return json.dumps({"workflow_runs": [
                {"id": 101, "run_attempt": 1, "conclusion": "success"},
                {"id": 102, "run_attempt": 1, "conclusion": "success"},
            ]}).encode()
        if "/runs/" in endpoint and "/artifacts?" in endpoint:
            run = "101" if "/101/" in endpoint else "102"
            return json.dumps({"artifacts": [
                {"id": int(run + "1"), "name": f"wow-v17-post-deploy-readback-{run}-1"},
                {"id": int(run + "2"), "name": f"wow-v17-post-deploy-final-readback-{run}-1"},
            ]}).encode()
        raise AssertionError(endpoint)
    def fake_artifact(_repo, artifact, *, expected_filename):
        if artifact["id"] in (1011, 1012):
            raise subprocess.CalledProcessError(1, ["gh", "api"])
        return _receipt()
    monkeypatch.setattr(d, "_gh", fake_gh)
    monkeypatch.setattr(d, "_artifact_readback", fake_artifact)
    assert d._prior_receipts("owner/repo", sha, "999") == [_receipt()]

