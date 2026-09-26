from types import SimpleNamespace

import pytest

import ncaaf_cfbd_client as cfbd


def test_api_key_required():
    with pytest.raises(cfbd.CFBDUnavailable) as exc:
        cfbd.CFBDClient(api_key="")
    assert exc.value.code == "CFBD_API_KEY_MISSING"


def test_base_url_is_pinned():
    with pytest.raises(cfbd.CFBDUnavailable) as exc:
        cfbd.CFBDClient(api_key="x", base_url="https://example.com")
    assert exc.value.code == "CFBD_BASE_URL_NOT_APPROVED"


def test_unknown_endpoint_is_rejected():
    client = cfbd.CFBDClient(api_key="x")
    with pytest.raises(cfbd.CFBDUnavailable) as exc:
        client.get("/admin")
    assert exc.value.code == "CFBD_ENDPOINT_NOT_ALLOWLISTED"


def test_http_failure_preserves_status_without_response_body(monkeypatch):
    def fake_get(url, params, headers, timeout):
        return SimpleNamespace(status_code=429)

    monkeypatch.setattr(cfbd.httpx, "get", fake_get)
    client = cfbd.CFBDClient(api_key="secret")
    with pytest.raises(cfbd.CFBDUnavailable) as exc:
        client.player_game_stats(year=2023, week=1)

    assert exc.value.code == "CFBD_HTTP_429"
    assert str(exc.value) == "CFBD returned HTTP 429 for /games/players."
    assert "secret" not in str(exc.value)


def test_games_request_is_read_only_and_bearer_authenticated(monkeypatch):
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return SimpleNamespace(status_code=200, json=lambda: [{"id": 1, "homeTeam": "A", "awayTeam": "B"}])

    monkeypatch.setattr(cfbd.httpx, "get", fake_get)
    client = cfbd.CFBDClient(api_key="secret")
    response = client.games(year=2025, week=1, classification="fbs")

    assert captured["url"] == "https://api.collegefootballdata.com/games"
    assert captured["headers"] == {"Authorization": "Bearer secret"}
    assert captured["params"] == {"year": 2025, "week": 1, "classification": "fbs"}
    assert response.rows[0]["id"] == 1
    assert cfbd.CAN_EXECUTE is False


def test_player_game_stats_uses_documented_read_only_route(monkeypatch):
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return SimpleNamespace(status_code=200, json=lambda: [{"id": 401, "teams": []}])

    monkeypatch.setattr(cfbd.httpx, "get", fake_get)
    client = cfbd.CFBDClient(api_key="secret")
    response = client.player_game_stats(year=2025, week=3)

    assert captured["url"] == "https://api.collegefootballdata.com/games/players"
    assert captured["headers"] == {"Authorization": "Bearer secret"}
    assert captured["params"] == {
        "year": 2025,
        "week": 3,
        "classification": "fbs",
        "seasonType": "both",
    }
    assert response.endpoint == "/games/players"
    assert response.rows[0]["id"] == 401
    assert cfbd.CAN_EXECUTE is False


def test_player_game_stats_requires_bounded_year_scope():
    client = cfbd.CFBDClient(api_key="secret")
    with pytest.raises(ValueError):
        client.player_game_stats(year=2025)


def test_elo_week_is_supported_but_other_ratings_do_not_invent_week(monkeypatch):
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append((url, params))
        return SimpleNamespace(status_code=200, json=lambda: [])

    monkeypatch.setattr(cfbd.httpx, "get", fake_get)
    client = cfbd.CFBDClient(api_key="secret")
    client.ratings("elo", year=2025, week=4)
    client.ratings("sp", year=2025, week=4)

    assert calls[0][1] == {"year": 2025, "week": 4}
    assert calls[1][1] == {"year": 2025}
