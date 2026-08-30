from pathlib import Path

from fastapi.testclient import TestClient

import api_ncaaf_acceptance as api
from ncaaf_closing_capture import CaptureResult


def test_ncaaf_capture_boundary_is_authenticated(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    client = TestClient(api.app)
    response = client.post("/internal/ncaaf/capture-closing-lines")
    assert response.status_code == 401


def test_ncaaf_capture_boundary_success_is_research_only(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    monkeypatch.setattr(
        api,
        "run_from_environment",
        lambda: CaptureResult(
            status="PASS",
            candidates_checked=2,
            quotes_captured=2,
            no_close_marked=0,
            provider_failures=0,
            identity_failures=0,
            stale_quote_failures=0,
        ),
    )
    client = TestClient(api.app)
    response = client.post(
        "/internal/ncaaf/capture-closing-lines",
        headers={"Authorization": "Bearer test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["quotes_captured"] == 2
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False


def test_ncaaf_capture_boundary_feed_missing_fails_closed(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")

    def fail():
        raise RuntimeError("WOW_NCAAF_MARKET_FEED_URL is required; synthetic closing lines are prohibited")

    monkeypatch.setattr(api, "run_from_environment", fail)
    client = TestClient(api.app)
    response = client.post(
        "/internal/ncaaf/capture-closing-lines",
        headers={"Authorization": "Bearer test-key"},
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "NCAAF_CLOSING_FEED_UNCONFIGURED"
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_ncaaf_cron_contract_is_five_minute_and_vault_backed():
    sql = Path("ncaaf_closing_capture_cron.sql").read_text()
    lowered = sql.lower()
    assert "wow-ncaaf-closing-line-capture" in sql
    assert "*/5 * * * *" in sql
    assert "vault.decrypted_secrets" in sql
    assert "wow_render_service_url" in sql
    assert "wow_action_api_key" in sql
    assert "/internal/ncaaf/capture-closing-lines" in sql
    assert "can_execute',false" in sql
    assert "security invoker" in lowered
    assert "security definer" not in lowered
    assert "revoke all privileges on function public.wow_ncaaf_trigger_closing_capture()" in lowered
    assert "from public, anon, authenticated, service_role" in lowered


def test_render_layers_live_wrapper_over_ncaaf_acceptance_without_auto_deploy():
    render = Path("../../render.yaml").read_text()
    wrapper = Path("api_live_upset.py").read_text()
    assert "uvicorn api_live_upset:app" in render
    assert "import api_ncaaf_acceptance as base" in wrapper
    assert "app = base.app" in wrapper
    assert 'autoDeployTrigger: "off"' in render
    assert "WOW_NCAAF_MARKET_FEED_URL" in render
