from __future__ import annotations

import pytest
from fastapi import Depends, HTTPException

import github_actions_oidc as oidc


def _claims():
    return {
        "repository": oidc.REPOSITORY,
        "repository_id": oidc.REPOSITORY_ID,
        "repository_owner_id": oidc.REPOSITORY_OWNER_ID,
        "ref": oidc.REF,
        "workflow_ref": oidc.WORKFLOW_REF,
        "runner_environment": "github-hosted",
        "event_name": "push",
    }


def test_exact_protected_main_multiscout_claims_are_accepted():
    assert oidc.validate_github_actions_claims(_claims())["repository_id"] == oidc.REPOSITORY_ID


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("repository", "someone/else"),
        ("repository_id", "1"),
        ("repository_owner_id", "2"),
        ("ref", "refs/heads/feature"),
        ("workflow_ref", "gregoryharper84-ship-it/WOW-Dashboard/.github/workflows/other.yml@refs/heads/main"),
        ("runner_environment", "self-hosted"),
    ],
)
def test_oidc_claim_identity_mismatch_fails_closed(field, bad):
    claims = _claims()
    claims[field] = bad
    with pytest.raises(oidc.GitHubOIDCValidationError):
        oidc.validate_github_actions_claims(claims)


def test_pull_request_oidc_is_never_authorized_for_live_scout():
    claims = _claims()
    claims["event_name"] = "pull_request"
    with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(claims)


def test_existing_action_key_remains_first_class_auth(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "action-secret")
    assert oidc.authorize_action_key_or_multiscout_oidc("Bearer action-secret") == "WOW_ACTION_API_KEY"


def test_oidc_is_only_fallback_after_action_key_miss(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "action-secret")
    seen = []
    monkeypatch.setattr(oidc, "verify_github_actions_oidc", lambda token: seen.append(token) or _claims())
    assert oidc.authorize_action_key_or_multiscout_oidc("Bearer github-token") == "GITHUB_ACTIONS_OIDC"
    assert seen == ["github-token"]


def test_route_dependency_preserves_permissive_test_injection():
    dep = oidc.scout_route_auth_dependency(Depends(lambda: None))
    dep.dependency(None)


def test_route_dependency_falls_back_to_oidc_after_existing_http_reject(monkeypatch):
    def reject(authorization=None):
        raise HTTPException(status_code=401, detail="no")

    monkeypatch.setattr(oidc, "authorize_action_key_or_multiscout_oidc", lambda authorization: "GITHUB_ACTIONS_OIDC")
    dep = oidc.scout_route_auth_dependency(Depends(reject))
    dep.dependency("Bearer github-token")


def test_route_dependency_preserves_original_auth_failure_when_oidc_invalid(monkeypatch):
    def reject(authorization=None):
        raise HTTPException(status_code=401, detail="original-action-auth")

    def fail(_authorization):
        raise oidc.GitHubOIDCValidationError("bad")

    monkeypatch.setattr(oidc, "authorize_action_key_or_multiscout_oidc", fail)
    dep = oidc.scout_route_auth_dependency(Depends(reject))
    with pytest.raises(HTTPException) as caught:
        dep.dependency("Bearer invalid")
    assert caught.value.detail == "original-action-auth"
