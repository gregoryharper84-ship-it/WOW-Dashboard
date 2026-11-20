"""Negative tests: native Render CI-gated web/worker readback never self-certifies."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17 import lean_production_readback as m

SHA = "a" * 40
OLD = "b" * 40


def service(role, trigger="checksPass"):
    return {
        "id": m.SERVICES[role], "branch": "main",
        "repo": "https://github.com/" + m.REPO,
        "autoDeployTrigger": trigger,
    }


def deploy(sha=SHA, status="live", ident="dep-real-live-1"):
    return {"deploy": {"id": ident, "status": status, "commit": {"id": sha}}}


def source_run():
    return {
        "name": m.SOURCE_WORKFLOW, "path": m.SOURCE_PATH,
        "repository": {"full_name": m.REPO},
        "head_repository": {"full_name": m.REPO},
        "head_branch": "main", "head_sha": SHA, "status": "completed",
        "conclusion": "success", "event": "push",
    }


def score(*, target=SHA, main=SHA, services=None, deploys=None, health=True):
    return m.classify_readback(
        target_sha=target, main_sha=main,
        services=services if services is not None else {
            role: service(role) for role in m.SERVICES
        },
        deploys=deploys if deploys is not None else {
            role: [deploy(ident="dep-" + role)] for role in m.SERVICES
        },
        health_ok=health,
    )


def test_two_services_current_ci_gated_and_live_sha_never_certifies_production():
    result = score()
    assert result["web_deploy_id"] == "dep-web"
    assert result["worker_deploy_id"] == "dep-worker"
    assert result["status"] == "WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED"
    assert result["terminal"] == "PENDING_SIRT_INDEPENDENT_ACCEPTANCE"
    assert result["production_verified"] is False
    assert result["model_ready"] == "UNVERIFIED"
    assert result["can_execute"] is False


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(name="fake-verify"),
    lambda r: r.update(path=".github/workflows/evil.yml"),
    lambda r: r.update(head_sha=OLD),
    lambda r: r.update(head_branch="staging"),
    lambda r: r.update(conclusion="failure"),
    lambda r: r.update(event="pull_request_target"),
    lambda r: r["repository"].update(full_name="fork/repo"),
])
def test_source_provenance_rejected(mutate):
    result = source_run()
    mutate(result)
    with pytest.raises(m.ReadbackHold, match="MAIN_CI_SOURCE_NOT_AUTHENTIC_EXACT_SUCCESS"):
        m.validate_source_run(result, target_sha=SHA)


@pytest.mark.parametrize("kwargs,reason", [
    ({"target": OLD}, "TARGET_NOT_CURRENT_MAIN"),
    ({"target": "not-sha"}, "SHA_INVALID"),
    ({"health": False}, "HEALTH_LIVE_NOT_SUCCESS"),
    ({"services": {"web": service("web"), "worker": service("worker", trigger="off")}},
     "RENDER_CI_GATED_SERVICE_CONFIGURATION_MISMATCH:worker"),
    ({"services": {"web": service("web", trigger="commit"), "worker": service("worker")}},
     "RENDER_CI_GATED_SERVICE_CONFIGURATION_MISMATCH:web"),
    ({"deploys": {"web": [deploy(OLD)], "worker": [deploy()]}},
     "RENDER_LIVE_SHA_MISMATCH:web"),
    ({"deploys": {"web": [deploy()], "worker": [deploy(OLD)]}},
     "RENDER_LIVE_SHA_MISMATCH:worker"),
    ({"deploys": {"web": [deploy()], "worker": [deploy(status="build_in_progress")]}},
     "RENDER_LIVE_DEPLOY_NOT_UNIQUE:worker"),
    ({"deploys": {"web": [deploy(), deploy(OLD)], "worker": [deploy()]}},
     "RENDER_LIVE_DEPLOY_NOT_UNIQUE:web"),
])
def test_wrong_service_configuration_or_deploy_is_hold(kwargs, reason):
    with pytest.raises(m.ReadbackHold, match=reason):
        score(**kwargs)


def test_transport_or_unexpected_shape_failure_always_writes_hold(tmp_path, monkeypatch):
    import http.client
    import json

    for failure in (
        ConnectionResetError("untrusted credential leak"),
        http.client.IncompleteRead(b"", 7),
        AttributeError("bad API response shape"),
    ):
        target = tmp_path / "readback.json"
        monkeypatch.setenv("READBACK_RECEIPT", str(target))
        monkeypatch.setenv("EXPECTED_SHA", SHA)
        def fail():
            raise failure
        monkeypatch.setattr(m, "_readback", fail)
        assert m.main() == 1
        receipt = json.loads(target.read_text())
        assert receipt == {
            "status": "READBACK_HOLD",
            "reason": "READBACK_SOURCE_UNAVAILABLE_OR_INVALID",
            "expected_sha": SHA,
            "production_verified": False,
            "model_ready": "UNVERIFIED",
            "can_execute": False,
        }
        assert "credential leak" not in target.read_text()


def test_scheduled_readback_is_not_a_source_ci_gate(monkeypatch):
    urls = []
    monkeypatch.setenv("EXPECTED_SHA", SHA)
    monkeypatch.setenv("GITHUB_REPOSITORY", m.REPO)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("RENDER_API_KEY", "fixture-token")
    monkeypatch.setenv("SOURCE_EVENT", "schedule")

    def getter(url, _headers):
        urls.append(url)
        if url.endswith("/branches/main"):
            return {"commit": {"sha": SHA}}
        if "/services/" in url and "/deploys?" not in url:
            role = next(role for role, sid in m.SERVICES.items() if sid in url)
            return service(role)
        if "/deploys?" in url:
            role = next(role for role, sid in m.SERVICES.items() if sid in url)
            return [deploy(ident="dep-" + role)]
        raise AssertionError("Unexpected authenticated GET " + url)

    monkeypatch.setattr(m, "_get_json", getter)
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda *_a, **_k: Response())
    receipt = m._readback()
    assert receipt["status"] == "WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED"
    assert receipt["production_verified"] is False
    assert not any("/actions/runs/" in url for url in urls)


def test_observer_is_nonblocking_and_never_a_render_writer():
    import yaml
    root = Path(__file__).resolve().parents[2]
    source = (root / ".github/workflows/wow-v17-lean-production-readback.yml").read_text()
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    job = workflow["jobs"]["production-readback"]
    observer = next(s for s in job["steps"] if s.get("id") == "readback")
    assert observer["continue-on-error"] == "true"
    assert "lean_production_readback.py" in observer["run"]
    assert 'cron: "11 * * * *"' in source
    assert "workflow_run:" not in source
    assert "workflow_dispatch:" in source
    assert "pull_request_target" not in source
    assert "gh pr merge" not in source
    assert "render.com/v1/services" not in source
    assert workflow["permissions"]["contents"] == "read"


def observer_source_run():
    return {
        "id": 987,
        "name": "wow-v17-lean-production-readback",
        "path": ".github/workflows/wow-v17-lean-production-readback.yml",
        "repository": {"full_name": m.REPO},
        "head_repository": {"full_name": m.REPO},
        "head_branch": "main", "head_sha": SHA, "status": "completed",
        "conclusion": "success", "event": "schedule",
    }


@pytest.mark.parametrize("worker_sha,main_sha,reason", [
    (OLD, SHA, "RENDER_LIVE_SHA_MISMATCH:worker"),
    (SHA, OLD, "TARGET_NOT_CURRENT_MAIN"),
    (SHA, SHA, None),
])
def test_post_deploy_source_is_authenticated_but_still_requires_fresh_both_service_readback(monkeypatch, worker_sha, main_sha, reason):
    """A green nonblocking observer is only a trigger, never deployment proof."""
    monkeypatch.setenv("EXPECTED_SHA", SHA)
    monkeypatch.setenv("GITHUB_REPOSITORY", m.REPO)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("RENDER_API_KEY", "fixture-token")
    monkeypatch.setenv("SOURCE_EVENT", "workflow_run")
    monkeypatch.setenv("SOURCE_RUN_ID", "987")
    monkeypatch.setenv("SOURCE_RUN_KIND", "production_observer")

    def getter(url, _headers):
        if url.endswith("/actions/runs/987"):
            return observer_source_run()
        if url.endswith("/branches/main"):
            return {"commit": {"sha": main_sha}}
        if "/services/" in url and "/deploys?" not in url:
            role = next(role for role, sid in m.SERVICES.items() if sid in url)
            return service(role)
        if "/deploys?" in url:
            role = next(role for role, sid in m.SERVICES.items() if sid in url)
            # Source workflow success cannot hide an old worker deployment.
            return [deploy(worker_sha if role == "worker" else SHA, ident="dep-" + role)]
        raise AssertionError("Unexpected authenticated GET " + url)

    monkeypatch.setattr(m, "_get_json", getter)
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda *_a, **_k: Response())
    if reason:
        with pytest.raises(m.ReadbackHold, match=reason):
            m._readback()
    else:
        # No upstream artifact read is allowed by the getter: fresh deployment
        # API evidence supplies this observation, and still cannot approve it.
        result = m._readback()
        assert result["status"] == "WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED"
        assert result["source_run_id"] == "987"
        assert result["source_run_kind"] == "production_observer"
        assert result["production_verified"] is False
        assert result["can_execute"] is False


@pytest.mark.parametrize("field,value", [
    ("name", "wow-v17-render-production-deploy"),
    ("path", ".github/workflows/foreign.yml"),
    ("head_sha", OLD),
    ("head_branch", "feature"),
    ("event", "pull_request"),
    ("conclusion", "failure"),
    ("id", 988),
    ("repository", {"full_name": "foreign/repo"}),
    ("head_repository", {"full_name": "foreign/repo"}),
])
def test_native_post_deploy_source_must_be_exact_authenticated_observer(field, value):
    run = observer_source_run()
    run[field] = value
    with pytest.raises(m.ReadbackHold, match="PRODUCTION_OBSERVER_SOURCE_NOT_AUTHENTIC_EXACT_SUCCESS"):
        m.validate_observer_run(run, target_sha=SHA, source_run_id="987")


@pytest.mark.parametrize("credential", ["GH_TOKEN", "RENDER_API_KEY"])
def test_missing_authenticated_readback_credentials_is_hold_without_any_source_read(monkeypatch, credential):
    monkeypatch.setenv("EXPECTED_SHA", SHA)
    monkeypatch.setenv("GITHUB_REPOSITORY", m.REPO)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("RENDER_API_KEY", "fixture-token")
    monkeypatch.delenv(credential, raising=False)
    def unexpected_get(*_a, **_kw):
        raise AssertionError("credential failure must stop before reading any source")
    monkeypatch.setattr(m, "_get_json", unexpected_get)
    with pytest.raises(m.ReadbackHold, match="READ_ONLY_API_CREDENTIAL_MISSING"):
        m._readback()


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_native_observer_trigger_can_start_acceptance_but_cannot_approve_production(event):
    source = observer_source_run()
    source["event"] = event
    m.validate_observer_run(source, target_sha=SHA, source_run_id="987")
    # Independent approval remains missing even after a valid authenticated
    # trigger and exact current-main web/worker readback.
    result = score()
    assert result["production_verified"] is False
    assert result["terminal"] == "PENDING_SIRT_INDEPENDENT_ACCEPTANCE"
    assert result["can_execute"] is False


# --- Bounded pending-deploy observation (release-gate circularity repair) ---
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 11, 7, 15, 0, 0, tzinfo=timezone.utc)


def _iso(delta_seconds):
    return (NOW - timedelta(seconds=delta_seconds)).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("role", ["web", "worker"])
def test_recent_live_sha_mismatch_is_typed_pending_never_verified(role):
    out = m.pending_deploy_outcome(
        "RENDER_LIVE_SHA_MISMATCH:" + role, target_sha=SHA, commit_iso=_iso(600), now=NOW)
    assert out["status"] == "READBACK_PENDING_DEPLOY"
    assert out["expected_sha"] == SHA
    assert out["production_verified"] is False
    assert out["model_ready"] == "UNVERIFIED"
    assert out["can_execute"] is False
    assert out["terminal"] == "DEPLOY_PENDING_NOT_ACCEPTANCE"


@pytest.mark.parametrize("reason", [
    "HEALTH_LIVE_NOT_SUCCESS",
    "RENDER_CI_GATED_SERVICE_CONFIGURATION_MISMATCH:web",
    "RENDER_LIVE_DEPLOY_NOT_UNIQUE:worker",
    "TARGET_NOT_CURRENT_MAIN",
    "READBACK_SOURCE_UNAVAILABLE_OR_INVALID",
    "",
])
def test_only_a_live_sha_mismatch_can_be_pending(reason):
    assert m.pending_deploy_outcome(
        reason, target_sha=SHA, commit_iso=_iso(60), now=NOW) is None


@pytest.mark.parametrize("commit_iso", [
    None, "", "not-a-date", "2026-11-07T14:50:00",          # naive (no zone)
    _iso(m.PENDING_DEPLOY_GRACE_SECONDS + 1),                  # expired window
    (NOW + timedelta(seconds=5)).isoformat(),                 # commit "in the future"
    12345,
])
def test_pending_requires_a_valid_recent_zoned_commit_time(commit_iso):
    assert m.pending_deploy_outcome(
        "RENDER_LIVE_SHA_MISMATCH:web", target_sha=SHA, commit_iso=commit_iso, now=NOW) is None


def test_grace_window_boundary_is_inclusive_and_bounded():
    reason = "RENDER_LIVE_SHA_MISMATCH:web"
    assert m.pending_deploy_outcome(
        reason, target_sha=SHA, commit_iso=_iso(m.PENDING_DEPLOY_GRACE_SECONDS), now=NOW)
    assert m.pending_deploy_outcome(
        reason, target_sha="not-a-sha", commit_iso=_iso(10), now=NOW) is None


def _run_main(monkeypatch, tmp_path, *, exc, commit_time):
    import json
    receipt, gh_out = tmp_path / "r.json", tmp_path / "out.txt"
    monkeypatch.setenv("READBACK_RECEIPT", str(receipt))
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    monkeypatch.setenv("EXPECTED_SHA", SHA)

    def fail():
        raise exc
    monkeypatch.setattr(m, "_readback", fail)
    if isinstance(commit_time, Exception):
        def boom(_t):
            raise commit_time
        monkeypatch.setattr(m, "_commit_time", boom)
    else:
        monkeypatch.setattr(m, "_commit_time", lambda _t: commit_time)
    code = m.main()
    return code, json.loads(receipt.read_text()), gh_out.read_text()


def test_main_exits_zero_with_pending_receipt_inside_grace(monkeypatch, tmp_path):
    fresh = datetime.now(timezone.utc).isoformat()
    code, receipt, out = _run_main(
        monkeypatch, tmp_path, exc=m.ReadbackHold("RENDER_LIVE_SHA_MISMATCH:web"), commit_time=fresh)
    assert code == 0
    assert receipt["status"] == "READBACK_PENDING_DEPLOY"
    assert receipt["production_verified"] is False
    assert "deploy_pending=true" in out


def test_main_still_fails_closed_after_grace_expires(monkeypatch, tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(seconds=m.PENDING_DEPLOY_GRACE_SECONDS + 60)).isoformat()
    code, receipt, out = _run_main(
        monkeypatch, tmp_path, exc=m.ReadbackHold("RENDER_LIVE_SHA_MISMATCH:worker"), commit_time=old)
    assert code == 1
    assert receipt["status"] == "READBACK_HOLD"
    assert receipt["reason"] == "RENDER_LIVE_SHA_MISMATCH:worker"
    assert "deploy_pending=false" in out


@pytest.mark.parametrize("commit_time", [RuntimeError("github down"), "garbage"])
def test_main_fails_closed_when_commit_age_cannot_be_established(monkeypatch, tmp_path, commit_time):
    code, receipt, out = _run_main(
        monkeypatch, tmp_path, exc=m.ReadbackHold("RENDER_LIVE_SHA_MISMATCH:web"), commit_time=commit_time)
    assert code == 1 and receipt["status"] == "READBACK_HOLD"
    assert "deploy_pending=false" in out


@pytest.mark.parametrize("reason", [
    "HEALTH_LIVE_NOT_SUCCESS", "RENDER_CI_GATED_SERVICE_CONFIGURATION_MISMATCH:web", "TARGET_NOT_CURRENT_MAIN",
])
def test_main_recent_commit_never_masks_non_deploy_holds(monkeypatch, tmp_path, reason):
    fresh = datetime.now(timezone.utc).isoformat()
    code, receipt, _ = _run_main(
        monkeypatch, tmp_path, exc=m.ReadbackHold(reason), commit_time=fresh)
    assert code == 1 and receipt["status"] == "READBACK_HOLD" and receipt["reason"] == reason



@pytest.mark.parametrize("stage", ["observed", "pending", "hold"])
def test_failed_github_output_never_leaves_reusable_readback_receipt(
    monkeypatch, tmp_path, stage
):
    """A failed output emission cannot leave a valid prior-run artifact."""
    receipt = tmp_path / "final-readback.json"
    monkeypatch.setenv("READBACK_RECEIPT", str(receipt))
    monkeypatch.setenv("EXPECTED_SHA", SHA)
    if stage == "observed":
        monkeypatch.setattr(m, "_readback", lambda: score())
    else:
        reason = ("RENDER_LIVE_SHA_MISMATCH:web"
                  if stage == "pending" else "HEALTH_LIVE_NOT_SUCCESS")

        def raise_hold():
            raise m.ReadbackHold(reason)

        monkeypatch.setattr(m, "_readback", raise_hold)
        if stage == "pending":
            monkeypatch.setattr(
                m, "_commit_time", lambda _sha: datetime.now(timezone.utc).isoformat()
            )

    def fail_output(_key, _value):
        raise OSError("simulated GitHub output filesystem error")

    monkeypatch.setattr(m, "_emit_output", fail_output)
    with pytest.raises(OSError, match="simulated GitHub output"):
        m.main()
    assert not receipt.exists(), (
        "A failed final-readback output must not leave an artifact that "
        "post_deploy_dedupe could treat as successful deployment evidence"
    )
