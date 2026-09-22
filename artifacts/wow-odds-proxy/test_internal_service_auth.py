import github_actions_oidc as oidc


def test_internal_service_bearer_is_separate_read_only_auth(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_PROXY_INTERNAL_KEY", "internal-test-secret")

    claims = oidc.verify_github_actions_oidc("internal-test-secret")

    assert claims["auth_mode"] == "RENDER_INTERNAL_SERVICE_BEARER"
    assert claims["audience"] == "wow-odds-proxy"
    assert claims["can_execute"] is False
