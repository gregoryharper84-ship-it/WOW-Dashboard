from pathlib import Path
import re


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
    assert "if not is_current_main_sha(target_sha, main_sha):" in text
    for check_name in REQUIRED_CHECKS:
        assert check_name in text


def test_render_deploy_handoff_uses_control_plane_only_after_governance_gate():
    text = _workflow_text()

    assert 'RENDER_API_KEY: ${{ secrets.RENDER_API_KEY }}' in text
    assert 'WOW_RENDER_SERVICE_ID: "srv-da7sa9gu01pc73brt80g"' in text
    assert "WOW_ACTION_API_KEY" not in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert "actions: read" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"can_execute":False' in text or '"can_execute": False' in text
    assert '"deploy_authority":"GOVERNED_RENDER_API_HANDOFF"' in text
    assert "return render_json(\"POST\", path, payload)" in text
    assert 'render_deploy_payload(target_sha)' in text
    assert '"commitId": commit_sha' in text


def test_render_deploy_handoff_is_idempotent_and_exact_sha_verified():
    text = _workflow_text()

    assert "group: wow-v17-render-production-deploy" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 120" in text
    assert "/deploys?limit=20" in text
    assert "commit_sha(deploy) == target_sha" in text
    assert "EXACT_SHA_ALREADY_LIVE" in text
    assert "EXACT_SHA_RENDER_DEPLOY_LIVE" in text
    assert "previous_live, created_sha, str((created_deploy or {}).get" in text
    assert "RENDER_TARGET_DEPLOY_TERMINAL_FAILURE" in text
    assert "DEPLOY_SUPERSEDED_BY_NEW_MAIN" in text
    assert "deploy_deadline = time.monotonic() + 90 * 60" in text
    assert "time.sleep(10)" in text
    assert "MAX_WORKFLOW_RUN_ATTEMPTS = 3" in text
    assert "cancel-in-progress: false" in text
    assert 'f"/services/{service_path}/rollback"' not in text
    assert "ROLLBACK_AUTHORIZATION_REQUIRED_HOLD" in text
    assert '"rollback_attempts": 0' in text
    assert "DEPLOY_STILL_BUILDING_OBSERVATION_TIMED_OUT" in text
    assert "journal_preflight = github_deployment_journal_preflight(" in text
    assert "RELEASE_AUTHORIZATION_HOLD_NO_DEPLOY" in text


def test_render_deploy_post_and_live_readback_recheck_authorized_main_sha():
    text = _workflow_text()

    assert '"commitId": commit_sha' in text
    assert text.index('if not is_current_main_sha(target_sha, latest_main_sha):') < text.index('render_deploy_payload(target_sha)')
    assert text.index('if not is_current_main_sha(target_sha, live_main_sha):') < text.index('"status":"EXACT_SHA_RENDER_DEPLOY_LIVE"')
    assert '"/branches/main"' in text
    assert "DEPLOY_LEASE_HOLD_NO_DEPLOY" in text


def test_journal_preflight_holds_before_any_render_post():
    text = _workflow_text()

    assert text.index("journal_preflight = github_deployment_journal_preflight(") < text.index(
        "def render_json("
    )
    assert text.index("raise SystemExit(0)", text.index("journal_preflight =")) < text.index(
        "created = guarded_render_post("
    )
    assert 'RENDER_BLUEPRINT_AUTO_DEPLOY_TRIGGER = "checksPass"' in text
    assert "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY" in text
    assert "GITHUB_DEPLOYMENT_CREATE_HAS_NO_ATOMIC_IDEMPOTENCY" in text
    assert '"live_render_auto_deploy_trigger": "UNVERIFIED"' in text
    assert '"obstruction": "GITHUB_NATIVE_FENCE_CANNOT_PROVE_SERVICE_WIDE_EXACT_ONCE"' in text
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text
    assert "until protected ref policy, owner-fenced service-lock release, and complete live writer inventory are independently verified" in text


