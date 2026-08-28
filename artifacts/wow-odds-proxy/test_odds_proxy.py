import os

import httpx
import pytest
from fastapi.testclient import TestClient

import api


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "vendor-secret-test")
    monkeypatch.setenv("WOW_ODDS_PROXY_ACTION_KEY", "caller-secret-test")
    return {"Authorization": "Bearer caller-secret-test"}


def test_health_never_returns_secret(configured):
    client = TestClient(api.app)
    response = client.get("/odds-api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["vendor_key_configured"] is True
    assert body["caller_auth_configured"] is True
    assert body["can_execute"] is False
    assert "vendor-secret-test" not in response.text
    assert "caller-secret-test" not in response.text


def test_protected_route_requires_caller_bearer(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "vendor-secret-test")
    monkeypatch.setenv("WOW_ODDS_PROXY_ACTION_KEY", "caller-secret-test")
    client = TestClient(api.app)

    missing = client.get("/odds-api/v4/sports/baseball_mlb/events")
    wrong = client.get(
        "/odds-api/v4/sports/baseball_mlb/events",
        headers={"Authorization": "Bearer wrong"},
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_events_appends_vendor_key_server_side_and_discards_unknown_query(monkeypatch, configured):
    captured = {}

    def fake_get(url, params):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(200, [{"id": "evt1"}], {"x-requests-remaining": "99"})

    monkeypatch.setattr(api, "_http_get", fake_get)
    client = TestClient(api.app)
    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events",
        params={
            "dateFormat": "iso",
            "includeRotationNumbers": "true",
            "apiKey": "caller-should-not-control-this",
            "url": "https://evil.example",
        },
        headers=configured,
    )

    assert response.status_code == 200
    assert captured["url"] == "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
    assert captured["params"]["apiKey"] == "vendor-secret-test"
    assert "url" not in captured["params"]
    assert captured["params"]["includeRotationNumbers"] == "true"
    assert response.headers["x-requests-remaining"] == "99"


def test_event_markets_requires_regions_or_bookmakers(configured):
    client = TestClient(api.app)
    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events/abc123/markets",
        headers=configured,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "REGIONS_OR_BOOKMAKERS_REQUIRED"


def test_event_markets_forwards_only_allowlisted_params(monkeypatch, configured):
    captured = {}

    def fake_get(url, params):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(200, {"id": "abc123", "bookmakers": []})

    monkeypatch.setattr(api, "_http_get", fake_get)
    client = TestClient(api.app)
    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events/abc123/markets",
        params={"regions": "us", "dateFormat": "iso", "unexpected": "drop-me"},
        headers=configured,
    )
    assert response.status_code == 200
    assert captured["url"].endswith("/sports/baseball_mlb/events/abc123/markets")
    assert captured["params"] == {
        "regions": "us",
        "dateFormat": "iso",
        "apiKey": "vendor-secret-test",
    }


def test_event_odds_forwards_market_options(monkeypatch, configured):
    captured = {}

    def fake_get(url, params):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(
            200,
            {"id": "abc123", "bookmakers": []},
            {
                "x-requests-remaining": "80",
                "x-requests-used": "20",
                "x-requests-last": "1",
            },
        )

    monkeypatch.setattr(api, "_http_get", fake_get)
    client = TestClient(api.app)
    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events/abc123/odds",
        params={
            "markets": "h2h,pitcher_strikeouts",
            "regions": "us",
            "oddsFormat": "american",
            "includeLinks": "false",
        },
        headers=configured,
    )
    assert response.status_code == 200
    assert captured["params"]["markets"] == "h2h,pitcher_strikeouts"
    assert captured["params"]["regions"] == "us"
    assert captured["params"]["oddsFormat"] == "american"
    assert captured["params"]["includeLinks"] == "false"
    assert captured["params"]["apiKey"] == "vendor-secret-test"
    assert response.headers["x-requests-last"] == "1"


def test_upstream_error_is_sanitized_and_vendor_key_redacted(monkeypatch, configured):
    def fake_get(url, params):
        return FakeResponse(
            401,
            {"message": "bad key vendor-secret-test for request https://example.test?apiKey=vendor-secret-test"},
        )

    monkeypatch.setattr(api, "_http_get", fake_get)
    client = TestClient(api.app)
    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events",
        headers=configured,
    )
    assert response.status_code == 401
    text = response.text
    assert "vendor-secret-test" not in text
    assert "[REDACTED]" in text
    assert response.json()["code"] == "ODDS_API_UPSTREAM_ERROR"
    assert response.json()["can_execute"] is False


def test_missing_vendor_key_fails_closed(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_PROXY_ACTION_KEY", "caller-secret-test")
    client = TestClient(api.app)
    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events",
        headers={"Authorization": "Bearer caller-secret-test"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ODDS_API_KEY_UNCONFIGURED"


def test_no_write_method_exists(configured):
    client = TestClient(api.app)
    response = client.post(
        "/odds-api/v4/sports/baseball_mlb/events",
        headers=configured,
    )
    assert response.status_code == 405


def test_openapi_never_exposes_vendor_api_key():
    schema = api.app.openapi()
    serialized = str(schema)
    assert "ODDS_API_KEY" not in serialized
    assert "apiKey" not in serialized
    assert "WOW_ODDS_PROXY_ACTION_KEY" not in serialized
    assert set(schema["paths"].keys()) == {
        "/odds-api/health",
        "/odds-api/v4/sports/{sport}/events",
        "/odds-api/v4/sports/{sport}/events/{event_id}/markets",
        "/odds-api/v4/sports/{sport}/events/{event_id}/odds",
    }
