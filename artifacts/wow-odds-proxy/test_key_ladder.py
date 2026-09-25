import json

import httpx

import api


def _response(status: int, payload):
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", "https://api.the-odds-api.com/v4/sports"),
    )


def test_vendor_keys_follow_governed_order_and_dedupe(monkeypatch):
    for name in api.ODDS_API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ODDS_API_KEY", "legacy")
    monkeypatch.setenv("ODDS_API_FREE_KEY", "shared")
    monkeypatch.setenv("ODDS_API_KEY_100K", "shared")
    monkeypatch.setenv("ODDS_API_PAID_KEY", "paid")

    assert api._vendor_keys() == (
        ("ODDS_API_PAID_KEY", "paid"),
        ("ODDS_API_KEY_100K", "shared"),
        ("ODDS_API_KEY", "legacy"),
    )


def test_proxy_get_fails_over_from_exhausted_key_to_next_alias(monkeypatch):
    for name in api.ODDS_API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ODDS_API_PAID_KEY", "exhausted")
    monkeypatch.setenv("ODDS_API_FREE_KEY", "healthy")

    seen = []

    def fake_get(url, params):
        seen.append((url, dict(params)))
        if params["apiKey"] == "exhausted":
            return _response(429, {"message": "quota exhausted"})
        return _response(200, [{"key": "baseball_mlb", "active": True}])

    monkeypatch.setattr(api, "_http_get", fake_get)
    result = api._proxy_get("/sports", {"all": "true"})

    assert result.status_code == 200
    assert len(seen) == 2
    assert seen[0][1]["apiKey"] == "exhausted"
    assert seen[1][1]["apiKey"] == "healthy"
    assert json.loads(result.body) == [{"key": "baseball_mlb", "active": True}]


def test_proxy_get_does_not_fail_over_on_non_auth_non_quota_failure(monkeypatch):
    for name in api.ODDS_API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ODDS_API_PAID_KEY", "first")
    monkeypatch.setenv("ODDS_API_FREE_KEY", "second")

    calls = []

    def fake_get(url, params):
        calls.append(dict(params))
        return _response(500, {"message": "upstream failure"})

    monkeypatch.setattr(api, "_http_get", fake_get)
    result = api._proxy_get("/sports", {})

    assert result.status_code == 500
    assert len(calls) == 1


def test_safe_upstream_message_redacts_every_configured_alias(monkeypatch):
    for name in api.ODDS_API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ODDS_API_PAID_KEY", "paid-secret")
    monkeypatch.setenv("ODDS_API_FREE_KEY", "free-secret")

    response = _response(401, {"message": "paid-secret free-secret rejected"})
    message = api._safe_upstream_message(response)

    assert message == "[REDACTED] [REDACTED] rejected"
    assert "paid-secret" not in message
    assert "free-secret" not in message
