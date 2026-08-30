from types import SimpleNamespace

import pytest

import wnba_stats_client as mod
from wnba_stats_client import WNBAStatsClient, WNBAStatsUnavailable


def _response(status=200, payload=None):
    return SimpleNamespace(status_code=status, json=lambda: payload)


def test_player_game_logs_uses_official_allowlisted_contract(monkeypatch):
    captured = {}

    def fake_get(url, *, params, headers, timeout, follow_redirects):
        captured.update(url=url, params=params, headers=headers, timeout=timeout, follow_redirects=follow_redirects)
        return _response(payload={
            "resultSets": [{
                "name": "LeagueGameLog",
                "headers": ["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "GAME_ID", "GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "AST", "FG3M"],
                "rowSet": [[1, "Player A", "DAL", "100", "2026-05-01", "DAL vs. NYL", 31, 18, 7, 5, 2]],
            }]
        })

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    result = WNBAStatsClient().player_game_logs(season=2026)
    assert captured["url"] == "https://stats.wnba.com/stats/leaguegamelog"
    assert list(captured["params"])[0] == "LeagueID"
    assert captured["params"]["LeagueID"] == "10"
    assert captured["params"]["PlayerOrTeam"] == "P"
    assert captured["headers"]["Referer"] == "https://stats.wnba.com/"
    assert result.rows[0]["PLAYER_NAME"] == "Player A"
    assert result.source_identity == "WNBA_STATS_LEAGUE_GAME_LOG"
    assert result.can_execute is False


def test_client_rejects_non_official_base_url():
    with pytest.raises(WNBAStatsUnavailable) as exc:
        WNBAStatsClient(base_url="https://example.com")
    assert exc.value.code == "WNBA_STATS_BASE_URL_NOT_APPROVED"


def test_future_season_and_unallowlisted_season_type_fail_closed():
    client = WNBAStatsClient()
    with pytest.raises(WNBAStatsUnavailable) as exc:
        client.player_game_logs(season=2100)
    assert exc.value.code == "WNBA_STATS_SEASON_OUT_OF_RANGE"
    with pytest.raises(WNBAStatsUnavailable) as exc:
        client.player_game_logs(season=2026, season_type="Preseason")
    assert exc.value.code == "WNBA_STATS_SEASON_TYPE_UNSUPPORTED"


def test_malformed_result_set_fails_closed(monkeypatch):
    monkeypatch.setattr(mod.httpx, "get", lambda *args, **kwargs: _response(payload={"resultSets": []}))
    with pytest.raises(WNBAStatsUnavailable) as exc:
        WNBAStatsClient().player_game_logs(season=2026)
    assert exc.value.code == "WNBA_STATS_RESULT_SET_MISSING"
