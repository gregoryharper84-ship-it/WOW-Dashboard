from v17 import nightly_multiscout as scout
from v17 import nightly_multiscout_oidc as oidc


def test_transient_proxy_failure_retries_and_recovers(monkeypatch):
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)
    monkeypatch.setenv("WOW_SCOUT_SOURCE_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("WOW_SCOUT_SOURCE_RETRY_BASE_SECONDS", "0")
    monkeypatch.setattr(oidc, "mint_github_actions_oidc", lambda: "test-oidc-token")

    calls = []
    responses = iter([
        scout.FetchResult(False, status=503, code="HTTP_503"),
        scout.FetchResult(True, data={"ok": True}, status=200),
    ])

    def fake_proxy_get(path, params=None):
        calls.append((path, params))
        return next(responses)

    monkeypatch.setattr(scout, "proxy_get", fake_proxy_get)
    oidc.install_refreshable_oidc_proxy_auth()

    result = scout.proxy_get("/odds-api/v4/sports", {"all": "true"})

    assert result.ok is True
    assert result.status == 200
    assert len(calls) == 2


def test_auth_or_governance_http_failure_is_not_retried(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_PROXY_ACTION_KEY", "legacy-test-key")
    monkeypatch.setenv("WOW_SCOUT_SOURCE_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("WOW_SCOUT_SOURCE_RETRY_BASE_SECONDS", "0")

    calls = []

    def fake_proxy_get(path, params=None):
        calls.append((path, params))
        return scout.FetchResult(False, status=403, code="HTTP_403")

    monkeypatch.setattr(scout, "proxy_get", fake_proxy_get)
    oidc.install_refreshable_oidc_proxy_auth()

    result = scout.proxy_get("/odds-api/v4/sports")

    assert result.ok is False
    assert result.status == 403
    assert len(calls) == 1


def test_transient_classifier_is_narrow():
    assert oidc._is_transient(scout.FetchResult(False, status=502, code="HTTP_502")) is True
    assert oidc._is_transient(scout.FetchResult(False, code="TimeoutError")) is True
    assert oidc._is_transient(scout.FetchResult(False, status=429, code="HTTP_429")) is False
    assert oidc._is_transient(scout.FetchResult(False, status=401, code="HTTP_401")) is False