def test_secret_sync_request_enters_only_protected_controller_after_durable_artifact():
    text = _workflow_text()
    sync_workflow = (
        Path(__file__).resolve().parents[3] / ".github/workflows/wow-v17-secret-sync.yml"
    ).read_text(encoding="utf-8")

    assert "secret_sync_request_run_id:" in text
    assert "secret_sync_request_run_attempt:" in text
    assert "secret_sync_request_sha:" in text
    assert "validate_secret_sync_release_request(" in text
    assert "SECRET_ENV_CHANGED_RELEASE_REQUEST_HOLD_NO_DEPLOY" in text
    assert "source_sha != target_sha" in text
    assert sync_workflow.index("Persist typed secret-change release request") < sync_workflow.index(
        "Dispatch protected Render release controller"
    )


def test_preflight_distinguishes_blueprint_from_live_render_writer_state():
    repo_root = Path(__file__).resolve().parents[3]
    render = (repo_root / "render.yaml").read_text(encoding="utf-8")
    manifest = (
        repo_root / "artifacts/wow-engine/v17/secret_sync_manifest.json"
    ).read_text(encoding="utf-8")
    sync_workflow = (
        repo_root / ".github/workflows/wow-v17-secret-sync.yml"
    ).read_text(encoding="utf-8")
    secret_sync = (
        repo_root / "artifacts/wow-engine/v17/secret_sync.py"
    ).read_text(encoding="utf-8")

    assert 'autoDeployTrigger: "checksPass"' in render
    assert 'RENDER_BLUEPRINT_AUTO_DEPLOY_TRIGGER = "checksPass"' in _workflow_text()
    assert '"live_render_auto_deploy_trigger": "UNVERIFIED"' in _workflow_text()
    assert '"render_service_id": "srv-da7sa9gu01pc73brt80g"' in manifest
    assert "wow-v17-secret-sync" in sync_workflow
    assert "/deploys" not in secret_sync


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


def test_render_deploy_receipt_is_durable_and_pointer_reconciler_is_governed():
    text = _workflow_text()

    assert "deployments: write" in text
    assert "wow-v17-render-deploy-receipt.json" in text
    assert "def emit_receipt(payload):" in text
    assert 'body["workflow_run_id"]' in text
    assert 'body["workflow_run_attempt"]' in text
    assert "Reconcile exact Render receipt to durable deployment pointer" in text
    assert "python artifacts/wow-engine/v17/render_deployment_pointer.py" in text
    assert "actions/checkout@v4" in text
    assert "ref: main" in text


def test_render_deploy_no_deploy_paths_emit_typed_receipts_before_exit():
    text = _workflow_text()

    for status in (
        "NON_MAIN_NO_DEPLOY",
        "UPSTREAM_NOT_SUCCESS_NO_DEPLOY",
        "STALE_SHA_NO_DEPLOY",
        "DEPLOY_SUPERSEDED_BY_NEW_MAIN",
        "SECRET_ENV_CHANGED_RELEASE_REQUEST_HOLD_NO_DEPLOY",
    ):
        assert re.search(
            rf'emit_receipt\(\{{\s*"status"\s*:\s*"{status}".*?raise SystemExit\(0\)',
            text,
            re.DOTALL,
        )
    assert '"status": journal_preflight["status"]' in text
    assert '"RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY"' in text


def test_daily_snapshot_has_deploy_handoff_runway_and_network_retries():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-daily-snapshot.yml").read_text(
        encoding="utf-8"
    )

    assert "timeout-minutes: 40" in text
    assert "workflow_call:" in text
    assert "post_deploy_acceptance:" in text
    assert "\n  push:\n" not in text
    assert "if: inputs.post_deploy_acceptance == true" in text
    assert "max_attempts = 90" in text
    assert "retry_delay_seconds = 20" in text
    assert "except (urllib.error.URLError, TimeoutError):" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
