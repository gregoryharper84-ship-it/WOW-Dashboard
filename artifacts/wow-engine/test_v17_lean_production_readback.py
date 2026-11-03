"""Read-only acceptance observer cannot self-certify or accept wrong deploys."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17 import lean_production_readback as m

SHA = "a" * 40
OLD = "b" * 40


def deploy(sha=SHA, status="live", ident="dep-real-live-1"):
    return {"deploy": {"id": ident, "status": status, "commit": {"id": sha}}}


def source_run():
    return {
        "name": m.DEPLOY_WORKFLOW, "path": m.DEPLOY_PATH,
        "repository": {"full_name": m.REPO},
        "head_repository": {"full_name": m.REPO},
        "head_branch": "main", "head_sha": SHA, "status": "completed",
        "conclusion": "success", "event": "workflow_run",
    }


def test_current_live_sha_and_health_observation_never_certifies_production():
    r = m.classify_readback(target_sha=SHA, main_sha=SHA, deploys=[deploy()], health_ok=True)
    assert r["render_deploy_id"] == "dep-real-live-1"
    assert r["status"] == "LIVE_SHA_AND_HEALTH_OBSERVED"
    assert r["terminal"] == "PENDING_SIRT_INDEPENDENT_ACCEPTANCE"
    assert r["production_verified"] is False
    assert r["model_ready"] == "UNVERIFIED"
    assert r["can_execute"] is False


@pytest.mark.parametrize("mutate, reason", [
    (lambda r: r.update(name="fake-deploy"), "DEPLOY_SOURCE_WORKFLOW_NOT_AUTHENTIC"),
    (lambda r: r.update(path=".github/workflows/evil.yml"), "DEPLOY_SOURCE_WORKFLOW_NOT_AUTHENTIC"),
    (lambda r: r.update(head_sha=OLD), "DEPLOY_SOURCE_WORKFLOW_NOT_AUTHENTIC"),
    (lambda r: r.update(head_branch="staging"), "DEPLOY_SOURCE_WORKFLOW_NOT_AUTHENTIC"),
    (lambda r: r.update(conclusion="failure"), "DEPLOY_SOURCE_WORKFLOW_NOT_AUTHENTIC"),
    (lambda r: r.update(event="pull_request_target"), "DEPLOY_SOURCE_WORKFLOW_NOT_AUTHENTIC"),
    (lambda r: r["repository"].update(full_name="fork/repo"), "DEPLOY_SOURCE_WORKFLOW_NOT_AUTHENTIC"),
])
def test_source_provenance_rejected(mutate, reason):
    source = source_run()
    mutate(source)
    with pytest.raises(m.ReadbackHold, match=reason):
        m.validate_source_run(source, target_sha=SHA)


@pytest.mark.parametrize("target,main,deploys,health,reason", [
    (OLD, SHA, [deploy()], True, "DEPLOY_TARGET_NOT_CURRENT_MAIN"),
    (SHA, SHA, [deploy(OLD)], True, "RENDER_LIVE_SHA_MISMATCH"),
    (SHA, SHA, [deploy(status="build_in_progress")], True, "RENDER_LIVE_DEPLOY_NOT_UNIQUE"),
    (SHA, SHA, [deploy(), deploy(OLD)], True, "RENDER_LIVE_DEPLOY_NOT_UNIQUE"),
    (SHA, SHA, [deploy(ident="")], True, "RENDER_LIVE_SHA_MISMATCH"),
    (SHA, SHA, [deploy()], False, "HEALTH_LIVE_NOT_SUCCESS"),
    ("not-sha", SHA, [deploy()], True, "SHA_INVALID"),
])
def test_wrong_source_or_live_state_holds(target, main, deploys, health, reason):
    with pytest.raises(m.ReadbackHold, match=reason):
        m.classify_readback(
            target_sha=target, main_sha=main, deploys=deploys, health_ok=health,
        )
