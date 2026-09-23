from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

import github_actions_oidc as oidc
from mcp_acceptance_auth import ACCEPTANCE_MARKER, build_action_auth_dependency


def _primary_auth(authorization: str | None = None) -> None:
    if authorization == "Bearer production-key":
        return
    raise HTTPException(status_code=401, detail="Invalid credential.")


def _client() -> TestClient:
    app = FastAPI()
    wrapped = build_action_auth_dependency(_primary_auth)
    combined = oidc.scout_route_auth_dependency(Depends(wrapped))

    @app.get("/score-pick-request", dependencies=[combined])
    def protected_fixture() -> dict[str, object]:
        return {"ok": True, "can_execute": False}

    return TestClient(app)


def test_mcp_acceptance_bearer_survives_oidc_composition(monkeypatch):
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", "1")
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_API_KEY", "acceptance-key")
    response = _client().get(
        "/score-pick-request",
        headers={
            "Authorization": "Bearer acceptance-key",
            "X-WOW-MCP-Acceptance": ACCEPTANCE_MARKER,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "can_execute": False}


def test_primary_action_bearer_remains_first_class_through_oidc_composition(monkeypatch):
    monkeypatch.delenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("WOW_MCP_ACCEPTANCE_API_KEY", raising=False)
    response = _client().get(
        "/score-pick-request",
        headers={"Authorization": "Bearer production-key"},
    )
    assert response.status_code == 200
    assert response.json()["can_execute"] is False


def test_mcp_acceptance_marker_is_not_dropped_by_oidc_composition(monkeypatch):
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", "1")
    monkeypatch.setenv("WOW_MCP_ACCEPTANCE_API_KEY", "acceptance-key")
    response = _client().get(
        "/score-pick-request",
        headers={"Authorization": "Bearer acceptance-key"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "MCP acceptance marker required."


def test_oidc_fallback_remains_available_after_wrapped_auth_rejects(monkeypatch):
    monkeypatch.delenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("WOW_MCP_ACCEPTANCE_API_KEY", raising=False)
    monkeypatch.setattr(
        oidc,
        "authorize_action_key_or_multiscout_oidc",
        lambda authorization: "GITHUB_ACTIONS_OIDC",
    )
    response = _client().get(
        "/score-pick-request",
        headers={"Authorization": "Bearer github-token"},
    )
    assert response.status_code == 200
    assert response.json()["can_execute"] is False


def test_combined_dependency_keeps_authorization_as_first_positional_argument(monkeypatch):
    def reject(authorization=None):
        raise HTTPException(status_code=401, detail="no")

    monkeypatch.setattr(
        oidc,
        "authorize_action_key_or_multiscout_oidc",
        lambda authorization: "GITHUB_ACTIONS_OIDC",
    )
    dependency = oidc.scout_route_auth_dependency(Depends(reject))
    dependency.dependency("Bearer github-token")
