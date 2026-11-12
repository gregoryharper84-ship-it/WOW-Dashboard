from pathlib import Path
import io
import json
import os
import re
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
import urllib.error
import urllib.request


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


def _verify_workflow_text() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / ".github" / "workflows" / "wow-verify.yml").read_text(encoding="utf-8")


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
    assert 'journal_preflight["payload"]' in text
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
    assert "deploy_deadline = time.time() + 2700" not in text
    assert "timeout-minutes: 55" not in text
    assert "role_deadline = time.time() + 900" in text
    assert "QA_RELEASE_DECISION_TIMEOUT" in text
    assert "plan_release_admission(" in text
    assert "journal.release_lock(render_service_id, target_sha)" in text
    assert "time.sleep(10)" in text
    assert "MAX_WORKFLOW_RUN_ATTEMPTS = 3" in text
    assert "cancel-in-progress: false" in text
    assert 'f"/services/{service_path}/rollback"' not in text
    assert "ROLLBACK_AUTHORIZATION_REQUIRED_HOLD" in text
    assert '"rollback_attempts": 0' in text
    assert "DEPLOY_STILL_BUILDING_OBSERVATION_TIMED_OUT" in text
    assert "journal_preflight = plan_release_admission(" in text
    assert "RELEASE_AUTHORIZATION_HOLD_NO_DEPLOY" in text


def test_ci_completion_has_only_one_protected_release_handoff():
    deploy = _workflow_text()
    verify = _verify_workflow_text()
    triggers = deploy.split("\npermissions:", 1)[0]

    assert triggers.count("\n  workflow_run:\n") == 1
    assert 'workflows: ["wow-verify"]' in triggers
    assert "types: [completed]" in triggers
    assert "branches: [main]" in triggers
    assert "workflow_dispatch:" in triggers
    assert "dispatch-protected-deploy:" not in verify
    assert "gh workflow run wow-v17-render-production-deploy.yml" not in verify
    assert "actions: write" not in verify


def test_manual_and_push_ci_keep_the_required_regression_producers():
    repo_root = Path(__file__).resolve().parents[3]
    verify = _verify_workflow_text()
    engine_verify = (repo_root / ".github" / "workflows" / "wow-engine-verify.yml").read_text(
        encoding="utf-8"
    )
    triggers = verify.split("\npermissions:", 1)[0]

    assert "\n  push:\n" in triggers
    assert "\n  workflow_dispatch:\n" in triggers
    assert "name: WOW required-three regression" in verify
    assert "name: WOW additional required regression" in verify
    assert "name: WOW governed probability backend" in engine_verify


def test_morning_green_resumes_ci_and_uses_the_single_workflow_run_handoff():
    repo_root = Path(__file__).resolve().parents[3]
    morning_green = (
        repo_root / ".github" / "workflows" / "wow-v17-morning-green-continuation.yml"
    ).read_text(encoding="utf-8")
    deploy = _workflow_text()

    assert "gh workflow run wow-verify.yml --repo \"$GITHUB_REPOSITORY\" --ref main" in morning_green
    assert "gh workflow run wow-v17-render-production-deploy.yml" not in morning_green
    assert 'workflows: ["wow-verify"]' in deploy


def test_repeated_attempts_remain_inside_the_protected_single_writer_fence():
    text = _workflow_text()
    verify = _verify_workflow_text()

    assert "group: wow-v17-render-production-deploy" in text
    assert "cancel-in-progress: false" in text
    assert "journal_preflight = plan_release_admission(" in text
    assert "journal.release_lock(render_service_id, target_sha)" in text
    assert "RELEASE_RENDER_CREDENTIAL_SCOPE_UNATTESTED" in text
    assert "dispatch-protected-deploy:" not in verify
    assert "gh workflow run wow-v17-render-production-deploy.yml" not in verify


def test_render_deploy_post_and_live_readback_recheck_authorized_main_sha():
    text = _workflow_text()

    assert '"commitId": commit_sha' in text
    assert text.index('if not is_current_main_sha(target_sha, latest_main_sha):') < text.index(
        'journal_preflight["payload"]'
    )
    assert text.index('if not is_current_main_sha(target_sha, live_main_sha):') < text.index('"status":"EXACT_SHA_RENDER_DEPLOY_LIVE"')
    assert '"/branches/main"' in text
    assert "DEPLOY_LEASE_HOLD_NO_DEPLOY" in text


