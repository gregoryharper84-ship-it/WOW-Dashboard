import pytest

from github_actions_oidc import GitHubOIDCValidationError, verify_github_actions_oidc


def test_internal_service_key_is_accepted_without_oidc(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_PROXY_INTERNAL_KEY", "internal-test-secret")
    claims = verify_github_actions_oidc("internal-test-secret")
    assert claims["auth_mode"] == "RENDER_INTERNAL_SERVICE_BEARER"
    assert claims["can_execute"] is False


def test_wrong_internal_key_does_not_bypass_oidc_validation(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_PROXY_INTERNAL_KEY", "internal-test-secret")

    class RejectingClient:
        def get_signing_key_from_jwt(self, _token):
            raise ValueError("not jwt")

    with pytest.raises(GitHubOIDCValidationError):
        verify_github_actions_oidc("wrong-secret", jwk_client=RejectingClient())
