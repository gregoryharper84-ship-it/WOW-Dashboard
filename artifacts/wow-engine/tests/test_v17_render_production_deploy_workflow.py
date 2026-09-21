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


def test_render_deploy_gate_waits_for_protected_main_required_checks():
    text = _workflow_text()

    assert 'workflows: ["wow-verify"]' in text
    assert 'head_branch != "main"' in text
    assert 'conclusion != "success"' in text
    assert 'github_json("/branches/main")' in text
    assert "if main_sha != target_sha:" in text
    for check_name in REQUIRED_CHECKS:
        assert check_name in text


def test_render_deploy_gate_has_no_second_deploy_authority_or_betting_secrets():
    text = _workflow_text()

    assert "RENDER_API_KEY" not in text
    assert "WOW_RENDER_SERVICE_ID" not in text
    assert "WOW_ACTION_API_KEY" not in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"can_execute": False' in text
    assert '"deploy_authority": "RENDER_CHECKSPASS"' in text
    assert 'method="POST"' not in text
    assert "clearCache" not in text


def test_render_deploy_gate_finishes_before_native_checkspass_deploy():
    text = _workflow_text()

    assert "CHECKSPASS_DEPLOY_GATE_RELEASED" in text
    assert "/deploys?limit=20" not in text
    assert "NATIVE_DEPLOY_LIVE" not in text
    assert "NATIVE_DEPLOY_OBSERVED_IN_PROGRESS" not in text
    assert "deploy_deadline" not in text
    assert "time.sleep(15)" in text  # required-check polling only


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
