import pytest
from fastapi.testclient import TestClient
import app


class R:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = {} if payload is None else payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for key in ("WOW_ODDS_ROUTER_ACTION_KEY", "OPTICODDS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    app._PRIMARY_EVENT_CONTEXT.clear()
    app._PRIMARY_TO_OPTIC_EVENT.clear()


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_ROUTER_ACTION_KEY", "router-secret")
    return {"Authorization": "Bearer router-secret"}


def test_health_is_evidence_only_and_non_executing():
    client = TestClient(app.app)
    body = client.get("/odds-api/health").json()
    assert body["provider_authority"] == "EVIDENCE_ONLY"
    assert body["partial_primary_failover_enabled"] is True
    assert body["can_execute"] is False


def test_primary_success_passes_through(monkeypatch, auth):
    monkeypatch.setattr(app, "_primary_get", lambda *a, **k: R(200, [{"key": "baseball_mlb", "active": True}]))
    client = TestClient(app.app)
    response = client.get("/odds-api/v4/sports", headers=auth)
    assert response.status_code == 200
    assert response.headers["x-wow-odds-provider"] == "THE_ODDS_API"
    assert response.headers["x-wow-odds-failover"] == "false"


def test_primary_401_without_backup_fails_closed(monkeypatch, auth):
    monkeypatch.setattr(app, "_primary_get", lambda *a, **k: R(401, {"code": "PRIMARY_FAILED", "can_execute": False}))
    client = TestClient(app.app)
    response = client.get("/odds-api/v4/sports", headers=auth)
    assert response.status_code == 401
    assert response.json()["can_execute"] is False


def test_primary_401_with_backup_returns_mapped_sports(monkeypatch, auth):
    monkeypatch.setenv("OPTICODDS_API_KEY", "optic-secret")
    monkeypatch.setattr(app, "_primary_get", lambda *a, **k: R(401, {"code": "PRIMARY_FAILED"}))
    client = TestClient(app.app)
    response = client.get("/odds-api/v4/sports", headers=auth)
    assert response.status_code == 200
    assert response.headers["x-wow-odds-provider"] == "OPTICODDS"
    assert any(row["key"] == "baseball_mlb" for row in response.json())


def test_events_failover_tags_provider_identity(monkeypatch, auth):
    monkeypatch.setenv("OPTICODDS_API_KEY", "optic-secret")
    monkeypatch.setattr(app, "_primary_get", lambda *a, **k: R(503, {}))
    monkeypatch.setattr(
        app,
        "_optic_get",
        lambda *a, **k: R(200, {"data": [{"id": "FX1", "start_date": "2026-09-12T00:00:00Z", "home_team_display": "A", "away_team_display": "B"}]}),
    )
    client = TestClient(app.app)
    response = client.get("/odds-api/v4/sports/baseball_mlb/events", headers=auth)
    row = response.json()[0]
    assert row["id"] == "optic__FX1"
    assert row["source_provider"] == "OPTICODDS"
    assert row["provider_event_id"] == "FX1"


def test_primary_event_success_is_remembered_for_later_failover(monkeypatch, auth):
    primary_event = {
        "id": "P1",
        "commence_time": "2026-09-14T20:00:00Z",
        "home_team": "Texas Rangers",
        "away_team": "Houston Astros",
    }
    monkeypatch.setattr(app, "_primary_get", lambda *a, **k: R(200, [primary_event]))
    client = TestClient(app.app)
    response = client.get("/odds-api/v4/sports/baseball_mlb/events", headers=auth)
    assert response.status_code == 200
    assert app._PRIMARY_EVENT_CONTEXT[("baseball_mlb", "P1")]["home_team"] == "Texas Rangers"


def test_market_inventory_fails_over_after_primary_event_market_401(monkeypatch, auth):
    monkeypatch.setenv("OPTICODDS_API_KEY", "optic-secret")
    primary_event = {
        "id": "P1",
        "commence_time": "2026-09-14T20:00:00Z",
        "home_team": "Texas Rangers",
        "away_team": "Houston Astros",
    }

    def primary(path, *args, **kwargs):
        if path.endswith("/events"):
            return R(200, [primary_event])
        return R(401, {"code": "PRIMARY_MARKETS_NOT_ENTITLED", "can_execute": False})

    def optic(path, *args, **kwargs):
        if path == "/fixtures/active":
            return R(200, {"data": [{"id": "FX1", "home_team_display": "Texas Rangers", "away_team_display": "Houston Astros"}]})
        if path == "/markets/active":
            return R(200, {"data": ["moneyline", "player_strikeouts"]})
        raise AssertionError(path)

    monkeypatch.setattr(app, "_primary_get", primary)
    monkeypatch.setattr(app, "_optic_get", optic)
    client = TestClient(app.app)
    assert client.get("/odds-api/v4/sports/baseball_mlb/events", headers=auth).status_code == 200
    response = client.get("/odds-api/v4/sports/baseball_mlb/events/P1/markets?regions=us", headers=auth)
    assert response.status_code == 200
    assert response.headers["x-wow-odds-provider"] == "OPTICODDS"
    assert response.headers["x-wow-primary-status"] == "401"
    market_keys = {m["key"] for m in response.json()["bookmakers"][0]["markets"]}
    assert "h2h" in market_keys
    assert "player_strikeouts" in market_keys


def test_odds_fail_over_after_primary_event_odds_401(monkeypatch, auth):
    monkeypatch.setenv("OPTICODDS_API_KEY", "optic-secret")
    primary_event = {
        "id": "P1",
        "commence_time": "2026-09-14T20:00:00Z",
        "home_team": "Texas Rangers",
        "away_team": "Houston Astros",
    }

    def primary(path, *args, **kwargs):
        if path.endswith("/events"):
            return R(200, [primary_event])
        return R(401, {"code": "PRIMARY_ODDS_NOT_ENTITLED", "can_execute": False})

    def optic(path, *args, **kwargs):
        if path == "/fixtures/active":
            return R(200, {"data": [{"id": "FX1", "home_team_display": "Texas Rangers", "away_team_display": "Houston Astros"}]})
        if path == "/fixtures/odds":
            return R(200, {"data": [{
                "id": "FX1",
                "start_date": "2026-09-14T20:00:00Z",
                "home_team_display": "Texas Rangers",
                "away_team_display": "Houston Astros",
                "odds": [{
                    "id": "o1",
                    "sportsbook": "BetMGM",
                    "market": "Moneyline",
                    "market_id": "moneyline",
                    "selection": "Texas Rangers",
                    "price": -120,
                    "points": None,
                    "player_id": None,
                }],
            }]})
        raise AssertionError(path)

    monkeypatch.setattr(app, "_primary_get", primary)
    monkeypatch.setattr(app, "_optic_get", optic)
    client = TestClient(app.app)
    assert client.get("/odds-api/v4/sports/baseball_mlb/events", headers=auth).status_code == 200
    response = client.get("/odds-api/v4/sports/baseball_mlb/events/P1/odds?markets=h2h&regions=us", headers=auth)
    assert response.status_code == 200
    assert response.headers["x-wow-odds-provider"] == "OPTICODDS"
    assert response.headers["x-wow-primary-status"] == "401"
    assert response.json()["id"] == "P1"
    assert response.json()["bookmakers"][0]["markets"][0]["key"] == "h2h"
    assert "probability" not in str(response.json()).lower()


def test_optic_odds_normalize_without_probability_authority():
    payload = {"data": [{
        "id": "FX1",
        "start_date": "2026-09-12T00:00:00Z",
        "home_team_display": "A",
        "away_team_display": "B",
        "odds": [{
            "id": "o1",
            "sportsbook": "BetMGM",
            "market": "Moneyline",
            "market_id": "moneyline",
            "selection": "A",
            "price": -120,
            "points": None,
            "player_id": None,
        }],
    }]}
    out = app._normalize_optic_odds(payload, "baseball_mlb", "FX1")
    assert out["source_provider"] == "OPTICODDS"
    assert out["bookmakers"][0]["markets"][0]["key"] == "h2h"
    assert "probability" not in str(out).lower()


def test_prop_market_id_is_preserved_not_promoted():
    assert app._optic_market_to_wow("player_passing_yards", "americanfootball_nfl") == "player_passing_yards"


def test_unauthorized_router_request_is_blocked(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_ROUTER_ACTION_KEY", "router-secret")
    monkeypatch.setattr(
        app,
        "verify_github_actions_oidc",
        lambda token: (_ for _ in ()).throw(app.GitHubOIDCValidationError("bad")),
    )
    client = TestClient(app.app)
    assert client.get("/odds-api/v4/sports").status_code == 401
