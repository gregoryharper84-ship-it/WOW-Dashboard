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
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)
    monkeypatch.setenv("WOW_SCOUT_SOURCE_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("WOW_SCOUT_SOURCE_RETRY_BASE_SECONDS", "0")
    monkeypatch.setattr(oidc, "mint_github_actions_oidc", lambda: "test-oidc-token")

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


def test_provider_failures_do_not_terminate_remaining_sports(monkeypatch):
    monkeypatch.setattr(scout, "TERMINAL_SOURCE_HTTP_STATUSES", {401, 403, 429})
    oidc.configure_source_failure_scope()
    assert scout.TERMINAL_SOURCE_HTTP_STATUSES == set()


def test_exhausted_provider_429_is_typed_blocker_but_scan_continues(monkeypatch):
    """A provider-local 429 must not truncate independent later sports."""
    monkeypatch.setattr(scout, "TERMINAL_SOURCE_HTTP_STATUSES", {401, 403, 429})
    oidc.configure_source_failure_scope()

    sports = [
        {"key": "baseball_mlb", "title": "MLB", "active": True},
        {"key": "icehockey_nhl", "title": "NHL", "active": True},
    ]
    seen = []

    def fake_proxy(path, params=None):
        seen.append(path)
        if path == "/odds-api/v4/sports":
            return scout.FetchResult(True, sports, 200)
        if path == "/odds-api/v4/sports/baseball_mlb/events":
            return scout.FetchResult(True, [{
                "id": "mlb-1",
                "commence_time": "2026-09-15T22:40:00Z",
                "home_team": "Tampa Bay Rays",
                "away_team": "Athletics",
            }], 200)
        if path == "/odds-api/v4/sports/baseball_mlb/events/mlb-1/markets":
            return scout.FetchResult(False, status=429, code="ODDS_PROVIDER_RATE_LIMITED")
        if path == "/odds-api/v4/sports/icehockey_nhl/events":
            return scout.FetchResult(True, [], 200)
        raise AssertionError(path)

    monkeypatch.setattr(scout, "proxy_get", fake_proxy)
    payload = scout.run()

    assert payload["status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
    assert any(
        row.get("sport") == "baseball_mlb" and row.get("http_status") == 429
        for row in payload["source_blockers"]
    )
    assert "/odds-api/v4/sports/icehockey_nhl/events" in seen
    scanned_nhl = [
        row for row in payload["coverage"]
        if row.get("scope") == "sport" and row.get("sport") == "icehockey_nhl"
    ]
    assert scanned_nhl and scanned_nhl[0]["status"] == "SCANNED"
    assert payload["governance"]["can_execute"] is False
