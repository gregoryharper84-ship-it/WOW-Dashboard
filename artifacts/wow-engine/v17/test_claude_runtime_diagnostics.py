from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException

from v17.claude_runtime import ClaudeRuntime, claude_runtime_readiness, emit_claude_startup_readiness_receipt


def _run_failure(monkeypatch, *, status: int, message: str, details: dict | None = None):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key-secret")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"request-id": "req_diag"},
            json={"error": {"type": "invalid_request_error", "message": message, "details": details}},
            request=request,
        )

    runtime = ClaudeRuntime(transport=httpx.MockTransport(handler))
    try:
        runtime.probe()
    except HTTPException as exc:
        return exc
    raise AssertionError("probe unexpectedly succeeded")


def test_readiness_reports_workspace_presence_without_exposing_id(monkeypatch):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key-secret")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_secret_value")

    state = claude_runtime_readiness()

    assert state["workspace_id_configured"] is True
    assert "wrkspc_secret_value" not in str(state)
    assert "api-key-secret" not in str(state)


def test_invalid_workspace_error_is_typed_without_reflecting_body(monkeypatch):
    message = "anthropic-workspace-id header must be a valid workspace ID. secret-do-not-reflect"
    exc = _run_failure(monkeypatch, status=400, message=message)

    assert exc.detail["code"] == "CLAUDE_WORKSPACE_ID_INVALID"
    assert exc.detail["upstream_status"] == 400
    assert "secret-do-not-reflect" not in str(exc.detail)


def test_workspace_required_error_is_typed(monkeypatch):
    exc = _run_failure(
        monkeypatch,
        status=400,
        message="anthropic-workspace-id is required when authenticating with an identity-linked API key",
    )

    assert exc.detail["code"] == "CLAUDE_WORKSPACE_ID_REQUIRED"


def test_workspace_spend_limit_error_is_typed(monkeypatch):
    exc = _run_failure(
        monkeypatch,
        status=400,
        message="You have reached your specified workspace API usage limits; access resumes later",
    )

    assert exc.detail["code"] == "CLAUDE_WORKSPACE_SPEND_LIMIT_REACHED"


def test_org_spend_limit_error_is_typed(monkeypatch):
    exc = _run_failure(
        monkeypatch,
        status=400,
        message="You have reached your specified API usage limits; access resumes later",
    )

    assert exc.detail["code"] == "CLAUDE_ORG_SPEND_LIMIT_REACHED"


def test_startup_receipt_logs_workspace_presence_not_value(monkeypatch, caplog):
    monkeypatch.setenv("WOW_CLAUDE_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "api-key-secret")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_secret_value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "anthropic-workspace-id header must be a valid workspace ID."}},
            request=request,
        )

    with caplog.at_level(logging.WARNING, logger="wow.claude_runtime"):
        receipt = emit_claude_startup_readiness_receipt(
            ClaudeRuntime(transport=httpx.MockTransport(handler))
        )

    assert receipt["workspace_id_configured"] is True
    assert receipt["error_code"] == "CLAUDE_WORKSPACE_ID_INVALID"
    assert "workspace_id_configured=true" in caplog.text
    assert "wrkspc_secret_value" not in caplog.text
    assert "api-key-secret" not in caplog.text