def test_journal_preflight_holds_before_any_render_post():
    text = _workflow_text()

    assert text.index("journal_preflight = plan_release_admission(") < text.index(
        "created = guarded_render_post("
    )
    assert text.index('if journal_preflight["action"] == "HOLD":') < text.index(
        "created = guarded_render_post("
    )
    assert 'RENDER_BLUEPRINT_AUTO_DEPLOY_TRIGGER = "checksPass"' in text
    assert "RELEASE_JOURNAL_PREFLIGHT_HOLD_NO_DEPLOY" in text
    assert "RELEASE_RENDER_CREDENTIAL_SCOPE_UNATTESTED" in text
    assert "service_lock_release_cas_verified" not in text
    assert "updateRefs" not in text
    assert "github_graphql" in text
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text
    assert "environment: wow-release" in text
    assert "contents: write" in text
    assert "qa_app_id = str(AUTHENTIC_QA_APP_ID)" in text
    assert "release_app_id = str(AUTHENTIC_RELEASE_APP_ID)" in text


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
    assert "RELEASE_RENDER_CREDENTIAL_SCOPE_UNATTESTED" in _workflow_text()
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
    # Preflight GET is observational; run attribution uses POST deployed_sha.
    assert "read_serving_sha_at_request" in text
    assert "serving_sha_preflight" in text
    assert "serving_sha_at_request" in text
    assert "DAILY_SNAPSHOT_SERVING_SHA_MISSING" in text
    assert "DAILY_SNAPSHOT_RUN_SERVING_SHA_INVALID" in text
    assert "DAILY_SNAPSHOT_RUN_ID_MISSING" in text
    assert 'daily_snapshot_response_deployed_sha' in text
    assert "serving_sha_for_run = str(result.get(\"deployed_sha\") or \"\").strip().lower()" in text
    assert '"serving_sha_at_request": serving_sha_for_run' in text
    assert "serving_sha_preflight_matches_post" in text
    assert 'origin + "/governance"' in text or 'rstrip("/") + "/governance"' in text
    # Preflight GET uses the same 40-hex gate as POST attribution (provenance only).
    assert 'serving_sha = str(governance.get("deployed_sha") or "").strip().lower()' in text
    assert "len(serving_sha) != 40" in text
    # Governance preflight must retry cold-start / gateway failures like the Daily POST.
    assert "def read_serving_sha_at_request" in text
    assert text.count("except (urllib.error.URLError, TimeoutError):") >= 2
    assert "max_attempts = 10" in text


def _attribute_daily_serving_sha(*, preflight_sha: str, result: dict) -> dict:
    """Mirror the unpaid Daily summary attribution contract from the workflow."""
    run_id = str(result.get("run_id") or "").strip()
    if not run_id:
        raise AssertionError("DAILY_SNAPSHOT_RUN_ID_MISSING")
    serving_sha_for_run = str(result.get("deployed_sha") or "").strip().lower()
    if (
        len(serving_sha_for_run) != 40
        or any(ch not in "0123456789abcdef" for ch in serving_sha_for_run)
    ):
        raise AssertionError("DAILY_SNAPSHOT_RUN_SERVING_SHA_INVALID")
    return {
        "run_id": run_id,
        "serving_sha_preflight": preflight_sha,
        "serving_sha_at_request": serving_sha_for_run,
        "serving_sha_source": "daily_snapshot_response_deployed_sha",
        "serving_sha_preflight_matches_post": preflight_sha == serving_sha_for_run,
    }


def test_daily_serving_sha_attribution_prefers_post_over_distinct_preflight():
    preflight = "a" * 40
    post = "b" * 40
    attributed = _attribute_daily_serving_sha(
        preflight_sha=preflight,
        result={"deployed_sha": post, "run_id": "v17-daily-x"},
    )
    assert attributed["serving_sha_preflight"] == preflight
    assert attributed["serving_sha_at_request"] == post
    assert attributed["serving_sha_source"] == "daily_snapshot_response_deployed_sha"
    assert attributed["serving_sha_preflight_matches_post"] is False


