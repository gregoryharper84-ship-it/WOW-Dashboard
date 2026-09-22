from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from mcp_acceptance_auth import ACCEPTANCE_MARKER, build_action_auth_dependency


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


def _primary_auth(authorization: str | None) -> None:
    if authorization == "Bearer production-key":
        return
    raise HTTPException(status_code=401, detail="Invalid credential.")


def _auth():
    return build_action_auth_dependency(_primary_auth)


def test_wrapped_dependency_preserves_canonical_action_auth_identity():
    assert _auth().__name__ == "_require_action_api_key"


def test_primary_action_key_is_unchanged_when_acceptance_is_disabled(monkeypatch):
    monkeypatch.delenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("WOW_MCP_ACCEPTANCE_API_KEY", raising=False)
    _auth()(_request("/anything"), "Bearer production-key", None)


def test_acceptance_key_is_inert_unless_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_API_KEY", "acceptance-key")
    monkeypatch.delenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", raising=False)
    with pytest.raises(HTTPException) as exc:
        _auth()(_request("/v17/host-contract"), "Bearer acceptance-key", ACCEPTANCE_MARKER)
    assert exc.value.status_code == 401


def test_acceptance_key_requires_marker(monkeypatch):
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", "1")
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_API_KEY", "acceptance-key")
    with pytest.raises(HTTPException) as exc:
        _auth()(_request("/v17/host-contract"), "Bearer acceptance-key", None)
    assert exc.value.status_code == 403
    assert exc.value.detail == "MCP acceptance marker required."


def test_acceptance_key_allows_only_declared_mcp_path(monkeypatch):
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", "1")
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_API_KEY", "acceptance-key")
    _auth()(_request("/score-team-event"), "Bearer acceptance-key", ACCEPTANCE_MARKER)

    with pytest.raises(HTTPException) as exc:
        _auth()(_request("/admin/anything"), "Bearer acceptance-key", ACCEPTANCE_MARKER)
    assert exc.value.status_code == 403
    assert exc.value.detail == "MCP acceptance credential is not authorized for this path."


def test_acceptance_key_allows_daily_row_detail_dynamic_path(monkeypatch):
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", "1")
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_API_KEY", "acceptance-key")
    _auth()(
        _request("/v17/daily-snapshot-run/acceptance-run-123/rows"),
        "Bearer acceptance-key",
        ACCEPTANCE_MARKER,
    )


def test_wrong_acceptance_key_remains_unauthorized(monkeypatch):
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", "1")
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_API_KEY", "acceptance-key")
    with pytest.raises(HTTPException) as exc:
        _auth()(_request("/v17/host-contract"), "Bearer wrong-key", ACCEPTANCE_MARKER)
    assert exc.value.status_code == 401
