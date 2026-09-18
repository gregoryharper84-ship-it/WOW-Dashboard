from __future__ import annotations

import json

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from v17.claude_runtime import (
    ClaudeAdvisoryRequest,
    ClaudeRuntime,
    claude_runtime_readiness,
    install_claude_runtime_routes,
)


def _ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"request-id": "req_test_123"},
        json={
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "OK"}],
            "usage": {"input_tokens": 10, "output_tokens": 1},
        },
        request=request,
    )


def test_readiness_separates_oauth_from_messages_api(monkeypatch):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-super-secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    state = claude_runtime_readiness()

    assert state["enabled"] is True
    assert state["configured"] is False
    assert state["messages_api_configured"] is False
    assert state["claude_code_oauth_configured"] is True
    assert state["auth_mode"] == "none"
    assert state["probability_authority"] is False
    assert state["terminal_authority"] is False
    assert state["can_execute"] is False
    assert "oauth-super-secret" not in json.dumps(state)


def test_oauth_alone_never_authenticates_messages_api(monkeypatch):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    runtime = ClaudeRuntime(transport=httpx.MockTransport(_ok_response))
    with pytest.raises(HTTPException) as exc_info:
        runtime.probe()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "CLAUDE_MESSAGES_API_KEY_MISSING",
        "claude_code_oauth_configured": True,
        "can_execute": False,
    }
    assert "oauth-token" not in str(exc_info.value.detail)


def test_api_key_uses_x_api_key_even_when_oauth_is_present(monkeypatch):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "api-key"
        assert "authorization" not in request.headers
        assert request.headers["anthropic-version"] == "2023-06-01"
        payload = json.loads(request.content)
        assert payload["model"] == "claude-sonnet-4-6"
        assert "NOT a fitted sports probability model" in payload["system"]
        return _ok_response(request)

    runtime = ClaudeRuntime(transport=httpx.MockTransport(handler))
    result = runtime.advisory(
        ClaudeAdvisoryRequest(
            purpose="engineering",
            prompt="Check this failure",
            max_tokens=20,
        )
    )

    assert result["status"] == "COMPLETE"
    assert result["auth_mode"] == "api_key"
    assert result["request_id"] == "req_test_123"
    assert result["probability_authority"] is False
    assert result["terminal_authority"] is False
    assert result["can_execute"] is False


def test_disabled_runtime_fails_closed_without_network(monkeypatch):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key")
    runtime = ClaudeRuntime(transport=httpx.MockTransport(_ok_response))

    with pytest.raises(HTTPException) as exc_info:
        runtime.probe()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "CLAUDE_RUNTIME_DISABLED"
    assert exc_info.value.detail["can_execute"] is False


def test_upstream_error_is_sanitized(monkeypatch):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers={"request-id": "req_denied"},
            json={"error": {"message": "do-not-reflect-this-body"}},
            request=request,
        )

    runtime = ClaudeRuntime(transport=httpx.MockTransport(handler))
    with pytest.raises(HTTPException) as exc_info:
        runtime.probe()

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {
        "code": "CLAUDE_UPSTREAM_HTTP_401",
        "upstream_status": 401,
        "request_id": "req_denied",
        "can_execute": False,
    }
    assert "do-not-reflect-this-body" not in str(exc_info.value.detail)
    assert "api-key-secret" not in str(exc_info.value.detail)


def test_routes_are_authenticated_idempotent_and_fail_closed_without_api_key(monkeypatch):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    app = FastAPI()

    def auth():
        return True

    runtime = ClaudeRuntime(transport=httpx.MockTransport(_ok_response))
    install_claude_runtime_routes(app, auth_dependency=Depends(auth), runtime=runtime)
    install_claude_runtime_routes(app, auth_dependency=Depends(auth), runtime=runtime)

    paths = [getattr(route, "path", None) for route in app.router.routes]
    assert paths.count("/internal/claude/readiness") == 1
    assert paths.count("/internal/claude/advisory") == 1

    client = TestClient(app)
    readiness = client.get("/internal/claude/readiness?probe=true")
    assert readiness.status_code == 200
    body = readiness.json()
    assert body["configured"] is False
    assert body["messages_api_configured"] is False
    assert body["claude_code_oauth_configured"] is True
    assert body["probe_status"] == "NOT_RUN"
    assert body["can_execute"] is False

    advisory = client.post(
        "/internal/claude/advisory",
        json={"purpose": "research", "prompt": "Summarize", "max_tokens": 10},
    )
    assert advisory.status_code == 503
    assert advisory.json()["detail"]["code"] == "CLAUDE_MESSAGES_API_KEY_MISSING"


def test_routes_probe_and_advisory_work_with_api_key(monkeypatch):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    app = FastAPI()

    def auth():
        return True

    runtime = ClaudeRuntime(transport=httpx.MockTransport(_ok_response))
    install_claude_runtime_routes(app, auth_dependency=Depends(auth), runtime=runtime)
    client = TestClient(app)

    readiness = client.get("/internal/claude/readiness?probe=true")
    assert readiness.status_code == 200
    body = readiness.json()
    assert body["messages_api_configured"] is True
    assert body["claude_code_oauth_configured"] is False
    assert body["auth_mode"] == "api_key"
    assert body["probe_status"] == "PASS"

    advisory = client.post(
        "/internal/claude/advisory",
        json={"purpose": "research", "prompt": "Summarize", "max_tokens": 10},
    )
    assert advisory.status_code == 200
    assert advisory.json()["text"] == "OK"
    assert advisory.json()["auth_mode"] == "api_key"
    assert advisory.json()["can_execute"] is False