def test_daily_serving_sha_attribution_fails_closed_when_post_sha_missing():
    with pytest.raises(AssertionError, match="DAILY_SNAPSHOT_RUN_SERVING_SHA_INVALID"):
        _attribute_daily_serving_sha(
            preflight_sha="a" * 40,
            result={"deployed_sha": None, "run_id": "v17-daily-x"},
        )


def test_daily_serving_sha_attribution_fails_closed_on_nonhex_post_sha():
    with pytest.raises(AssertionError, match="DAILY_SNAPSHOT_RUN_SERVING_SHA_INVALID"):
        _attribute_daily_serving_sha(
            preflight_sha="a" * 40,
            result={"deployed_sha": "g" * 40, "run_id": "v17-daily-x"},
        )


def test_daily_serving_sha_attribution_fails_closed_on_missing_run_id():
    with pytest.raises(AssertionError, match="DAILY_SNAPSHOT_RUN_ID_MISSING"):
        _attribute_daily_serving_sha(
            preflight_sha="a" * 40,
            result={"deployed_sha": "b" * 40, "run_id": ""},
        )


def _daily_snapshot_workflow_python() -> str:
    """Extract the unpaid Daily snapshot step Python from the workflow YAML."""
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-daily-snapshot.yml").read_text(
        encoding="utf-8"
    )
    marker = "Run server-owned Daily snapshot"
    assert marker in text
    section = text.split(marker, 1)[1]
    match = re.search(r"python - <<'PY'\n(.*?)^\s+PY\s*$", section, re.M | re.S)
    assert match, "Daily snapshot Python heredoc missing"
    # YAML indent is 10 spaces before each code line; strip common indent.
    lines = match.group(1).splitlines()
    stripped = []
    for line in lines:
        if line.startswith("          "):
            stripped.append(line[10:])
        else:
            stripped.append(line)
    return "\n".join(stripped) + "\n"


class _JsonResponse:
    def __init__(self, payload: dict):
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self._buf

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


def _mock_urlopen_factory(*, governance: dict, daily: dict | Exception):
    def _urlopen(request, timeout=0):  # noqa: ARG001
        if isinstance(request, str):
            url = request
            method = "GET"
        else:
            url = request.full_url
            method = request.get_method()
        if url.endswith("/governance") and method == "GET":
            return _JsonResponse(governance)
        if "/internal/v17/daily-snapshot" in url and method == "POST":
            if isinstance(daily, Exception):
                raise daily
            return _JsonResponse(daily)
        if "idtoken" in url.lower() or "audience=" in url:
            return _JsonResponse({"value": "oidc-token"})
        raise AssertionError(f"unexpected urlopen target: {url} {method}")

    return _urlopen


def _valid_governance(**overrides) -> dict:
    payload = {
        "deployed_sha": "a" * 40,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
    }
    payload.update(overrides)
    return payload


def _valid_daily(**overrides) -> dict:
    payload = {
        "run_id": "v17-daily-exec-1",
        "run_status": "COMPLETED",
        "requested_slate_date": "2026-11-10",
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "automation_auth": "GITHUB_ACTIONS_OIDC",
        "deployed_sha": "b" * 40,
        "rows": [{"lane": "MONEYLINE"}] * 24,
        "reconciliation": {
            "balanced": True,
            "rows_completed": 0,
            "rows_held": 24,
            "rows_rejected": 0,
        },
        "lane_reconciliation": {
            "MONEYLINE": {"requested_row_limit": 24, "canonicalized_count": 24}
        },
        "cross_sport_discovery_audit": {
            "reconciliation": {"row_reconciliation": "PASS"}
        },
        "blockers": [],
    }
    payload.update(overrides)
    return payload


def _run_daily_snapshot_workflow_python(*, governance: dict, daily: dict | Exception) -> str:
    code = _daily_snapshot_workflow_python()
    env = {
        "WOW_ACTION_ORIGIN": "https://wow-governed-probability-engine.onrender.com",
        "WOW_USER_TIMEZONE": "America/Chicago",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.test/idtoken?audience=x",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "token",
    }
    stdout = io.StringIO()
    with patch.dict(os.environ, env, clear=False):
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen_factory(governance=governance, daily=daily)):
            with redirect_stdout(stdout):
                exec(compile(code, "wow-v17-daily-snapshot-step.py", "exec"), {})
    return stdout.getvalue()


