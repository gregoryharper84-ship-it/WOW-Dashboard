from datetime import datetime, timezone

import pytest

from prop_auto_hydration import PropAutoHydrationError
import prop_auto_hydration_router as router
import v17.nba_prop_hydration_overlay  # noqa: F401 - installs reviewed research-only route
from v17.nba_prop_auto_hydration import (
    NBAPropHydrationError,
    PROVIDER_ID,
    hydrate_nba_prop_evidence,
)


EVENT_START = "2026-10-22T00:00:00+00:00"
NOW = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _schedule():
    game = {
        "gameId": "0022600010",
        "gameStatus": 1,
        "gameStatusText": "7:00 pm ET",
        "gameDateTimeUTC": EVENT_START,
        "homeTeam": {
            "teamId": 1610612742,
            "teamCity": "Dallas",
            "teamName": "Mavericks",
            "teamTricode": "DAL",
        },
        "awayTeam": {
            "teamId": 1610612745,
            "teamCity": "Houston",
            "teamName": "Rockets",
            "teamTricode": "HOU",
        },
    }
    return {"leagueSchedule": {"gameDates": [{"games": [game]}]}}


def _roster(team_id):
    headers = ["PLAYER_ID", "PLAYER", "POSITION"]
    rows = [[12345, "Test Player", "G"]] if str(team_id) == "1610612742" else [[99999, "Other Player", "F"]]
    return {"resultSets": [{"name": "CommonTeamRoster", "headers": headers, "rowSet": rows}]}


def _game_log(season):
    headers = [
        "GAME_ID", "PLAYER_ID", "PLAYER_NAME", "GAME_DATE", "TEAM_ABBREVIATION",
        "MATCHUP", "MIN", "PTS", "REB", "AST",
    ]
    rows = []
    if season == "2026-27":
        rows = [
            ["0022600008", 12345, "Test Player", "2026-10-20", "DAL", "DAL vs. SAS", 34, 20, 7, 8],
            # Event-date row must never enter pregame evidence even if returned by provider.
            ["0022600010", 12345, "Test Player", "2026-10-22", "DAL", "DAL vs. HOU", 1, 2, 0, 0],
        ]
    elif season == "2025-26":
        for index in range(9):
            day = 10 - index
            rows.append([
                f"00225000{index:02d}", 12345, "Test Player", f"2026-04-{day:02d}",
                "DAL", "DAL vs. HOU", 30 + (index % 5), 18 + index, 5 + (index % 3), 4 + (index % 4),
            ])
    return {"resultSets": [{"name": "LeagueGameLog", "headers": headers, "rowSet": rows}]}


def _get(url, *, params=None, **_kwargs):
    params = params or {}
    if "scheduleLeagueV2" in url:
        return _Response(_schedule())
    if "commonteamroster" in url:
        return _Response(_roster(params.get("TeamID")))
    if "leaguegamelog" in url:
        return _Response(_game_log(str(params.get("Season"))))
    raise AssertionError(url)


def test_nba_candidate_hydration_binds_exact_official_identity_and_prior_history():
    result = hydrate_nba_prop_evidence(
        player="Test Player",
        stat_type="PRA",
        event_start_time=EVENT_START,
        http_get=_get,
        now=NOW,
        opponent="HOU",
    )

    assert result["role_status"]["official_game_id"] == "0022600010"
    assert result["role_status"]["player_id"] == "12345"
    assert result["role_status"]["team_tricode"] == "DAL"
    assert result["role_status"]["opponent_tricode"] == "HOU"
    assert result["role_status"]["production_availability_certified"] is False
    assert result["role_status"]["availability"] == "UNVERIFIED_RESEARCH_ONLY"
    assert len(result["game_log"]) == 10
    assert result["game_log"][0] == 35.0  # 20 PTS + 7 REB + 8 AST from 10/20
    assert all(row["date"] < "2026-10-22" for row in result["box_score_log"])
    assert result["research_only"] is True
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_nba_provider_registration_is_narrow_and_does_not_claim_unbuilt_3pm():
    assert router.provider_for_sport("NBA", "POINTS") == PROVIDER_ID
    assert router.provider_for_sport("NBA", "PRA") == PROVIDER_ID
    assert router.provider_for_sport("NBA", "THREE_POINTERS_MADE") == router.UNREGISTERED_PROVIDER


def test_nba_hydration_rejects_unbuilt_route_and_started_event():
    with pytest.raises(NBAPropHydrationError) as exc_info:
        hydrate_nba_prop_evidence(
            player="Test Player",
            stat_type="THREE_POINTERS_MADE",
            event_start_time=EVENT_START,
            http_get=_get,
            now=NOW,
        )
    assert exc_info.value.code == "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE"

    with pytest.raises(NBAPropHydrationError) as exc_info:
        hydrate_nba_prop_evidence(
            player="Test Player",
            stat_type="POINTS",
            event_start_time="2026-09-27T00:00:00+00:00",
            http_get=_get,
            now=NOW,
        )
    assert exc_info.value.code == "EVENT_ALREADY_STARTED"


def test_router_translates_nba_hydration_failure_to_typed_shared_error():
    with pytest.raises(PropAutoHydrationError) as exc_info:
        router.auto_hydrate_prop_evidence(
            sport="NBA",
            player="Test Player",
            stat_type="THREE_POINTERS_MADE",
            event_start_time=EVENT_START,
            http_get=_get,
            now=NOW,
        )
    assert exc_info.value.code == "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE"
