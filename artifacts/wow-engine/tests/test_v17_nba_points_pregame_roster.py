"""Strict cross-provider pregame NBA player roster proof; all synthetic fixtures."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v17.nba_points_pregame_roster import (
    NBAPregameRosterError, verify_nba_points_current_roster,
)


def fixtures():
    now = datetime.now(timezone.utc)
    kickoff = (now + timedelta(hours=6)).isoformat()
    capture_a = (now - timedelta(minutes=5)).isoformat()
    capture_b = (now - timedelta(minutes=4)).isoformat()
    summary = {
        "header": {
            "id": "401000123",
            "competitions": [{
                "id": "401000123", "date": kickoff,
                "competitors": [
                    {"homeAway": "home", "team": {"id": "2", "abbreviation": "BOS"}},
                    {"homeAway": "away", "team": {"id": "9", "abbreviation": "GS"}},
                ],
            }],
        },
    }
    box = {"game": {
        "gameId": "0022600123", "gameStatus": 1, "gameTimeUTC": kickoff,
        "homeTeam": {
            "teamTricode": "BOS", "players": [
                {"personId": 1629001, "name": "Sample Player",
                 "status": "ACTIVE", "notPlayingReason": "",
                 "notPlayingDescription": ""},
            ],
        },
        "awayTeam": {
            "teamTricode": "GS", "players": [
                {"personId": 1629002, "name": "Different Player",
                 "status": "ACTIVE", "notPlayingReason": ""},
            ],
        },
    }}
    return {
        "espn_event_id": "espn-401000123", "nba_game_id": "0022600123",
        "player_name": "Sample Player", "espn_summary": summary,
        "nba_boxscore": box, "espn_captured_at": capture_a,
        "nba_captured_at": capture_b, "now": now,
    }


def call(x=None):
    return verify_nba_points_current_roster(**(x or fixtures()))


def fails(error, modify):
    x = fixtures()
    modify(x)
    with pytest.raises(NBAPregameRosterError) as result:
        call(x)
    assert result.value.code == error


def test_exact_two_source_pregame_active_roster_no_model_or_line():
    x = fixtures()
    old = deepcopy(x)
    result = call(x)
    assert x == old
    assert result["status"] == "NBA_CURRENT_ROSTER_ACTIVE_SOURCE_VERIFIED_ROLE_NOT_PROVEN"
    assert result["roster"]["event_id"] == "espn-401000123"
    assert result["roster"]["team_id"] == "espn-2"
    assert result["roster"]["player_id"] == "1629001"
    assert result["source_identity"]["nba_game_id"] == "0022600123"
    assert result["role_ledger_ready"] is False
    assert result["exact_board_line_present"] is False
    assert result["probability_publishable"] is False
    assert result["write_performed"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("error,modify", [
    ("NBA_ROSTER_ESPN_EVENT_MISMATCH",
     lambda x: x["espn_summary"]["header"].update(id="401000999")),
    ("NBA_ROSTER_ESPN_COMPETITION_MISMATCH",
     lambda x: x["espn_summary"]["header"]["competitions"][0].update(id="401000999")),
    ("NBA_ROSTER_NBA_GAME_ID_MISMATCH",
     lambda x: x["nba_boxscore"]["game"].update(gameId="0022600999")),
    ("NBA_ROSTER_NBA_GAME_NOT_SCHEDULED",
     lambda x: x["nba_boxscore"]["game"].update(gameStatus=2)),
    ("NBA_ROSTER_CROSS_PROVIDER_KICKOFF_MISMATCH",
     lambda x: x["nba_boxscore"]["game"].update(gameTimeUTC=(x["now"]+timedelta(hours=7)).isoformat())),
    ("NBA_ROSTER_CROSS_PROVIDER_TEAM_MISMATCH",
     lambda x: x["nba_boxscore"]["game"]["homeTeam"].update(teamTricode="NYK")),
    ("NBA_ROSTER_ESPN_TEAMS_INVALID",
     lambda x: x["espn_summary"]["header"]["competitions"][0]["competitors"][1].update(homeAway="home")),
    ("NBA_ROSTER_ACTIVE_STATUS_NOT_AFFIRMED",
     lambda x: x["nba_boxscore"]["game"]["homeTeam"]["players"][0].update(status="INACTIVE")),
    ("NBA_ROSTER_NOT_PLAYING_REASON_PRESENT",
     lambda x: x["nba_boxscore"]["game"]["homeTeam"]["players"][0].update(notPlayingReason="INJURY")),
    ("NBA_ROSTER_PLAYER_MISSING_OR_AMBIGUOUS",
     lambda x: x.update(player_name="Unknown Player")),
    ("NBA_ROSTER_PLAYER_MISSING_OR_AMBIGUOUS",
     lambda x: x["nba_boxscore"]["game"]["homeTeam"]["players"].append(deepcopy(x["nba_boxscore"]["game"]["homeTeam"]["players"][0]))),
    ("NBA_ROSTER_PLAYER_MISSING_OR_AMBIGUOUS",
     lambda x: x["nba_boxscore"]["game"]["awayTeam"]["players"][0].update(name="Sample Player")),
    ("NBA_ROSTER_NBA_PLAYER_ID_INVALID",
     lambda x: x["nba_boxscore"]["game"]["homeTeam"]["players"][0].update(personId="bad-nba-person-id")),
    ("NBA_ROSTER_ESPN_COMPETITION_AMBIGUOUS",
     lambda x: x["espn_summary"]["header"].update(competitions=[])),
    ("NBA_ROSTER_ESPN_CAPTURE_NAIVE",
     lambda x: x.update(espn_captured_at="2026-10-22T19:05:00")),
    ("NBA_ROSTER_CAPTURE_IN_FUTURE",
     lambda x: x.update(nba_captured_at=(x["now"] + timedelta(seconds=10)).isoformat())),
    ("NBA_ROSTER_NOT_PREGAME",
     lambda x: x["espn_summary"]["header"]["competitions"][0].update(date=(x["now"]-timedelta(minutes=1)).isoformat())),
    ("NBA_ROSTER_NOT_PREGAME",
     lambda x: x.update(nba_captured_at=(x["now"]+timedelta(hours=7)).isoformat(),
                        now=x["now"]+timedelta(hours=8))),
])
def test_exact_roster_negative_conditions(error, modify):
    fails(error, modify)


def test_case_whitespace_normalized_to_exact_player_name():
    x = fixtures()
    x["player_name"] = "  sample   player  "
    assert call(x)["roster"]["player"] == "sample player"


def test_cannot_promote_unchanged_roster_evidence_to_role_or_model_readiness():
    x = fixtures()
    x["nba_boxscore"]["game"]["homeTeam"]["players"][0]["starter"] = True
    result = call(x)
    assert result["role_ledger_ready"] is False
    assert result["probability_publishable"] is False
    assert not result["can_execute"]
