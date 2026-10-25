from datetime import datetime, timezone
from copy import deepcopy
from types import SimpleNamespace

from v17.nba_exact_scalar_settlement import evaluate_nba_scalar_outcome, NBA_SCALAR_STATS

NOW = datetime(2026, 10, 26, 18, tzinfo=timezone.utc)
START = "2026-10-25T19:00:00Z"
IDENTITY = {"nba_game_id": "0022600001", "nba_person_id": "12345", "espn_event_id": "espn-98765", "event_start_time": START, "espn_captured_at": "2026-10-25T17:00:00Z", "nba_captured_at": "2026-10-25T17:01:00Z"}
PRED = {"source_snapshot_id": "sid", "sport": "NBA", "stat_type": "ASSISTS", "line": 6.5, "event_start_time": START, "event_id": "espn-98765", "player": "Example Player"}
SNAP = {**PRED, "role_status": {"source_identity": IDENTITY}}
PLAYER = {"personId": 12345, "name": "Example Player", "played": "1", "statistics": {"points": 20, "assists": 7, "reboundsTotal": 9}}
BOX = {"game": {"gameId": "0022600001", "gameTimeUTC": START, "gameStatus": 3, "homeTeam": {"players": [PLAYER]}, "awayTeam": {"players": []}}}

def fetch(payload):
    def get(url, *, timeout, follow_redirects):
        assert url.endswith("boxscore_0022600001.json") and timeout <= 12 and follow_redirects is False
        return SimpleNamespace(status_code=200, json=lambda: payload)
    return get

def check(pred=None, snap=None, box=None):
    return evaluate_nba_scalar_outcome(pred or PRED, snap or SNAP, http_get=fetch(box or BOX), now=NOW)

def test_seven_exact_stats_and_composites():
    expected = {"POINTS": 20, "REBOUNDS": 9, "ASSISTS": 7, "PRA": 36, "POINTS_REBOUNDS": 29, "POINTS_ASSISTS": 27, "REBOUNDS_ASSISTS": 16}
    assert NBA_SCALAR_STATS == set(expected)
    for stat, total in expected.items():
        result = check({**PRED, "stat_type": stat}, {**SNAP, "stat_type": stat})
        assert result["status"] == "SETTLED" and result["actual_stat"] == total and result["can_execute"] is False

def test_wrong_event_line_snapshot_name_and_time_fail_closed():
    for change in [{"event_id": "espn-999"}, {"line": 9.5}, {"source_snapshot_id": "alien"}, {"event_start_time": "2026-10-25T20:00:00Z"}, {"player": "Wrong Player"}]:
        assert check({**PRED, **change})["status"] == "IDENTITY_UNRESOLVED"

def test_started_capture_spoofed_source_fails_closed():
    for update in [{"nba_captured_at": "2026-10-25T20:00:00Z"}, {"nba_game_id": "https://evil.test"}, {"espn_event_id": "espn-999"}]:
        snap = deepcopy(SNAP)
        snap["role_status"]["source_identity"].update(update)
        assert check(snap=snap)["status"] == "IDENTITY_UNRESOLVED"

def test_final_status_game_identity_required():
    box = deepcopy(BOX); box["game"]["gameStatus"] = 2
    assert check(box=box)["status"] == "NOT_FINAL"
    box = deepcopy(BOX); box["game"]["gameId"] = "0022600099"
    assert check(box=box)["status"] == "OFFICIAL_EVENT_ID_MISMATCH"

def test_ambiguous_or_missing_player_does_not_void():
    box = deepcopy(BOX); box["game"]["awayTeam"]["players"].append(deepcopy(PLAYER))
    assert check(box=box)["status"] == "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS"
    box = deepcopy(BOX); box["game"]["homeTeam"]["players"] = []
    assert check(box=box)["status"] == "OFFICIAL_PLAYER_EVENT_IDENTITY_AMBIGUOUS"

def test_explicit_dnp_only_voids():
    box = deepcopy(BOX); box["game"]["homeTeam"]["players"][0].update({"played": "0", "notPlayingReason": "INACTIVE"})
    assert check(box=box)["status"] == "SETTLED_VOID_DNP"
    box["game"]["homeTeam"]["players"][0]["notPlayingReason"] = ""
    assert check(box=box)["status"] == "OFFICIAL_PLAYER_PARTICIPATION_UNPROVEN"

def test_nan_bool_and_missing_stats_fail():
    for value in [float("nan"), None, True, -2, "7"]:
        box = deepcopy(BOX); box["game"]["homeTeam"]["players"][0]["statistics"]["assists"] = value
        assert check(box=box)["status"] == "OFFICIAL_STAT_MISSING"

def test_source_failure_typed():
    def broken(*args, **kwargs):
        raise TimeoutError("network")
    result = evaluate_nba_scalar_outcome(PRED, SNAP, http_get=broken, now=NOW)
    assert result["status"] == "OFFICIAL_SOURCE_UNAVAILABLE"
