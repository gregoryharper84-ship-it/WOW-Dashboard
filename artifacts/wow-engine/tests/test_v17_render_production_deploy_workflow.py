from pathlib import Path


REQUIRED_CHECKS = (
    "WOW additional required regression",
    "WOW governed probability backend",
    "WOW required-three regression",
)


def _workflow_text() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / ".github" / "workflows" / "wow-v17-render-production-deploy.yml").read_text(
        encoding="utf-8"
    )


def test_render_deploy_observer_is_manual_only_to_avoid_checkspass_deadlock():
    text = _workflow_text()

    assert "workflow_dispatch:" in text
    assert "workflow_run:" not in text
    assert 'workflows: ["wow-verify"]' not in text
    assert "checksPass" in text
    assert "sole production deploy authority" in text


def test_render_deploy_observer_requires_current_main_required_checks():
    text = _workflow_text()

    assert 'github_json("/branches/main")' in text
    for check_name in REQUIRED_CHECKS:
        assert check_name in text
    assert "RENDER_DEPLOY_REQUIRED_CHECKS_NOT_GREEN" in text


def test_render_deploy_observer_is_read_only_and_preserves_execution_guardrails():
    text = _workflow_text()

    assert "RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}" in text
    assert "WOW_ACTION_API_KEY" not in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"can_execute": False' in text
    assert "/deploys?limit=20" in text
    assert 'method="POST"' not in text
    assert "DEPLOY_TRIGGERED" not in text
    assert "clearCache" not in text


def test_daily_snapshot_has_deploy_handoff_runway_and_network_retries():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-daily-snapshot.yml").read_text(
        encoding="utf-8"
    )

    assert "timeout-minutes: 40" in text
    assert "max_attempts = 90" in text
    assert "retry_delay_seconds = 20" in text
    assert "except (urllib.error.URLError, TimeoutError):" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
