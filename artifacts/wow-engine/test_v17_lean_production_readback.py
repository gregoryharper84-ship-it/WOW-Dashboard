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


def test_observer_is_nonblocking_and_never_a_render_writer():
    import yaml
    root = Path(__file__).resolve().parents[2]
    source = (root / ".github/workflows/wow-v17-lean-production-readback.yml").read_text()
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    job = workflow["jobs"]["production-readback"]
    observer = next(s for s in job["steps"] if s.get("id") == "readback")
    assert observer["continue-on-error"] == "true"
    assert "lean_production_readback.py" in observer["run"]
    assert "workflows: [wow-verify]" in source
    assert "pull_request_target" not in source
    assert "gh pr merge" not in source
    assert "render.com/v1/services" not in source
    assert workflow["permissions"]["contents"] == "read"
