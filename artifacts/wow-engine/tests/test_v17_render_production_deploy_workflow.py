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


def test_render_deploy_handoff_uses_control_plane_only_after_governance_gate():
    text = _workflow_text()

    assert 'RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}' in text
    assert 'WOW_RENDER_SERVICE_ID: "srv-da7sa9gu01pc73brt80g"' in text
    assert "WOW_ACTION_API_KEY" not in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"can_execute":False' in text or '"can_execute": False' in text
    assert '"deploy_authority":"GOVERNED_RENDER_API_HANDOFF"' in text
    assert 'render_json(\n                  "POST"' in text
    assert '{"clearCache":"do_not_clear"}' in text


def test_render_deploy_handoff_is_idempotent_and_exact_sha_verified():
    text = _workflow_text()

    assert "/deploys?limit=20" in text
    assert "commit_sha(deploy) == target_sha" in text
    assert "EXACT_SHA_ALREADY_LIVE" in text
    assert "EXACT_SHA_RENDER_DEPLOY_LIVE" in text
    assert "RENDER_DEPLOY_WRONG_SHA" in text
    assert "RENDER_TARGET_DEPLOY_TERMINAL_FAILURE" in text
    assert "DEPLOY_SUPERSEDED_BY_NEW_MAIN" in text
    assert "deploy_deadline = time.time() + 900" in text
    assert "time.sleep(10)" in text


def test_render_deploy_handoff_reconciles_successful_create_without_top_level_id():
    text = _workflow_text()

    assert 'isinstance(created.get("deploy"), dict)' in text
    assert 'created_deploy = created["deploy"]' in text
    assert 'deploy_id = ""' in text
    assert "RENDER_DEPLOY_CREATE_INVALID" not in text
    assert "if target is None and matching:" in text
    assert "target = matching[0]" in text


def test_render_deploy_list_unwraps_render_pagination_entries():
    text = _workflow_text()

    assert 'isinstance(item.get("deploy"), dict)' in text
    assert 'deploys.append(item["deploy"])' in text
    assert "elif isinstance(item, dict):" in text
    assert "deploys.append(item)" in text


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
