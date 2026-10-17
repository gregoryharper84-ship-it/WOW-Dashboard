"""Negative and regression coverage for gated worker deployment."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "v17" / "render_worker_release_handoff.py"
sys.path.insert(0, str(SCRIPT.parent))
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
    worker_job = workflow.split("  deploy-background-worker:", 1)[1]
    assert "timeout-minutes: 110" in worker_job
    assert "WOW_RELEASE_CONTROL_DSN" in worker_job
    assert "WOW_RELEASE_CONTROL_PROJECT_REF" in worker_job
    assert "timeout-minutes: 20" not in worker_job
    worker_script = SCRIPT.read_text()
    assert "until = time.monotonic() + 5400" in worker_script
    assert "until = time.monotonic() + 900" not in worker_script
    assert "renew_committed(" in worker_script



def test_absent_deployment_fails_closed():
    source = SCRIPT.read_text()
    assert "WORKER_RELEASE_CONTROL_DSN_NOT_PROVISIONED" in source
    assert "WORKER_DEPLOY_ENTRY_INVALID" in source


def test_missing_worker_deploy_does_not_attempt_post(monkeypatch, tmp_path):
    """Missing authorized DB credential must HOLD before any Render POST."""
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
    monkeypatch.delenv("WOW_RELEASE_CONTROL_DSN", raising=False)
    with pytest.raises(RuntimeError, match="WORKER_RELEASE_CONTROL_DSN_NOT_PROVISIONED"):
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
    # Replaying a provider LIVE response is not a durable DB-fenced approval.
    assert module.reconcile_intent(row, [active], sha) == ("RECONCILIATION_REQUIRED", active)


def test_intent_rejects_cross_service_and_cross_revision():
    sha = "a" * 40
    for row in [
        {"service_id": "srv-other", "target_sha": sha, "state": "PENDING"},
        {"service_id": module.WORKER_SERVICE, "target_sha": "b" * 40, "state": "PENDING"},
    ]:
        with pytest.raises(RuntimeError, match="WORKER_DEPLOY_INTENT_IDENTITY_MISMATCH"):
            module.reconcile_intent(row, [], sha)


def test_governed_live_receipt_requires_fenced_db_commit(monkeypatch, tmp_path):
    """One authorized POST leads to committed LIVE_VERIFIED before file receipt."""
    import json
    import types
    from render_worker_governed_initiator import Handoff

    sha = "a" * 40
    timeline = []
    class Connection:
        def __enter__(self):
            timeline.append("db-open")
            return self
        def __exit__(self, *args):
            timeline.append("db-close")
            return False

    fake_psycopg = types.SimpleNamespace(connect=lambda *args, **kwargs: Connection())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    def fake_initiate(**kwargs):
        timeline.append("claim-before-post")
        assert kwargs["target_sha"] == sha
        reply = kwargs["post_deploy"]()
        assert reply["id"] == "dep-receipt"
        return Handoff("DEPLOYING", "dep-receipt", 17)
    monkeypatch.setattr(module, "attest_release_control_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "initiate_once", fake_initiate)

    def fake_request(url, token, method="GET", payload=None):
        if url.endswith("/branches/main"):
            return {"commit": {"sha": sha}}
        if url.endswith("/deploys") and method == "POST":
            assert payload == {"clearCache": "do_not_clear", "commitId": sha}
            timeline.append("exact-commit-post")
            return {"id": "dep-receipt"}
        if "/deploys?" in url:
            timeline.append("provider-read")
            # Initial inventory is empty; subsequent read is verified LIVE.
            if timeline.count("provider-read") == 1:
                return []
            return [{"deploy": {
                "id": "dep-receipt", "commit": {"id": sha}, "status": "live"
            }}]
        raise AssertionError(("unexpected request", url, method))
    monkeypatch.setattr(module, "request_json", fake_request)
    monkeypatch.setattr(module, "renew_committed", lambda **kwargs: timeline.append("lease-renew-committed"))
    def committed(**kwargs):
        assert kwargs["state"] == "LIVE_VERIFIED"
        assert kwargs["deploy_id"] == "dep-receipt"
        assert kwargs["token"] == 17
        timeline.append("live-fence-committed")
    monkeypatch.setattr(module, "record_committed", committed)

    # Advance beyond the renewal interval without waiting.
    times = iter([0, 0, 100, 101])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    for key, value in {
        "GH_API_TOKEN": "test-gh", "RENDER_API_KEY": "test-render",
        "GITHUB_REPOSITORY": "example/repo", "TARGET_MAIN_SHA": sha,
        "PRIMARY_RELEASE_RESULT": "success", "RUNNER_TEMP": str(tmp_path),
        "WOW_RELEASE_CONTROL_DSN": "postgresql://ci-only/mock",
        "GITHUB_RUN_ID": "ci-123",
    }.items():
        monkeypatch.setenv(key, value)
    module.run()
    receipt = json.loads((tmp_path / "wow-render-worker-deploy-receipt.json").read_text())
    assert receipt["render_deploy_id"] == "dep-receipt"
    assert receipt["commit_sha"] == sha
    assert receipt["can_execute"] is False
    assert timeline.count("claim-before-post") == 1
    assert timeline.count("exact-commit-post") == 1
    assert timeline.count("lease-renew-committed") == 1
    assert timeline.index("lease-renew-committed") < timeline.index("live-fence-committed")


def test_failed_durable_live_receipt_never_reports_worker_green(monkeypatch, tmp_path):
    """Provider can be live while the DB acknowledgement is unavailable: HOLD."""
    import types
    from render_worker_governed_initiator import Handoff

    sha = "a" * 40
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(
        connect=lambda *args, **kwargs: Connection()
    ))
    monkeypatch.setattr(module, "attest_release_control_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "initiate_once", lambda **kwargs: Handoff("DEPLOYING", "dep-receipt", 8))
    calls = []
    def fake_request(url, token, method="GET", payload=None):
        if url.endswith("/branches/main"):
            return {"commit": {"sha": sha}}
        if "/deploys?" in url:
            calls.append("read")
            return [] if len(calls) == 1 else [{"deploy": {
                "id": "dep-receipt", "commit": {"id": sha}, "status": "live"
            }}]
        raise AssertionError(url)
    monkeypatch.setattr(module, "request_json", fake_request)
    def fails_commit(**kwargs):
        raise ConnectionError("hidden private database details")
    monkeypatch.setattr(module, "record_committed", fails_commit)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0)
    for key, value in {
        "GH_API_TOKEN": "mock", "RENDER_API_KEY": "mock",
        "GITHUB_REPOSITORY": "example/repo", "TARGET_MAIN_SHA": sha,
        "PRIMARY_RELEASE_RESULT": "success", "RUNNER_TEMP": str(tmp_path),
        "WOW_RELEASE_CONTROL_DSN": "postgresql://ci-only/mock",
        "GITHUB_RUN_ID": "ci-124",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConnectionError):
        module.run()
    assert not (tmp_path / "wow-render-worker-deploy-receipt.json").exists()



def test_worker_deploy_refuses_main_revision_move_between_claim_and_post(monkeypatch, tmp_path):
    """A newer main revision must prevent the sole authorized Render POST."""
    import types

    sha = "a" * 40
    github_reads = []
    render_posts = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(
        connect=lambda *args, **kwargs: Connection()
    ))

    def fake_request(url, token, method="GET", payload=None):
        if url.endswith("/branches/main"):
            github_reads.append(1)
            return {"commit": {"sha": sha if len(github_reads) < 3 else "b" * 40}}
        if "/deploys?" in url:
            return []
        if method == "POST":
            render_posts.append(payload)
            return {"id": "dep-unsafe"}
        raise AssertionError(url)

    monkeypatch.setattr(module, "request_json", fake_request)
    def fake_initiate(**kwargs):
        # Existing lease claim is simulated; initiation invokes the supplied
        # safeguarded callback exactly once, just before provider creation.
        return kwargs["post_deploy"]()
    monkeypatch.setattr(module, "attest_release_control_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "initiate_once", fake_initiate)
    for key, value in {
        "GH_API_TOKEN": "test-gh", "RENDER_API_KEY": "test-render",
        "GITHUB_REPOSITORY": "example/repo", "TARGET_MAIN_SHA": sha,
        "PRIMARY_RELEASE_RESULT": "success", "RUNNER_TEMP": str(tmp_path),
        "WOW_RELEASE_CONTROL_DSN": "postgresql://ci-only/mock",
        "GITHUB_RUN_ID": "ci-stale",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="WORKER_RELEASE_SHA_STALE_OR_INVALID"):
        module.run()
    assert len(github_reads) == 3
    assert render_posts == []
    assert not (tmp_path / "wow-render-worker-deploy-receipt.json").exists()



def test_missing_release_project_identity_does_not_call_render_post(monkeypatch, tmp_path):
    """Fail closed before claim or provider POST on unconfigured DB identity."""
    import types

    sha = "a" * 40
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(
        connect=lambda *args, **kwargs: Connection()
    ))
    called = []
    def disallow_initiate(**kwargs):
        called.append("initiate")
        raise AssertionError("never claim an unverified production DB")
    monkeypatch.setattr(module, "initiate_once", disallow_initiate)
    def reject_identity(connection, *, expected_project_ref):
        assert expected_project_ref == ""
        raise module.DeploymentLeaseError("WORKER_RELEASE_PROJECT_IDENTITY_UNCONFIGURED")
    monkeypatch.setattr(module, "attest_release_control_identity", reject_identity)
    def request(url, token, method="GET", payload=None):
        if url.endswith("/branches/main"): return {"commit":{"sha":sha}}
        if "/deploys?" in url: return []
        if method == "POST": raise AssertionError("unsafe provider POST")
        raise AssertionError(url)
    monkeypatch.setattr(module, "request_json", request)
    for key, value in {
        "GH_API_TOKEN": "test", "RENDER_API_KEY": "test",
        "GITHUB_REPOSITORY": "owner/repo",
        "TARGET_MAIN_SHA": sha, "PRIMARY_RELEASE_RESULT": "success",
        "WOW_RELEASE_CONTROL_DSN": "postgresql://ci-only/mock",
        "RUNNER_TEMP": str(tmp_path), "GITHUB_RUN_ID": "ci-5",
    }.items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv("WOW_RELEASE_CONTROL_PROJECT_REF", raising=False)
    with pytest.raises(module.DeploymentLeaseError, match="PROJECT_IDENTITY_UNCONFIGURED"):
        module.run()
    assert called == []
    assert not (tmp_path / "wow-render-worker-deploy-receipt.json").exists()


@pytest.mark.parametrize("render_status", ["live", "building", "pre_deploy"])
def test_existing_provider_deploy_cannot_forge_fenced_live_receipt(monkeypatch, tmp_path, render_status):
    """An exact SHA on Render is observation, not a DB-fenced release approval."""
    sha = "a" * 40
    calls = []
    def request(url, token, method="GET", payload=None):
        calls.append((method, url))
        if url.endswith("/branches/main"):
            return {"commit": {"sha": sha}}
        if "/deploys?" in url:
            return [{"deploy": {
                "id": "dep-existing", "commit": {"id": sha}, "status": render_status
            }}]
        raise AssertionError("unauthorized provider mutation")
    monkeypatch.setattr(module, "request_json", request)
    for name, value in {
        "GH_API_TOKEN": "mock", "RENDER_API_KEY": "mock",
        "GITHUB_REPOSITORY": "example/repo",
        "TARGET_MAIN_SHA": sha, "PRIMARY_RELEASE_RESULT": "success",
        "RUNNER_TEMP": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("WOW_RELEASE_CONTROL_DSN", raising=False)
    with pytest.raises(RuntimeError, match="WORKER_EXISTING_DEPLOY_REQUIRES_FENCED_RECONCILIATION"):
        module.run()
    assert not any(method == "POST" for method, _ in calls)
    assert not (tmp_path / "wow-render-worker-deploy-receipt.json").exists()


def test_main_move_during_render_live_read_prevents_durable_adoption(monkeypatch, tmp_path):
    """Provider LIVE is not authority if main advances during Render polling."""
    import types
    from render_worker_governed_initiator import Handoff

    sha, next_sha = "a" * 40, "b" * 40
    observations = []
    main_moved = {"value": False}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(
        connect=lambda *args, **kwargs: Connection()
    ))
    monkeypatch.setattr(module, "attest_release_control_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "initiate_once", lambda **kwargs: Handoff("DEPLOYING", "dep-receipt", 7))
    monkeypatch.setattr(module, "record_committed", lambda **kwargs:
                        observations.append("unsafe-live-commit"))
    monkeypatch.setattr(module.time, "monotonic", lambda: 0)

    def request(url, token, method="GET", payload=None):
        assert method == "GET", "No Render POST in this read-only reconciliation test"
        if url.endswith("/branches/main"):
            return {"commit": {"sha": next_sha if main_moved["value"] else sha}}
        if "/deploys?" in url:
            observations.append("provider-read")
            if observations.count("provider-read") == 1:
                return []
            main_moved["value"] = True
            return [{"deploy": {"id": "dep-receipt", "commit": {"id": sha}, "status": "live"}}]
        raise AssertionError(url)

    monkeypatch.setattr(module, "request_json", request)
    for name, value in {
        "GH_API_TOKEN": "ci", "RENDER_API_KEY": "ci",
        "GITHUB_REPOSITORY": "example/repo", "TARGET_MAIN_SHA": sha,
        "PRIMARY_RELEASE_RESULT": "success", "RUNNER_TEMP": str(tmp_path),
        "WOW_RELEASE_CONTROL_DSN": "postgresql://ci-only/mock", "GITHUB_RUN_ID": "ci-race",
    }.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="WORKER_RELEASE_SHA_STALE_OR_INVALID"):
        module.run()
    assert observations.count("provider-read") == 2
    assert "unsafe-live-commit" not in observations
    assert not (tmp_path / "wow-render-worker-deploy-receipt.json").exists()
