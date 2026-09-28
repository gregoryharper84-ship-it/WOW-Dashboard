from datetime import datetime, timezone

import v17.nba_prop_settlement_overlay  # noqa: F401 - installs settlement route
import v17.prop_exact_route_settlement as settlement


NOW = datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _prediction(stat_type, *, line=20.5, direction="MORE"):
    return {
        "prediction_id": "00000000-0000-0000-0000-000000000202",
        "event_id": "NBA:0022600010",
        "event_start_time": "2026-10-22T00:00:00+00:00",
        "player": "Test Player",
        "sport": "NBA",
        "stat_type": stat_type,
        "line": line,
        "direction": direction,
        "primary_failure_path": "TEST_FAILURE_PATH",
    }


def _boxscore(*, final=True, include_player=True):
    players = []
    if include_player:
        players.append({
            "personId": 12345,
            "name": "Test Player",
            "statistics": {
                "points": 24,
                "reboundsTotal": 9,
                "assists": 7,
            },
        })
    return {
        "game": {
            "gameId": "0022600010",
            "gameStatus": 3 if final else 2,
            "gameStatusText": "Final" if final else "Q4",
            "homeTeam": {"players": players},
            "awayTeam": {"players": []},
        }
    }


def _snapshot():
    return {"role_status": {"official_game_id": "0022600010", "player_id": "12345"}}


def test_nba_candidate_routes_are_exact_settlement_ready_but_not_execution_authority():
    rows = settlement.build_settlement_inventory()
    points = next(row for row in rows if row["sport"] == "NBA" and row["stat_type"] == "POINTS")
    pra = next(row for row in rows if row["sport"] == "NBA" and row["stat_type"] == "PRA")
    assert points["status"] == settlement.SETTLEMENT_READY
    assert points["official_source"] == "NBA_OFFICIAL_CDN_LIVE_BOX_SCORE"
    assert points["can_execute"] is False
    assert pra["status"] == settlement.SETTLEMENT_READY
    assert ("NBA", "THREE_POINTERS_MADE") not in settlement.NBA_SUPPORTED


def test_nba_settlement_uses_exact_same_player_event_components():
    def get(*_args, **_kwargs):
        return _Response(_boxscore())

    points = settlement.settle_nba_scalar(
        _prediction("POINTS", line=23.5), _snapshot(), http_get=get, now=NOW
    )
    assert points["status"] == "SETTLED"
    assert points["outcome"]["actual_stat"] == 24.0
    assert points["outcome"]["official_result"] == "HIT"
    assert points["can_execute"] is False

    pra = settlement.settle_nba_scalar(
        _prediction("PRA", line=39.5), _snapshot(), http_get=get, now=NOW
    )
    assert pra["status"] == "SETTLED"
    assert pra["outcome"]["actual_stat"] == 40.0
    assert pra["settlement_components"] == {"POINTS": 24.0, "REBOUNDS": 9.0, "ASSISTS": 7.0}
    assert pra["settlement_method"] == "EXACT_OFFICIAL_NBA_PLAYER_EVENT_COMPONENT_SUM"


def test_nba_settlement_does_not_grade_live_game_or_missing_identity():
    def live_get(*_args, **_kwargs):
        return _Response(_boxscore(final=False))

    live = settlement.settle_nba_scalar(
        _prediction("POINTS"), _snapshot(), http_get=live_get, now=NOW
    )
    assert live["status"] == "NOT_FINAL"
    assert "outcome" not in live

    missing_identity = settlement.settle_nba_scalar(
        _prediction("POINTS"), {"role_status": {}}, http_get=live_get, now=NOW
    )
    assert missing_identity["status"] == "IDENTITY_UNRESOLVED"
    assert missing_identity["blocker"] == "OFFICIAL_NBA_GAME_ID_AND_PLAYER_ID_REQUIRED"


def test_nba_final_game_without_exact_player_is_typed_void():
    def get(*_args, **_kwargs):
        return _Response(_boxscore(include_player=False))

    result = settlement.settle_nba_scalar(
        _prediction("POINTS"), _snapshot(), http_get=get, now=NOW
    )
    assert result["status"] == "SETTLED_VOID_DNP"
    assert result["outcome"]["void"] is True
    assert result["outcome"]["failure_category"] == "PLAYER_DNP"
    assert result["can_execute"] is False
