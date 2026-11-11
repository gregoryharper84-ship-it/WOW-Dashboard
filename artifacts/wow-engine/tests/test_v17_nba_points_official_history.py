"""NBA public-history adapter: bounded reads, never a governed prediction."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v17.nba_points_official_history import (
    acquire_nba_points_l10, NBAHistoryError, STATS_URL,
)

NOW = datetime(2026, 10, 22, 20, tzinfo=timezone.utc)
KICKOFF = "2026-10-23T02:00:00Z"


def fake_client(override=None):
    seen = []

    def get(url, params=None, headers=None, timeout=None):
        seen.append((url, params))
        if url == STATS_URL:
            rows = [
                ["002260%04d" % i, "Oct %02d, 2026" % (11 + i), 18 + i, "678"]
                for i in range(10)
            ]
            result = {"resultSets": [{
                "name": "PlayerGameLog",
                "headers": ["Game_ID", "GAME_DATE", "PTS", "Player_ID"],
                "rowSet": rows,
            }]}
        else:
            game_id = url.rsplit("_", 1)[-1].split(".")[0]
            i = int(game_id[-4:])
            result = {"game": {
                "gameId": game_id, "gameStatus": 3,
                "gameTimeUTC": "2026-10-%02dT23:00:00Z" % (11 + i),
                "homeTeam": {"players": [{
                    "personId": "678", "statistics": {"points": 18 + i},
                }]},
                "awayTeam": {"players": []},
            }}
        if override:
            result = override(url, result)
        return SimpleNamespace(status_code=200, json=lambda: result)

    return get, seen


def run(**kwargs):
    http_get, seen = fake_client(kwargs.pop("override", None))
    result = acquire_nba_points_l10(
        player_id=kwargs.pop("player_id", "678"),
        event_start_time=kwargs.pop("event_start_time", KICKOFF),
        seasons=kwargs.pop("seasons", ("2026-27",)),
        now=NOW, http_get=http_get,
    )
    return result, seen


def fails(code, **kwargs):
    with pytest.raises(NBAHistoryError) as exc:
        run(**kwargs)
    assert exc.value.code == code


def test_l10_official_records_are_bounded_and_not_published():
    result, requests = run()
    assert len(requests) == 11
    assert len(result["game_log"]) == len(result["box_score_log"]) == 10
    assert result["status"] == "NBA_HISTORY_L10_VERIFIED_NO_CURRENT_ROLE_OR_LINE"
    assert all(row["player_id"] == "678" for row in result["box_score_log"])
    assert all(row["event_start_time"] < KICKOFF for row in result["box_score_log"])
    assert not result["can_execute"] and not result["probability_publishable"]
    assert "calibrated_probability" not in result


def test_boxscore_game_id_is_exact():
    def wrong(url, result):
        if url != STATS_URL:
            result["game"]["gameId"] = "unrelated"
        return result
    fails("NBA_HISTORY_BOX_GAME_ID_MISMATCH", override=wrong)


def test_boxscore_player_id_is_exact():
    def wrong(url, result):
        if url != STATS_URL:
            result["game"]["homeTeam"]["players"][0]["personId"] = "999"
        return result
    fails("NBA_HISTORY_BOX_PLAYER_ID_MISSING_OR_DUPLICATE", override=wrong)


def test_boxscore_points_agree_with_logged_points():
    def wrong(url, result):
        if url != STATS_URL:
            result["game"]["homeTeam"]["players"][0]["statistics"]["points"] = 99
        return result
    fails("NBA_HISTORY_POINTS_DISAGREE_WITH_BOX", override=wrong)


def test_game_must_be_officially_final():
    def wrong(url, result):
        if url != STATS_URL:
            result["game"]["gameStatus"] = 2
        return result
    fails("NBA_HISTORY_GAME_NOT_FINAL", override=wrong)


def test_future_box_time_does_not_become_prior_history():
    def wrong(url, result):
        if url != STATS_URL:
            result["game"]["gameTimeUTC"] = "2026-10-24T01:00:00Z"
        return result
    fails("NBA_HISTORY_BOX_TIME_CONTRADICTION", override=wrong)


def test_unavailable_l10_is_an_explicit_hold():
    def wrong(url, result):
        if url == STATS_URL:
            result["resultSets"][0]["rowSet"] = result["resultSets"][0]["rowSet"][:5]
        return result
    fails("NBA_HISTORY_L10_INSUFFICIENT", override=wrong)


def test_unknown_gamelog_schema_is_rejected():
    def wrong(url, result):
        if url == STATS_URL:
            result["resultSets"][0]["headers"].remove("Player_ID")
        return result
    fails("NBA_HISTORY_GAMELOG_SCHEMA_INVALID", override=wrong)


def test_started_game_cannot_be_backfilled():
    fails("NBA_HISTORY_EVENT_NOT_PREGAME",
          event_start_time="2026-10-22T18:00:00Z")


def test_player_ids_cannot_inject_url_paths():
    fails("NBA_HISTORY_PLAYER_ID_INVALID", player_id="x/../../")


def test_season_fanout_is_bounded():
    fails("NBA_HISTORY_SEASON_SCOPE_INVALID",
          seasons=("2026-27", "2025-26", "2024-25"))


def test_row_is_still_research_only_without_roster_and_exact_line():
    result, _ = run()
    assert "role_status" not in result
    assert "line" not in result
    assert "source_snapshot_id" not in result
