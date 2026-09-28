from datetime import datetime, timedelta, timezone

import v17.mlb_1ip_exact_settlement_overlay  # noqa: F401
import v17.prop_exact_route_settlement as settlement


NOW = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _prediction(*, line=11.5, direction="MORE"):
    return {
        "prediction_id": "00000000-0000-0000-0000-000000000101",
        "event_id": "MLB-TEST-1IP",
        "event_start_time": (NOW - timedelta(hours=4)).isoformat(),
        "player": "Test Starter",
        "sport": "MLB",
        "stat_type": "1ST_INNING_PITCHES_THROWN",
        "line": line,
        "direction": direction,
        "primary_failure_path": "TEST_FAILURE_PATH",
    }


def _pitch_events(n):
    return [{"isPitch": True, "index": idx} for idx in range(n)]


def _feed(*, final=True, started=True, include_first=True):
    all_plays = []
    if include_first:
        all_plays.extend([
            {
                "about": {"inning": 1, "halfInning": "top"},
                "matchup": {"pitcher": {"id": 123}},
                "playEvents": _pitch_events(4) + [{"isPitch": False}],
            },
            {
                "about": {"inning": 1, "halfInning": "top"},
                "matchup": {"pitcher": {"id": 123}},
                "playEvents": _pitch_events(5),
            },
            {
                "about": {"inning": 1, "halfInning": "top"},
                "matchup": {"pitcher": {"id": 123}},
                "playEvents": _pitch_events(3),
            },
        ])
    all_plays.extend([
        {
            "about": {"inning": 1, "halfInning": "bottom"},
            "matchup": {"pitcher": {"id": 999}},
            "playEvents": _pitch_events(8),
        },
        {
            "about": {"inning": 2, "halfInning": "top"},
            "matchup": {"pitcher": {"id": 123}},
            "playEvents": _pitch_events(6),
        },
    ])
    return {
        "gameData": {
            "status": {"abstractGameState": "Final" if final else "Live"},
            "players": {"ID123": {"id": 123, "fullName": "Test Starter"}},
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {"players": {}},
                    "home": {
                        "players": {
                            "ID123": {
                                "person": {"id": 123, "fullName": "Test Starter"},
                                "stats": {"pitching": {"gamesStarted": 1 if started else 0}},
                            }
                        }
                    },
                }
            },
            "plays": {"allPlays": all_plays},
        },
    }


def test_1ip_route_becomes_exact_settlement_ready_without_granting_execution():
    rows = settlement.build_settlement_inventory()
    row = next(
        item for item in rows
        if item["sport"] == "MLB" and item["stat_type"] == "1ST_INNING_PITCHES_THROWN"
    )
    assert row["status"] == settlement.SETTLEMENT_READY
    assert row["official_source"] == "MLB_STATS_API_OFFICIAL_GAME_FEED"
    assert row["blocker"] is None
    assert row["can_execute"] is False


def test_1ip_counts_only_exact_starter_first_inning_pitch_events():
    snapshot = {"role_status": {"official_game_pk": 99, "player_id": 123}}

    def get(*_args, **_kwargs):
        return _Response(_feed())

    result = settlement.settle_mlb_scalar(
        _prediction(line=11.5), snapshot, http_get=get, now=NOW
    )
    assert result["status"] == "SETTLED"
    assert result["outcome"]["actual_stat"] == 12.0
    assert result["outcome"]["official_result"] == "HIT"
    assert result["settlement_method"] == "EXACT_OFFICIAL_INNING1_PITCH_EVENTS_BY_STARTER_ID"
    assert result["matched_play_n"] == 3
    assert result["can_execute"] is False


def test_1ip_nonstarter_voids_instead_of_grading_wrong_pitcher_role():
    snapshot = {"role_status": {"official_game_pk": 99, "player_id": 123}}

    def get(*_args, **_kwargs):
        return _Response(_feed(started=False))

    result = settlement.settle_mlb_scalar(_prediction(), snapshot, http_get=get, now=NOW)
    assert result["status"] == "SETTLED_VOID_NOT_STARTER"
    assert result["outcome"]["void"] is True
    assert result["outcome"]["failure_category"] == "NOT_STARTING_PITCHER"


def test_1ip_requires_final_and_exact_event_tree():
    snapshot = {"role_status": {"official_game_pk": 99, "player_id": 123}}

    def live_get(*_args, **_kwargs):
        return _Response(_feed(final=False))

    live = settlement.settle_mlb_scalar(_prediction(), snapshot, http_get=live_get, now=NOW)
    assert live["status"] == "NOT_FINAL"
    assert "outcome" not in live

    def missing_get(*_args, **_kwargs):
        return _Response(_feed(include_first=False))

    missing = settlement.settle_mlb_scalar(_prediction(), snapshot, http_get=missing_get, now=NOW)
    assert missing["status"] == "OFFICIAL_1IP_EVENT_TREE_MISSING"
    assert missing["blocker"] == "MLB_1IP_EXACT_EVENT_TREE_SETTLEMENT_UNAVAILABLE"
    assert missing["can_execute"] is False
