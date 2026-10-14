"""Negative and regression coverage for gated worker deployment."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "v17" / "render_worker_release_handoff.py"
spec = importlib.util.spec_from_file_location("render_worker_release_handoff", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_release_sha_accepts_only_successful_primary_release():
    target = "a" * 40
    assert module.validated_target(target, target, "success") == target
    with pytest.raises(RuntimeError, match="WORKER_PRIMARY_RELEASE_NOT_SUCCESS"):
        module.validated_target(target, target, "failure")


@pytest.mark.parametrize("main,target", [
    ("a" * 40, "b" * 40),
    ("a" * 40, "invalid"),
    ("not-a-sha", "not-a-sha"),
])
def test_reject_stale_or_invalid_main(main, target):
    with pytest.raises(RuntimeError, match="WORKER_RELEASE_SHA_STALE_OR_INVALID"):
        module.validated_target(main, target, "success")


def test_separate_exact_worker_sha_not_other_services():
    sha = "b" * 40
    other = {"id": "dep-other", "commit": {"id": "c" * 40}, "status": "live"}
    assert module.choose_deploy([other], sha) == ("ABSENT", None)
    live = {"id": "dep-correct", "commit": {"id": sha}, "status": "live"}
    assert module.choose_deploy([other, live], sha) == ("LIVE", live)


@pytest.mark.parametrize("bad", sorted(module.TERMINAL))
def test_existing_target_failure_never_redeployed(bad):
    sha = "c" * 40
    with pytest.raises(RuntimeError, match="WORKER_EXACT_SHA_DEPLOY_FAILED"):
        module.choose_deploy([{"commit": {"id": sha}, "status": bad}], sha)


def test_active_deploy_is_reused():
    sha = "d" * 40
    active = {"id": "dep-active", "commit": {"id": sha}, "status": "building"}
    assert module.choose_deploy([active], sha) == ("ACTIVE", active)


def test_protected_workflow_chains_worker_after_primary():
    workflow = (Path(__file__).parents[2] / ".github/workflows/wow-v17-render-production-deploy.yml").read_text()
    assert "needs: [deploy-production]" in workflow
    assert "if: needs.deploy-production.result == 'success'" in workflow
    assert "PRIMARY_RELEASE_RESULT:" in workflow
    assert "TARGET_MAIN_SHA:" in workflow
    assert "render_worker_release_handoff.py" in workflow
    assert 'WOW_RENDER_SERVICE_ID: "srv-da7sa9gu01pc73brt80g"' in workflow


def test_absent_deployment_fails_closed():
    source = SCRIPT.read_text()
    assert "WORKER_NO_EXACT_DEPLOY_REQUIRES_GOVERNED_INITIATION" in source
    assert "WORKER_DEPLOY_ENTRY_INVALID" in source


def test_missing_worker_deploy_does_not_attempt_post(monkeypatch, tmp_path):
    """Provider mock: absent exact SHA must not create a second deployment."""
    import json
    calls = []
    sha = "a" * 40
    def fake_request(url, token, method="GET", payload=None):
        calls.append((url, method))
        if url.endswith("/branches/main"):
            return {"commit": {"sha": sha}}
        if "/deploys?" in url:
            return []
        raise AssertionError("unexpected API call: " + url)
    monkeypatch.setattr(module, "request_json", fake_request)
    monkeypatch.setenv("GH_API_TOKEN", "mock")
    monkeypatch.setenv("RENDER_API_KEY", "mock")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("TARGET_MAIN_SHA", sha)
    monkeypatch.setenv("PRIMARY_RELEASE_RESULT", "success")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    with pytest.raises(RuntimeError, match="WORKER_NO_EXACT_DEPLOY_REQUIRES_GOVERNED_INITIATION"):
        module.run()
    assert not any(method == "POST" for _, method in calls)


def test_stale_main_rejects_before_any_render_request(monkeypatch, tmp_path):
    sha = "a" * 40
    calls = []
    def fake_request(url, token, method="GET", payload=None):
        calls.append(url)
        assert url.endswith("/branches/main")
        return {"commit": {"sha": "b" * 40}}
    monkeypatch.setattr(module, "request_json", fake_request)
    for key, value in {
        "GH_API_TOKEN": "mock", "RENDER_API_KEY": "mock",
        "GITHUB_REPOSITORY": "example/repo", "TARGET_MAIN_SHA": sha,
        "PRIMARY_RELEASE_RESULT": "success", "RUNNER_TEMP": str(tmp_path),
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="WORKER_RELEASE_SHA_STALE_OR_INVALID"):
        module.run()
    assert len(calls) == 1


@pytest.mark.parametrize("state", ["INITIATING", "ACK_AMBIGUOUS", "DEPLOYING", "LIVE_VERIFIED", "HOLD"])
def test_ambiguous_intent_does_not_reset_to_pending(state):
    row = {"service_id": module.WORKER_SERVICE, "target_sha": "a" * 40, "state": state}
    result, candidate = module.reconcile_intent(row, [], "a" * 40)
    assert result == "RECONCILIATION_REQUIRED"
    assert candidate is None


def test_intent_reconciles_observed_deployment():
    sha = "a" * 40
    row = {"service_id": module.WORKER_SERVICE, "target_sha": sha, "state": "ACK_AMBIGUOUS"}
    active = {"id": "dep-live", "commit": {"id": sha}, "status": "live"}
    assert module.reconcile_intent(row, [active], sha) == ("LIVE_VERIFIED", active)


def test_intent_rejects_cross_service_and_cross_revision():
    sha = "a" * 40
    for row in [
        {"service_id": "srv-other", "target_sha": sha, "state": "PENDING"},
        {"service_id": module.WORKER_SERVICE, "target_sha": "b" * 40, "state": "PENDING"},
    ]:
        with pytest.raises(RuntimeError, match="WORKER_DEPLOY_INTENT_IDENTITY_MISMATCH"):
            module.reconcile_intent(row, [], sha)
