import github_actions_oidc as oidc


def test_router_accepts_same_internal_read_only_bearer(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_PROXY_INTERNAL_KEY", "internal-router-test")

    claims = oidc.verify_github_actions_oidc("internal-router-test")

    assert claims["auth_mode"] == "RENDER_INTERNAL_SERVICE_BEARER"
    assert claims["audience"] == "wow-odds-router"
    assert claims["can_execute"] is False
