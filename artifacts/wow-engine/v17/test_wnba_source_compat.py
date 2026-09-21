from __future__ import annotations

import wnba_prop_auto_hydration as wnba
import v17.wnba_source_compat as subject


def test_league_game_log_query_order_and_browser_cache_headers_are_locked(monkeypatch):
    captured = {}

    def original(url, *, http_get, params=None, headers=None, expect_json=True):
        captured["url"] = url
        captured["params"] = dict(params or {})
        captured["headers"] = dict(headers or {})
        return {"ok": True}

    monkeypatch.setattr(wnba, "_request", original)
    assert subject.install_wnba_current_source_compat() is True
    response = wnba._request(
        f"{wnba.WNBA_STATS_BASE}/leaguegamelog",
        http_get=lambda *args, **kwargs: None,
        params={
            "LeagueID": "10",
            "PlayerOrTeam": "P",
            "Season": "2026",
            "SeasonType": "Regular Season",
            "Counter": "0",
            "DateFrom": "",
            "DateTo": "",
            "Direction": "ASC",
            "Sorter": "DATE",
        },
        headers={"Accept": "application/json"},
    )
    assert response == {"ok": True}
    assert list(captured["params"]) == [
        "LeagueID",
        "Season",
        "SeasonType",
        "PlayerOrTeam",
        "Counter",
        "Direction",
        "Sorter",
        "DateFrom",
        "DateTo",
    ]
    assert captured["headers"]["Cache-Control"] == "no-cache"
    assert captured["headers"]["Pragma"] == "no-cache"
    assert captured["headers"]["Connection"] == "keep-alive"


def test_non_stats_request_keeps_existing_headers_and_parameter_order(monkeypatch):
    captured = {}

    def original(url, *, http_get, params=None, headers=None, expect_json=True):
        captured["params"] = dict(params or {})
        captured["headers"] = dict(headers or {})
        return {"ok": True}

    monkeypatch.setattr(wnba, "_request", original)
    assert subject.install_wnba_current_source_compat() is True
    wnba._request(
        wnba.WNBA_SCHEDULE_URL,
        http_get=lambda *args, **kwargs: None,
        params={"b": 2, "a": 1},
        headers={"Accept": "application/json"},
    )
    assert list(captured["params"]) == ["b", "a"]
    assert captured["headers"] == {"Accept": "application/json"}
