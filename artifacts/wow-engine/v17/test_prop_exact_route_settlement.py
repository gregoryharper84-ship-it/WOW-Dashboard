from datetime import datetime, timedelta, timezone

from v17.cross_sport_certification_inventory import CERTIFICATION_SPORTS
from v17.prop_exact_route_settlement import (
    FANTASY_COMPONENT_SETTLEMENT_REQUIRED,
    NO_CURRENT_PROP_CATEGORY_DECLARED,
    SEPARATE_SETTLEMENT_REQUIRED,
    SETTLEMENT_READY,
    WNBA_SUPPORTED,
    build_settlement_inventory,
    settle_mlb_scalar,
    settle_wnba_scalar,
)


NOW = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
    def json(self): return self._payload


def _prediction(stat_type, *, line=5.5, direction="MORE", sport="MLB"):
    return {
        "prediction_id": "00000000-0000-0000-0000-000000000001",
        "event_id": "TEST-EVENT",
        "event_start_time": (NOW - timedelta(hours=4)).isoformat(),
        "player": "Test Player",
        "sport": sport,
        "stat_type": stat_type,
        "line": line,
        "direction": direction,
        "primary_failure_path": "TEST_FAILURE_PATH",
    }


def _mlb_feed(*, final=True, started=True, plate_appearances=4):
    pitching = {
        "gamesStarted": 1 if started else 0,
        "strikeOuts": 7,
        "inningsPitched": "5.2",
        "numberOfPitches": 91,
        "strikes": 58,
    }
    return {
        "gameData": {
            "status": {"abstractGameState": "Final" if final else "Live"},
            "players": {"ID123": {"id": 123, "fullName": "Test Player"}},
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {
                        "battingOrder": [123],
                        "players": {
                            "ID123": {
                                "person": {"id": 123, "fullName": "Test Player"},
                                "stats": {
                                    "pitching": pitching,
                                    "batting": {"plateAppearances": plate_appearances},
                                },
                            }
                        },
                    },
                    "home": {"battingOrder": [], "players": {}},
                }
            }
        },
    }


def test_inventory_accounts_for_every_required_sport_and_keeps_special_contracts_typed():
    rows = build_settlement_inventory()
    sports = {row["sport"] for row in rows}
    assert set(CERTIFICATION_SPORTS).issubset(sports)
    assert all(row["can_execute"] is False for row in rows)

    wnba_points = next(row for row in rows if row["sport"] == "WNBA" and row["stat_type"] == "POINTS")
    assert wnba_points["status"] == SETTLEMENT_READY

    one_ip = next(row for row in rows if row["sport"] == "MLB" and row["stat_type"] == "1ST_INNING_PITCHES_THROWN")
    assert one_ip["status"] == SEPARATE_SETTLEMENT_REQUIRED

    nba_fs = next(row for row in rows if row["sport"] == "NBA" and row["stat_type"] == "FANTASY_SCORE")
    assert nba_fs["status"] == FANTASY_COMPONENT_SETTLEMENT_REQUIRED

    ncaaf = next(row for row in rows if row["sport"] == "NCAAF")
    assert ncaaf["status"] == NO_CURRENT_PROP_CATEGORY_DECLARED


def test_mlb_pitching_routes_extract_exact_official_stats():
    snapshot = {"role_status": {"official_game_pk": 99, "player_id": 123}}
    feed = _mlb_feed()
    def get(*_args, **_kwargs): return _Response(feed)

    cases = {
        "PITCHER_STRIKEOUTS": 7.0,
        "PITCHING_OUTS": 17.0,
        "STRIKES_THROWN": 58.0,
        "BALLS_THROWN": 33.0,
    }
    for stat, expected in cases.items():
        result = settle_mlb_scalar(_prediction(stat), snapshot, http_get=get, now=NOW)
        assert result["status"] == "SETTLED"
        assert result["outcome"]["actual_stat"] == expected
        assert result["outcome"]["void"] is False
        assert result["outcome"]["settlement_source"].endswith("/game/99/feed/live")
        assert result["can_execute"] is False


