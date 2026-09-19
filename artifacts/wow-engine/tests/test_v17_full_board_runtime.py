from __future__ import annotations

import json
from urllib.error import HTTPError

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17 import market_evidence_sources as sources
from v17 import scout_secondary_source as secondary
from v17.full_board_runtime import (
    compact_espn_scoreboard,
    install_full_board_runtime_routes,
    run_rundown_live_health,
)


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.code = status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_rundown_health_requires_catalog_and_event_access(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("THERUNDOWN_API_KEY", "not-a-real-key")
    monkeypatch.setenv("WOW_RUNDOWN_SPORT_ID_BASEBALL_MLB", "3")
    calls = []

    def opener(request, timeout=None):
        calls.append(request)
        if request.full_url.endswith("/api/v2/sports"):
            return _Response({"sports": [{"sport_id": 3, "sport_name": "MLB"}]})
        if "/api/v2/sports/3/events/2026-09-19" in request.full_url:
            return _Response({"events": []})
        raise AssertionError(request.full_url)

    result = run_rundown_live_health(
        sport_key="baseball_mlb", date="2026-09-19", opener=opener
    )
    assert result["status"] == "PASS"
    assert result["catalog_access"]["market_acquisition_status"] == "PASS"
    assert result["event_access"]["market_acquisition_status"] == "PASS"
    assert result["event_access"]["market_snapshot_present"] is False
    assert result["auth_contract"] == "X-TheRundown-Key"
    assert len(calls) == 2
    for request in calls:
        assert request.headers.get("X-therundown-key") == "not-a-real-key"
        assert "?key=" not in request.full_url


def test_rundown_health_fails_when_catalog_works_but_events_are_unauthorized(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("THERUNDOWN_API_KEY", "not-a-real-key")
    monkeypatch.setenv("WOW_RUNDOWN_SPORT_ID_BASEBALL_MLB", "3")

    def opener(request, timeout=None):
        if request.full_url.endswith("/api/v2/sports"):
            return _Response({"sports": [{"sport_id": 3, "sport_name": "MLB"}]})
        if "/api/v2/sports/3/events/2026-09-19" in request.full_url:
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
        raise AssertionError(request.full_url)

    result = run_rundown_live_health(
        sport_key="baseball_mlb", date="2026-09-19", opener=opener
    )
    assert result["status"] == "BLOCKED"
    assert result["catalog_access"]["market_acquisition_status"] == "PASS"
    assert result["event_access"]["market_acquisition_status"] == "AUTH_FAILED"
    assert result["event_access"]["market_snapshot_present"] is False
    assert result["affects_model_capability"] is False
    assert result["can_execute"] is False


def test_rundown_health_fails_fast_when_catalog_auth_fails(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("THERUNDOWN_API_KEY", "not-a-real-key")

    def opener(request, timeout=None):
        raise HTTPError(request.full_url, 403, "forbidden", {}, None)

    result = run_rundown_live_health(
        sport_key="baseball_mlb", date="2026-09-19", opener=opener
    )
    assert result["status"] == "BLOCKED"
    assert result["catalog_access"]["market_acquisition_status"] == "AUTH_FAILED"
    assert result["event_access"]["market_acquisition_status"] == "NOT_ATTEMPTED"


def test_compact_espn_scoreboard_returns_bounded_identity_rows(monkeypatch):
    raw_events = []
    for i in range(230):
        raw_events.append({
            "id": str(i),
            "date": "2026-09-19T20:00:00Z",
            "status": {"type": {"name": "STATUS_SCHEDULED"}},
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": f"Home {i}"}},
                    {"homeAway": "away", "team": {"displayName": f"Away {i}"}},
                ],
                "odds": [{"huge": "x" * 10000}],
            }],
        })

    monkeypatch.setattr(
        secondary,
        "_scoreboard",
        lambda *args, **kwargs: secondary.SecondaryResult(True, {"events": raw_events}, 200),
    )
    page = compact_espn_scoreboard(
        sport_key="baseball_mlb", date="2026-09-19", page=2, page_size=100
    )
    assert page["status"] == "PASS"
    assert page["total_events"] == 230
    assert len(page["events"]) == 100
    assert page["has_more"] is True
    assert page["events"][0]["provider_event_id"] == "100"
    assert page["events"][0]["official_event_id"] is None
    assert "competitions" not in page["events"][0]
    assert "odds" not in page["events"][0]


def test_runtime_routes_expose_capabilities_and_are_read_only(monkeypatch):
    app = FastAPI()
    assert install_full_board_runtime_routes(app) is True
    client = TestClient(app)

    response = client.get("/v17/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert body["can_execute"] is False
    assert "registered_models" in body

    paths = {route.path for route in app.routes}
    assert "/v17/market-health/rundown" in paths
    assert "/v17/discovery/espn-compact" in paths
