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
    assert 'head_branch != "main"' in text
    assert 'conclusion != "success"' in text
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


def test_render_deploy_observes_native_checkspass_without_second_post():
    text = _workflow_text()

    assert "/deploys?limit=20" in text
    assert "NATIVE_DEPLOY_LIVE" in text
    assert "NATIVE_DEPLOY_OBSERVED_IN_PROGRESS" in text
    assert '{"created", "build_in_progress", "update_in_progress"}' in text
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