def test_mlb_plate_appearances_requires_postgame_starting_order_identity():
    snapshot = {"role_status": {"official_game_pk": 99, "player_id": 123}}
    def get(*_args, **_kwargs): return _Response(_mlb_feed(plate_appearances=4))
    result = settle_mlb_scalar(
        _prediction("PLATE_APPEARANCES", line=3.5), snapshot, http_get=get, now=NOW
    )
    assert result["status"] == "SETTLED"
    assert result["outcome"]["actual_stat"] == 4.0
    assert result["outcome"]["official_result"] == "HIT"


def test_mlb_pitcher_late_role_change_voids_instead_of_grading_wrong_population():
    snapshot = {"role_status": {"official_game_pk": 99, "player_id": 123}}
    def get(*_args, **_kwargs): return _Response(_mlb_feed(started=False))
    result = settle_mlb_scalar(
        _prediction("PITCHER_STRIKEOUTS"), snapshot, http_get=get, now=NOW
    )
    assert result["status"] == "SETTLED_VOID_NOT_STARTER"
    assert result["outcome"]["void"] is True
    assert result["outcome"]["actual_stat"] is None
    assert result["outcome"]["failure_category"] == "NOT_STARTING_PITCHER"


def test_mlb_does_not_settle_before_official_final_state():
    snapshot = {"role_status": {"official_game_pk": 99, "player_id": 123}}
    def get(*_args, **_kwargs): return _Response(_mlb_feed(final=False))
    result = settle_mlb_scalar(
        _prediction("PITCHER_STRIKEOUTS"), snapshot, http_get=get, now=NOW
    )
    assert result["status"] == "NOT_FINAL"
    assert "outcome" not in result


def _wnba_schedule(final=True):
    return {
        "leagueSchedule": {
            "gameDates": [{
                "games": [{
                    "gameId": "1022600200",
                    "gameStatus": 3 if final else 2,
                }]
            }]
        }
    }


def _wnba_log_row():
    headers = ["GAME_ID", "PLAYER_ID", "PTS", "REB", "AST", "FG3M"]
    row = ["1022600200", 456, 24, 9, 7, 3]
    return {"resultSets": [{"name": "LeagueGameLog", "headers": headers, "rowSet": [row]}]}


def test_wnba_routes_extract_exact_stat_column_and_settle_direction():
    snapshot = {"role_status": {"official_game_id": "1022600200", "player_id": "456"}}
    calls = []
    def get(url, **_kwargs):
        calls.append(url)
        if "scheduleLeagueV2" in url:
            return _Response(_wnba_schedule())
        return _Response(_wnba_log_row())

    expected = {
        "POINTS": 24.0,
        "REBOUNDS": 9.0,
        "ASSISTS": 7.0,
        "THREE_POINTERS_MADE": 3.0,
    }
    assert set(expected).issubset({stat for sport, stat in WNBA_SUPPORTED if sport == "WNBA"})
    for stat, actual in expected.items():
        result = settle_wnba_scalar(
            _prediction(stat, line=actual - 0.5, sport="WNBA"),
            snapshot,
            http_get=get,
            now=NOW,
        )
        assert result["status"] == "SETTLED"
        assert result["outcome"]["actual_stat"] == actual
        assert result["outcome"]["official_result"] == "HIT"
        assert result["can_execute"] is False


def test_wnba_final_game_without_player_row_is_void_dnp():
    snapshot = {"role_status": {"official_game_id": "1022600200", "player_id": "456"}}
    empty = {"resultSets": [{"name": "LeagueGameLog", "headers": ["GAME_ID", "PLAYER_ID", "PTS"], "rowSet": []}]}
    def get(url, **_kwargs):
        return _Response(_wnba_schedule()) if "scheduleLeagueV2" in url else _Response(empty)
    result = settle_wnba_scalar(
        _prediction("POINTS", sport="WNBA"), snapshot, http_get=get, now=NOW
    )
    assert result["status"] == "SETTLED_VOID_DNP"
    assert result["outcome"]["void"] is True
    assert result["outcome"]["failure_category"] == "PLAYER_DNP"


def test_wnba_does_not_settle_live_game():
    snapshot = {"role_status": {"official_game_id": "1022600200", "player_id": "456"}}
    def get(*_args, **_kwargs): return _Response(_wnba_schedule(final=False))
    result = settle_wnba_scalar(
        _prediction("POINTS", sport="WNBA"), snapshot, http_get=get, now=NOW
    )
    assert result["status"] == "NOT_FINAL"
    assert "outcome" not in result
