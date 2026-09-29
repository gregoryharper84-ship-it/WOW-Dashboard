from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-render-production-deploy.yml"
CANARY_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-spread-forward-production-canary.yml"


def test_non_advancing_deploy_receipts_fail_controller_after_pointer_reconcile():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    reconcile = text.index("Reconcile exact Render receipt to durable deployment pointer")
    final_gate = text.index("Fail closed when no exact deployment advanced")
    assert reconcile < final_gate
    for status in (
        "NON_MAIN_NO_DEPLOY",
        "UPSTREAM_NOT_SUCCESS_NO_DEPLOY",
        "STALE_SHA_NO_DEPLOY",
        "DEPLOY_SUPERSEDED_BY_NEW_MAIN",
    ):
        assert status in text[final_gate:]
    assert '"POST_DEPLOY_CANARIES_BLOCKED"' in text[final_gate:]
    assert "raise SystemExit(1)" in text[final_gate:]


def test_exact_live_deploy_receipts_still_authorize_successful_controller():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    final_gate = text.index("Fail closed when no exact deployment advanced")
    gated = text[final_gate:]

    assert "EXACT_SHA_RENDER_DEPLOY_LIVE" in gated
    assert "EXACT_SHA_ALREADY_LIVE" in gated
    assert '"EXACT_DEPLOY_ACCEPTED_FOR_CANARIES"' in gated
    assert "raise SystemExit(0)" in gated


def test_spread_canary_requires_successful_deploy_controller():
    text = CANARY_WORKFLOW.read_text(encoding="utf-8")

    assert 'workflows: ["wow-v17-render-production-deploy"]' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text


def test_release_gate_preserves_non_execution_governance():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"can_execute":False' in text or '"can_execute": False' in text