def test_daily_workflow_python_attributes_distinct_post_sha():
    preflight = "a" * 40
    post = "b" * 40
    out = _run_daily_snapshot_workflow_python(
        governance=_valid_governance(deployed_sha=preflight),
        daily=_valid_daily(deployed_sha=post, run_id="v17-daily-exec-1"),
    )
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary["serving_sha_preflight"] == preflight
    assert summary["serving_sha_at_request"] == post
    assert summary["serving_sha_preflight_matches_post"] is False
    assert summary["run_id"] == "v17-daily-exec-1"
    assert summary["can_execute"] is False


def test_daily_workflow_python_rejects_missing_post_sha_and_logs_run_id():
    code = _daily_snapshot_workflow_python()
    env = {
        "WOW_ACTION_ORIGIN": "https://wow-governed-probability-engine.onrender.com",
        "WOW_USER_TIMEZONE": "America/Chicago",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.test/idtoken?audience=x",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "token",
    }
    stdout = io.StringIO()
    with pytest.raises(AssertionError, match="DAILY_SNAPSHOT_RUN_SERVING_SHA_INVALID"):
        with patch.dict(os.environ, env, clear=False):
            with patch(
                "urllib.request.urlopen",
                side_effect=_mock_urlopen_factory(
                    governance=_valid_governance(),
                    daily=_valid_daily(deployed_sha=None, run_id="v17-daily-legacy"),
                ),
            ):
                with redirect_stdout(stdout):
                    exec(compile(code, "wow-v17-daily-snapshot-step.py", "exec"), {})
    diagnostic = json.loads(stdout.getvalue().strip().splitlines()[-1])
    assert diagnostic == {
        "diagnostic": "DAILY_SNAPSHOT_RUN_SERVING_SHA_INVALID",
        "run_id": "v17-daily-legacy",
    }


@pytest.mark.parametrize(
    ("governance", "daily", "match"),
    [
        (
            _valid_governance(),
            _valid_daily(deployed_sha="g" * 40),
            "DAILY_SNAPSHOT_RUN_SERVING_SHA_INVALID",
        ),
        (
            _valid_governance(),
            _valid_daily(run_id=""),
            "DAILY_SNAPSHOT_RUN_ID_MISSING",
        ),
        (
            _valid_governance(deployed_sha=""),
            _valid_daily(),
            "DAILY_SNAPSHOT_SERVING_SHA_MISSING",
        ),
        (
            _valid_governance(deployed_sha="unknown"),
            _valid_daily(),
            "DAILY_SNAPSHOT_SERVING_SHA_MISSING",
        ),
        (
            _valid_governance(can_execute=True),
            _valid_daily(),
            "DAILY_SNAPSHOT_GOVERNANCE_CAN_EXECUTE_INVARIANT_FAILED",
        ),
        (
            _valid_governance(global_terminal_authority="OTHER"),
            _valid_daily(),
            "DAILY_SNAPSHOT_GOVERNANCE_TERMINAL_AUTHORITY_MISMATCH",
        ),
    ],
)
def test_daily_workflow_python_guard_rejects(governance, daily, match):
    with pytest.raises(AssertionError, match=match):
        _run_daily_snapshot_workflow_python(governance=governance, daily=daily)


def test_post_deploy_canary_classifier_accepts_observation_timeout_as_hold():
    """A typed slow-build receipt must never be misreported as unknown or LIVE."""
    text = _workflow_text()
    classifier = text.split("- name: Fail closed when no exact deployment advanced", 1)[1]
    non_advancing = classifier.split("non_advancing = {", 1)[1].split("}", 1)[0]
    advancing = classifier.split("advancing = {", 1)[1].split("}", 1)[0]
    assert '"DEPLOY_STILL_BUILDING_OBSERVATION_TIMED_OUT"' in non_advancing
    assert '"DEPLOY_STILL_BUILDING_OBSERVATION_TIMED_OUT"' not in advancing
    assert '"status":"POST_DEPLOY_CANARIES_BLOCKED"' in classifier
    assert "raise SystemExit(1)" in classifier
