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
    ):
        marker = f'"status":"{status}"'
        assert marker in text
        assert text.index("emit_receipt({", text.index(marker) - 40) < text.index("raise SystemExit(0)", text.index(marker))


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


def _github_json_function(monkeypatch, error):
    import email.message
    import json
    import textwrap
    import urllib.error
    import urllib.request

    text = _workflow_text()
    start = text.index("          def github_json(path):")
    end = text.index("          def validate_release_provenance", start)
    source = textwrap.dedent(text[start:end])
    namespace = {
        "json": json,
        "urllib": urllib,
        "repo": "owner/repo",
        "github_headers": {"Authorization": "******"},
    }
    exec(source, namespace)

    def _raise(*args, **kwargs):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    return namespace["github_json"]


def _http_403(body: bytes):
    import email.message
    import io
    import urllib.error

    headers = email.message.Message()
    headers["x-accepted-github-permissions"] = "checks=read"
    headers["x-ratelimit-remaining"] = "4999"
    return urllib.error.HTTPError(
        "https://api.github.com/repos/owner/repo/commits/abc/check-runs?token=SECRET_TOKEN_VALUE",
        403,
        "Forbidden",
        headers,
        io.BytesIO(body),
    )


def test_github_json_403_json_body_is_sanitized_and_reraised(monkeypatch, capsys):
    import json
    import urllib.error

    import pytest

    body = json.dumps({"message": "SECRET_TOKEN_VALUE", "secret": "SECRET_TOKEN_VALUE"})
    error = _http_403(body.encode("utf-8"))
    github_json = _github_json_function(monkeypatch, error)

    with pytest.raises(urllib.error.HTTPError) as raised:
        github_json("/commits/abc/check-runs?per_page=100")

    assert raised.value is error
    out = capsys.readouterr().out
    assert out.startswith("GITHUB_RELEASE_API_ERROR:")
    payload = json.loads(out.split(":", 1)[1])
    assert payload == {
        "endpoint": "/commits/abc/check-runs",
        "status": 403,
        "message_category": "ACCESS_DENIED",
        "accepted_permissions": "checks=read",
        "rate_limit_remaining": "4999",
    }
    assert "SECRET_TOKEN_VALUE" not in out


def test_github_json_403_invalid_body_is_sanitized_and_reraised(monkeypatch, capsys):
    import json
    import urllib.error

    import pytest

    error = _http_403(b"<html>SECRET_TOKEN_VALUE not json</html>")
    github_json = _github_json_function(monkeypatch, error)

    with pytest.raises(urllib.error.HTTPError) as raised:
        github_json("/commits/abc/check-runs")

    assert raised.value is error
    out = capsys.readouterr().out
    payload = json.loads(out.split(":", 1)[1])
    assert payload["status"] == 403
    assert payload["message_category"] == "ACCESS_DENIED"
    assert "SECRET_TOKEN_VALUE" not in out


def test_github_json_403_rate_limit_is_categorized(monkeypatch, capsys):
    import json
    import urllib.error
    import pytest

    error = _http_403(b'{"message":"SECRET_TOKEN_VALUE"}')
    error.headers.replace_header("x-ratelimit-remaining", "0")
    github_json = _github_json_function(monkeypatch, error)
    with pytest.raises(urllib.error.HTTPError):
        github_json("/commits/abc/check-runs")
    out = capsys.readouterr().out
    assert json.loads(out.split(":", 1)[1])["message_category"] == "RATE_LIMIT"
    assert "SECRET_TOKEN_VALUE" not in out
