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


def test_render_deploy_waits_for_protected_main_required_checks():
    text = _workflow_text()

    assert 'workflows: ["wow-verify"]' in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert '"/branches/main"' in text
    assert "if main_sha != target_sha:" in text
    for check_name in REQUIRED_CHECKS:
        assert check_name in text


def test_render_deploy_uses_control_plane_secret_without_betting_secrets():
    text = _workflow_text()

    assert "RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}" in text
    assert "WOW_ACTION_API_KEY" not in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"can_execute": False' in text


def test_render_deploy_deduplicates_active_commit_before_posting():
    text = _workflow_text()

    assert "/deploys?limit=20" in text
    assert "DEPLOY_ALREADY_ACTIVE" in text
    assert 'active_statuses = {"created", "build_in_progress", "update_in_progress", "live"}' in text
    assert 'method="POST"' in text
    assert "DEPLOY_TRIGGERED" in text


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
