"""The proxy degrades to core markets when the vendor rejects event endpoints.

That degradation has to stay visible. Acquisition reads response bodies, so a
header-only signal is discarded and a core-markets-only answer looks exactly
like a slate with no player props.
"""
import pytest
from fastapi.testclient import TestClient

import api


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = [] if payload is None else payload
        self.headers = headers or {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def reset_endpoint_state(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)
    api._market_inventory_unavailable_at = None
    api._event_odds_unavailable_for_core_at = None
    yield
    api._market_inventory_unavailable_at = None
    api._event_odds_unavailable_for_core_at = None


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "vendor-secret-test")
    monkeypatch.setenv("WOW_ODDS_PROXY_ACTION_KEY", "caller-secret-test")
    return {"Authorization": "Bearer caller-secret-test"}


def featured_payload(event_id="abc123"):
    return [{
        "id": event_id,
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": [{"key": "book", "title": "Book", "markets": [
            {"key": "h2h", "outcomes": [{"name": "Home", "price": -120}]},
        ]}],
    }]


def test_market_inventory_rejection_reports_degradation_in_the_body(configured, monkeypatch):
    def fake_get(url, params):
        if url.endswith("/markets"):
            return FakeResponse(403, {"message": "not in plan"})
        return FakeResponse(200, featured_payload())

    monkeypatch.setattr(api, "_http_get", fake_get)
    client = TestClient(api.app)

    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events/abc123/markets",
        params={"regions": "us"},
        headers=configured,
    )

    assert response.status_code == 200
    body = response.json()
    assert body[api.SOURCE_DEGRADATION_FIELD] == "MARKET_INVENTORY_CORE_MARKETS_ONLY_FALLBACK"
    assert response.headers[api.MARKET_INVENTORY_FALLBACK_HEADER] == "sport-featured-odds"
    # The degraded body carries core markets only, which is exactly why zero
    # prop candidates downstream must not be read as an empty slate.
    keys = {m["key"] for b in body["bookmakers"] for m in b["markets"]}
    assert keys <= api.CORE_EVENT_MARKETS


def test_successful_inventory_carries_no_degradation_marker(configured, monkeypatch):
    monkeypatch.setattr(api, "_http_get", lambda url, params: FakeResponse(
        200, [{"key": "batter_hits"}, {"key": "h2h"}]))
    client = TestClient(api.app)

    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events/abc123/markets",
        params={"regions": "us"},
        headers=configured,
    )

    assert response.status_code == 200
    assert api.MARKET_INVENTORY_FALLBACK_HEADER not in response.headers
    assert api.SOURCE_DEGRADATION_FIELD not in response.text


def test_endpoint_rejection_is_re_probed_after_the_retry_window(configured, monkeypatch):
    attempts = {"markets": 0}

    def fake_get(url, params):
        if url.endswith("/markets"):
            attempts["markets"] += 1
            return FakeResponse(403, {"message": "not in plan"})
        return FakeResponse(200, featured_payload())

    monkeypatch.setattr(api, "_http_get", fake_get)
    client = TestClient(api.app)
    url = "/odds-api/v4/sports/baseball_mlb/events/abc123/markets"

    client.get(url, params={"regions": "us"}, headers=configured)
    assert attempts["markets"] == 1

    # Inside the window the rejection is cached, so the vendor is not hammered.
    client.get(url, params={"regions": "us"}, headers=configured)
    assert attempts["markets"] == 1

    # Past the window the entitlement is re-probed rather than assumed dead
    # for the lifetime of the process.
    monkeypatch.setattr(api, "_is_unavailable", lambda marked_at: False)
    client.get(url, params={"regions": "us"}, headers=configured)
    assert attempts["markets"] == 2


def test_prop_market_request_is_never_served_from_the_core_fallback(configured, monkeypatch):
    monkeypatch.setattr(api, "_http_get", lambda url, params: FakeResponse(403, {"message": "no"}))
    client = TestClient(api.app)

    response = client.get(
        "/odds-api/v4/sports/baseball_mlb/events/abc123/odds",
        params={"markets": "batter_hits", "regions": "us"},
        headers=configured,
    )

    # A prop request must surface the rejection, never a core-markets substitute.
    assert response.status_code in {401, 403}
    assert api.SOURCE_DEGRADATION_FIELD not in response.text
