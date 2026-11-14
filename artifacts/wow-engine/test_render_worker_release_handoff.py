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
