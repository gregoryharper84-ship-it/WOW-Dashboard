"""Controlled cutover: retire native checksPass; sole writer remains API deploy."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from v17 import single_writer_cutover as cutover
from v17.lean_production_readback import REPO, SERVICES

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-single-writer-cutover.yml"


def _service(role: str, *, trigger: str = "checksPass", auto: str = "yes") -> dict:
    return {
        "id": SERVICES[role],
        "branch": "main",
        "repo": "https://github.com/" + REPO,
        "autoDeployTrigger": trigger,
        "autoDeploy": auto,
    }


def test_plan_cutover_needs_both_services_when_checks_pass_enabled():
    decision = cutover.plan_cutover({role: _service(role) for role in SERVICES})
    assert decision["status"] == "CUTOVER_NEEDED"
    assert decision["pending"] == ["web", "worker"]
    assert decision["target"] == {"autoDeploy": "no", "autoDeployTrigger": "off"}


def test_plan_cutover_already_complete_when_native_off():
    decision = cutover.plan_cutover({
        role: _service(role, trigger="off", auto="no") for role in SERVICES
    })
    assert decision["status"] == "CUTOVER_ALREADY_COMPLETE"
    assert decision["pending"] == []


def test_dry_run_never_patches():
    state = {role: _service(role) for role in SERVICES}
    posts: list[tuple[str, dict]] = []

    def fetch(method, url, body):
        if method == "PATCH":
            posts.append((url, body))
            return {**state["web"], "autoDeployTrigger": "off", "autoDeploy": "no"}
        role = next(r for r, sid in SERVICES.items() if sid in url)
        return state[role]

    out = cutover.run(fetch=fetch, apply=False)
    assert out["status"] == "CUTOVER_NEEDED"
    assert out["applied"] is False
    assert posts == []


def test_apply_patches_only_pending_services_and_verifies():
    state = {role: _service(role) for role in SERVICES}
    patches: list[tuple[str, dict]] = []

    def fetch(method, url, body):
        role = next(r for r, sid in SERVICES.items() if sid in url)
        if method == "PATCH":
            assert body == cutover.CUTOVER_PAYLOAD
            patches.append((role, body))
            state[role] = {**state[role], **body}
            return state[role]
        return state[role]

    out = cutover.run(fetch=fetch, apply=True)
    assert out["status"] == "CUTOVER_APPLIED_AND_VERIFIED"
    assert out["applied"] is True
    assert sorted(role for role, _ in patches) == ["web", "worker"]
    assert out["native_auto_deploy_retired"] is True
    assert out["after_triggers"] == {"web": "off", "worker": "off"}


def test_verify_failure_after_patch_is_typed_hold():
    state = {role: _service(role) for role in SERVICES}

    def fetch(method, url, body):
        role = next(r for r, sid in SERVICES.items() if sid in url)
        if method == "PATCH":
            # Pretend Render accepted but did not persist.
            return {**state[role], **body}
        return state[role]

    with pytest.raises(cutover.CutoverHold, match="CUTOVER_VERIFY_TRIGGER_NOT_OFF"):
        cutover.run(fetch=fetch, apply=True)


def test_foreign_service_identity_holds_before_write():
    with pytest.raises(cutover.CutoverHold, match="RENDER_SINGLE_WRITER_SERVICE_CONFIGURATION_MISMATCH"):
        cutover.plan_cutover({
            "web": {**_service("web"), "id": "srv-foreign"},
            "worker": _service("worker"),
        })


def test_main_rejects_non_cutover_writes(monkeypatch, tmp_path):
    receipt = tmp_path / "receipt.json"
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("RENDER_API_KEY", "rnd_SECRET")
    monkeypatch.setenv("APPLY_CUTOVER", "true")
    monkeypatch.setenv("CUTOVER_RECEIPT", str(receipt))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def run(*, fetch, apply):
        fetch("POST", "https://api.render.com/v1/services/" + SERVICES["web"] + "/deploys", {})
        return {"status": "SHOULD_NOT"}

    monkeypatch.setattr(cutover, "run", run)
    assert cutover.main() == 1
    body = receipt.read_text()
    assert "WRITE_TARGET_NOT_ALLOWED" in body
    assert "SECRET" not in body


def test_workflow_is_dispatch_only_isolated_and_scoped():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert set(workflow[True]) == {"workflow_dispatch"}
    assert "apply_cutover" in workflow[True]["workflow_dispatch"]["inputs"]
    job = workflow["jobs"]["cutover"]
    assert job["environment"] == "wow-release"
    assert job["env"]["WOW_CAN_EXECUTE"] == "false"
    for step in job["steps"]:
        assert step.get("continue-on-error") is True
    run_step = next(s for s in job["steps"] if "v17.single_writer_cutover" in str(s.get("run")))
    assert set(run_step["env"]) == {"RENDER_API_KEY"}
    scripts = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "GITHUB_STEP_SUMMARY" in scripts
    assert "SINGLE_WRITER_CUTOVER_HOLD" in scripts
