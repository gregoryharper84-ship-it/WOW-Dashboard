from datetime import datetime, timedelta, timezone

import pytest

import wnba_prop_auto_hydration as w
import prop_auto_hydration_router  # noqa: F401 -- installs canonical strict WNBA status parser


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, payload=None, status_code=200, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.content = content

    def json(self):
        return self._payload


def result_set(name, rows):
    headers = list(rows[0]) if rows else []
    return {
        "resultSets": [
            {
                "name": name,
                "headers": headers,
                "rowSet": [[row[h] for h in headers] for row in rows],
            }
        ]
    }


def schedule_payload(status=1):
    return {
        "leagueSchedule": {
            "gameDates": [
                {
                    "gameDate": "2026-08-30",
                    "games": [
                        {
                            "gameId": "1022600200",
                            "gameDateTimeUTC": EVENT.isoformat().replace("+00:00", "Z"),
                            "gameStatus": status,
                            "gameStatusText": "Scheduled" if status == 1 else "Final",
                            "homeTeam": {
                                "teamId": "2",
                                "teamCity": "Beta",
                                "teamName": "Belles",
                                "teamTricode": "BBB",
                            },
                            "awayTeam": {
                                "teamId": "1",
                                "teamCity": "Alpha",
                                "teamName": "Aces",
                                "teamTricode": "AAA",
                            },
                        }
                    ],
                }
            ]
        }
    }


def roster_payload(team_id):
    if str(team_id) == "1":
        rows = [
            {
                "TeamID": "1",
                "SEASON": "2026",
                "LeagueID": "10",
                "PLAYER": "Test Player",
                "POSITION": "G",
                "PLAYER_ID": "1001",
            }
        ]
    else:
        rows = [
            {
                "TeamID": "2",
                "SEASON": "2026",
                "LeagueID": "10",
                "PLAYER": "Opponent Player",
                "POSITION": "F",
                "PLAYER_ID": "2001",
            }
        ]
    return result_set("CommonTeamRoster", rows)


def game_log_payload(n=10):
    rows = []
    for i in range(n):
        game_date = (EVENT.date() - timedelta(days=i + 1)).isoformat()
        rows.append(
            {
                "PLAYER_ID": "1001",
                "PLAYER_NAME": "Test Player",
                "TEAM_ABBREVIATION": "AAA",
                "GAME_ID": f"g{i}",
                "GAME_DATE": game_date,
                "MATCHUP": "AAA vs. CCC",
                "MIN": 30 + (i % 3),
                "PTS": 10 + i,
                "REB": 4 + (i % 4),
                "AST": 3 + (i % 3),
                "FG3M": i % 4,
            }
        )
    return result_set("LeagueGameLog", rows)


def http_get_factory(*, log_n=10, schedule_status=1):
    calls = []

    def get(url, params=None, **kwargs):
        calls.append((url, dict(params or {})))
        if url == w.WNBA_SCHEDULE_URL:
            return Response(schedule_payload(schedule_status))
        if url.endswith("/commonteamroster"):
            return Response(roster_payload((params or {}).get("TeamID")))
        if url.endswith("/leaguegamelog"):
            assert list((params or {}).keys())[0] == "LeagueID"
            assert (params or {})["LeagueID"] == "10"
            assert (params or {})["PlayerOrTeam"] == "P"
            return Response(game_log_payload(log_n))
        raise AssertionError(f"unexpected URL {url}")

    return get, calls


def submitted_report(player_line="Other Player Out Injury Illness"):
    return (
        "Injury Report: 08/30/26 08:00 AM "
        "Game Date Game Time Matchup Team Player Name Current Status Reason "
        "08/30/2026 08:00 (ET) AAA@BBB Alpha Aces "
        f"{player_line} Beta Belles Other Player Out Coach Decision"
    )


def test_availability_passes_only_when_team_submitted_and_player_not_listed():
    result = w._availability_from_report(
        submitted_report(),
        player_name="Test Player",
        team_name="Alpha Aces",
        matchup="AAA@BBB",
        game_date="08/30/2026",
    )
    assert result["availability"] == "NOT_LISTED_ON_FRESH_OFFICIAL_INJURY_REPORT"


def test_explicit_questionable_designation_blocks_unconditional_model_path():
    with pytest.raises(w.WNBAPropHydrationError) as exc:
        w._availability_from_report(
            submitted_report("Test Player Questionable Injury Illness Right Ankle"),
            player_name="Test Player",
            team_name="Alpha Aces",
            matchup="AAA@BBB",
            game_date="08/30/2026",
        )
    assert exc.value.code == "WNBA_PLAYER_AVAILABILITY_NOT_CLEAR"
    assert exc.value.detail["designation"] == "QUESTIONABLE"


