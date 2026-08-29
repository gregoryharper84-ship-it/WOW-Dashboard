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