def test_available_target_is_not_overread_as_later_player_out():
    result = w._availability_from_report(
        submitted_report("Test Player Available Returned To Competition"),
        player_name="Test Player",
        team_name="Alpha Aces",
        matchup="AAA@BBB",
        game_date="08/30/2026",
    )
    assert result["availability"] == "AVAILABLE"
    assert result["designation"] == "AVAILABLE"


def test_not_yet_submitted_blocks_omission_logic():
    with pytest.raises(w.WNBAPropHydrationError) as exc:
        w._availability_from_report(
            "08/30/2026 AAA@BBB Alpha Aces NOT YET SUBMITTED Beta Belles Other Player Out",
            player_name="Test Player",
            team_name="Alpha Aces",
            matchup="AAA@BBB",
            game_date="08/30/2026",
        )
    assert exc.value.code == "WNBA_INJURY_REPORT_NOT_SUBMITTED"


def test_full_hydration_returns_exact_l10_minutes_role_and_opportunity(monkeypatch):
    get, calls = http_get_factory()
    monkeypatch.setattr(
        w,
        "_latest_injury_report",
        lambda now, http_get: (
            "https://ak-static.cms.nba.com/referee/wnba_injury/test.pdf",
            NOW.astimezone(w.ET),
            submitted_report(),
        ),
    )
    result = w.hydrate_wnba_prop_evidence(
        player="Test Player",
        stat_type="POINTS",
        event_start_time=EVENT.isoformat(),
        now=NOW,
        opponent="BBB",
        http_get=get,
    )
    assert result["game_log"] == [float(10 + i) for i in range(10)]
    assert len(result["box_score_log"]) == 10
    assert all("date" in row and "minutes" in row for row in result["box_score_log"])
    assert result["role_status"]["team_tricode"] == "AAA"
    assert result["role_status"]["opponent_tricode"] == "BBB"
    assert result["role_status"]["official_game_id"] == "1022600200"
    assert result["opportunity_ledger"]["status"] == "READY"
    assert result["opportunity_ledger"]["availability_gate"] == "PASS"
    assert result["evidence_version"] == "PROP_EVIDENCE_V1"
    assert result["hydration_provider"] == w.PROVIDER_ID
    assert "can_execute" not in result
    assert len(calls) == 4  # schedule + two rosters + player log


def test_event_already_started_stops_before_http(monkeypatch):
    calls = []

    def get(*args, **kwargs):
        calls.append(args)
        raise AssertionError("HTTP must not be called")

    with pytest.raises(w.WNBAPropHydrationError) as exc:
        w.hydrate_wnba_prop_evidence(
            player="Test Player",
            stat_type="POINTS",
            event_start_time=(NOW - timedelta(minutes=1)).isoformat(),
            now=NOW,
            http_get=get,
        )
    assert exc.value.code == "EVENT_ALREADY_STARTED"
    assert calls == []


def test_requested_opponent_conflict_fails_closed(monkeypatch):
    get, _ = http_get_factory()
    monkeypatch.setattr(
        w,
        "_latest_injury_report",
        lambda now, http_get: ("report.pdf", NOW.astimezone(w.ET), submitted_report()),
    )
    with pytest.raises(w.WNBAPropHydrationError) as exc:
        w.hydrate_wnba_prop_evidence(
            player="Test Player",
            stat_type="ASSISTS",
            event_start_time=EVENT.isoformat(),
            now=NOW,
            opponent="Wrong Team",
            http_get=get,
        )
    assert exc.value.code == "PROP_EVENT_IDENTITY_CONFLICT"


def test_insufficient_prior_games_fails_closed(monkeypatch):
    get, _ = http_get_factory(log_n=9)
    monkeypatch.setattr(
        w,
        "_latest_injury_report",
        lambda now, http_get: ("report.pdf", NOW.astimezone(w.ET), submitted_report()),
    )
    with pytest.raises(w.WNBAPropHydrationError) as exc:
        w.hydrate_wnba_prop_evidence(
            player="Test Player",
            stat_type="REBOUNDS",
            event_start_time=EVENT.isoformat(),
            now=NOW,
            http_get=get,
        )
    assert exc.value.code == "WNBA_RECENT_GAMES_INSUFFICIENT"


def test_schedule_started_status_blocks_before_roster_or_logs():
    get, calls = http_get_factory(schedule_status=3)
    with pytest.raises(w.WNBAPropHydrationError) as exc:
        w.hydrate_wnba_prop_evidence(
            player="Test Player",
            stat_type="3PM",
            event_start_time=EVENT.isoformat(),
            now=NOW,
            http_get=get,
        )
    assert exc.value.code == "EVENT_ALREADY_STARTED"
    assert len(calls) == 1
