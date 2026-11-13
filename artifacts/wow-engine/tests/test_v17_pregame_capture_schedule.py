from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from v17.pregame_capture_schedule import (
    NBA_WINDOW,
    NHL_WINDOW,
    CaptureTransportError,
    discover_pregame_events,
    plan_pregame_capture_cycle,
    run_pregame_capture_cycle,
    select_retained_receipts,
    window_for,
)
from v17.pregame_source_capture import SourceFetch

NOW = datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)
ESPN_ID = "401909936"
NHL_ID = "2026020251"


def _fetch(url: str, payload) -> SourceFetch:
    return SourceFetch(
        url=url,
        status=200,
        received_at_utc=NOW,
        body=json.dumps(payload).encode("utf-8"),
        http_date="Thu, 05 Nov 2026 17:00:00 GMT",
        content_type="application/json",
    )


def _event(event_id: str, minutes: int, *, sport: str = "NBA") -> dict:
    return {
        "sport": sport,
        "official_event_id": event_id if sport == "NHL" else f"espn-{event_id}",
        "provider_event_id": event_id,
        "start_time_utc": (NOW + timedelta(minutes=minutes)).isoformat(),
        "state": "PRE" if sport == "NBA" else "FUT",
        "home": "PHX",
        "away": "ORL",
    }


def _nba_board(events: list[tuple[str, int, str, bool]]) -> dict:
    payload = []
    for event_id, minutes, state, with_teams in events:
        start = NOW + timedelta(minutes=minutes)
        competitors = (
            [
                {"homeAway": "home", "team": {"id": "21", "abbreviation": "PHX"}},
                {"homeAway": "away", "team": {"id": "19", "abbreviation": "ORL"}},
            ]
            if with_teams
            else []
        )
        payload.append(
            {
                "id": event_id,
                "date": start.isoformat(),
                "status": {"type": {"state": state}},
                "competitions": [
                    {
                        "id": event_id,
                        "date": start.isoformat(),
                        "status": {"type": {"state": state}},
                        "competitors": competitors,
                    }
                ],
            }
        )
    return {"events": payload}


def _nhl_schedule(games: list[tuple[str, int, str]]) -> dict:
    return {
        "gameWeek": [
            {
                "date": "2026-11-05",
                "games": [
                    {
                        "id": event_id,
                        "startTimeUTC": (NOW + timedelta(minutes=minutes)).isoformat(),
                        "gameState": state,
                        "homeTeam": {"abbrev": "MTL"},
                        "awayTeam": {"abbrev": "UTA"},
                    }
                    for event_id, minutes, state in games
                ],
            }
        ]
    }


def _nba_fetcher(board: dict, summary: dict | None = None):
    payloads = {"board": board, "summary": summary or _nba_summary()}

    def fetcher(url: str) -> SourceFetch:
        return _fetch(url, payloads["summary" if "summary" in url else "board"])

    return fetcher


def _nba_summary() -> dict:
    start = NOW + timedelta(minutes=90)
    last_five = []
    for team_id, opponent_id in (("21", "19"), ("19", "21")):
        last_five.append(
            {
                "team": {"id": team_id},
                "events": [
                    {
                        "id": f"40190000{index}",
                        "gameDate": (start - timedelta(days=2 + index)).isoformat(),
                        "homeTeamId": team_id,
                        "awayTeamId": opponent_id,
                        "homeTeamScore": "110",
                        "awayTeamScore": str(110 - (index + 1)),
                    }
                    for index in range(5)
                ],
            }
        )
    return {
        "header": {
            "competitions": [
                {
                    "id": ESPN_ID,
                    "date": start.isoformat(),
                    "competitors": [
                        {"id": "21", "homeAway": "home"},
                        {"id": "19", "homeAway": "away"},
                    ],
                }
            ]
        },
        "injuries": [
            {
                "team": {"id": "21"},
                "injuries": [
                    {
                        "athlete": {"id": "4277", "displayName": "Jordan Goodwin"},
                        "status": "Day-To-Day",
                        "date": (NOW - timedelta(hours=2)).isoformat(),
                    }
                ],
            }
        ],
        "lastFiveGames": last_five,
    }


def _nhl_landing(flags: tuple[bool, bool] = (False, False)) -> dict:
    def goalies(starting: bool, base: int) -> dict:
        leaders = [
            {
                "playerId": base,
                "name": {"default": "G. Goalie"},
                "gamesPlayed": 10,
                "savePctg": 0.91,
            },
            {"playerId": base + 1, "name": {"default": "B. Backup"}, "gamesPlayed": 4},
        ]
        if starting:
            leaders[0]["starting"] = True
        return {"leaders": leaders}

    return {
        "id": NHL_ID,
        "gameState": "FUT",
        "startTimeUTC": (NOW + timedelta(minutes=120)).isoformat(),
        "homeTeam": {"id": 8, "abbrev": "MTL"},
        "awayTeam": {"id": 68, "abbrev": "UTA"},
        "matchup": {
            "goalieComparison": {
                "homeTeam": goalies(flags[0], 8484000),
                "awayTeam": goalies(flags[1], 8485000),
            }
        },
    }


def _nhl_fetcher(landing: dict):
    return lambda url: _fetch(url, landing)


def _receipt(
    minutes: int,
    *,
    status: str = "CAPTURED_PREGAME_EVIDENCE_INSUFFICIENT",
    confirmed: dict | None = None,
    digest: str | None = None,
) -> dict:
    return {
        "official_event_id": f"espn-{ESPN_ID}",
        "captured_at_utc": (NOW + timedelta(minutes=minutes)).isoformat(),
        "status": status,
        "capture_sha256": digest
        or hashlib.sha256(f"{minutes}:{status}".encode("utf-8")).hexdigest(),
        "confirmed_starter_ids": confirmed or {"home": None, "away": None},
    }


def test_nba_discovery_keeps_only_pregame_events_inside_horizon():
    board = _nba_board(
        [
            (ESPN_ID, 90, "pre", True),
            ("401900002", 20 * 60, "pre", True),
            ("401900003", 90, "in", True),
            (ESPN_ID, 90, "pre", True),
            ("401900004", 90, "pre", False),
        ]
    )
    result = discover_pregame_events("NBA", now=NOW, fetcher=_nba_fetcher(board))

    assert result["status"] == "DISCOVERED"
    assert [event["official_event_id"] for event in result["events"]] == [f"espn-{ESPN_ID}"]
    assert result["events"][0]["start_time_utc"] == (NOW + timedelta(minutes=90)).isoformat()
    assert "CAPTURE_DISCOVERY_EVENT_INVALID" in result["blockers"]
    assert result["starter_inference_allowed"] is False
    assert result["can_execute"] is False


def test_nhl_discovery_keeps_fut_and_pre_but_never_claims_a_starter():
    schedule = _nhl_schedule(
        [
            (NHL_ID, 120, "FUT"),
            ("2026020252", 150, "PRE"),
            ("2026020253", 120, "LIVE"),
            ("2026020254", -30, "FUT"),
        ]
    )
    result = discover_pregame_events("NHL", now=NOW, fetcher=lambda url: _fetch(url, schedule))

    assert [event["official_event_id"] for event in result["events"]] == [
        NHL_ID,
        "2026020252",
    ]
    assert result["starter_inference_allowed"] is False
    assert all(event["state"] in {"FUT", "PRE"} for event in result["events"])
    assert "confirmed" not in json.dumps(result["events"]).lower()


def test_discovery_transport_failure_holds_closed():
    def broken(url: str) -> SourceFetch:
        raise CaptureTransportError("source down")

    result = discover_pregame_events("NBA", now=NOW, fetcher=broken)
    assert result["status"] == "HELD"
    assert result["blockers"] == ["CAPTURE_DISCOVERY_SOURCE_UNAVAILABLE"]
    assert result["can_execute"] is False


def test_plan_applies_window_open_close_freshness_budget_and_attempts():
    events = [
        _event("401900010", 180),
        _event("401900011", 60),
        _event("401900012", 60),
        _event("401900013", 60),
        _event("401900014", 5),
        _event("401900015", -5),
        _event("401900016", 60),
    ]
    receipts = [
        _receipt(-5),  # 5 minutes old -> FRESH
        _receipt(-16),  # 16 minutes old -> DUE
    ]
    receipts[0]["official_event_id"] = "espn-401900013"
    receipts[1]["official_event_id"] = "espn-401900012"

    plan = plan_pregame_capture_cycle(
        "NBA",
        now=NOW,
        events=events,
        receipts=receipts,
        attempts_by_event={"espn-401900016": NBA_WINDOW.max_attempts},
    )
    decisions = {row["official_event_id"]: row["decision"] for row in plan["events"]}

    assert decisions["espn-401900010"] == "NOT_DUE"
    assert decisions["espn-401900011"] == "DUE"
    assert decisions["espn-401900013"] == "FRESH"
    assert decisions["espn-401900012"] == "DUE"
    assert decisions["espn-401900014"] == "WINDOW_CLOSED"
    assert decisions["espn-401900015"] == "EXPIRED"
    assert decisions["espn-401900016"] == "EXHAUSTED"
    assert set(plan["due_event_ids"]) == {"espn-401900011", "espn-401900012"}
    not_due = next(row for row in plan["events"] if row["official_event_id"] == "espn-401900010")
    assert not_due["next_action_at_utc"] == (
        NOW + timedelta(minutes=180 - NBA_WINDOW.open_minutes_before_start)
    ).isoformat()
    fresh = next(row for row in plan["events"] if row["official_event_id"] == "espn-401900013")
    assert fresh["next_action_at_utc"] == (
        NOW - timedelta(minutes=5) + timedelta(minutes=NBA_WINDOW.freshness_minutes)
    ).isoformat()
    assert plan["starter_inference_allowed"] is False
    assert plan["can_execute"] is False


def test_plan_defers_due_events_beyond_the_per_cycle_budget():
    events = [_event(f"40190002{index}", 60) for index in range(4)]
    plan = plan_pregame_capture_cycle("NBA", now=NOW, events=events, max_captures=2)
    assert len(plan["due_event_ids"]) == 2
    assert len(plan["deferred_event_ids"]) == 2
    assert plan["capture_budget"] == 2


def test_nba_cycle_captures_and_keeps_missing_feature_blockers_explicit(tmp_path):
    board = _nba_board([(ESPN_ID, 90, "pre", True)])
    cycle = run_pregame_capture_cycle(
        "NBA",
        now=NOW,
        fetcher=_nba_fetcher(board),
        artifact_root=str(tmp_path),
    )

    assert cycle["status"] == "CYCLE_COMPLETE"
    assert cycle["captures_attempted"] == 1
    capture = cycle["captures"][0]
    assert capture["status"] == "CAPTURED_PREGAME_EVIDENCE_INSUFFICIENT"
    for blocker in (
        "NBA_CONTEXT_ROTATION_MISSING",
        "NBA_CONTEXT_SHOT_QUALITY_MISSING",
        "NBA_CONTEXT_PACE_MISSING",
        "NBA_CONTEXT_SHOT_PROFILE_MISSING",
        "NBA_CONTEXT_TRAVEL_MISSING",
    ):
        assert blocker in capture["blockers"]
    assert capture["capture_verified"] is True
    assert capture["artifact"].endswith(".json")
    assert len(cycle["receipts"]) == 1
    assert cycle["retention"]["retained_count"] == 1
    assert cycle["probability_publishable"] is False
    assert cycle["can_execute"] is False


def test_second_cycle_is_suppressed_by_the_freshness_floor():
    board = _nba_board([(ESPN_ID, 90, "pre", True)])
    first = run_pregame_capture_cycle("NBA", now=NOW, fetcher=_nba_fetcher(board))
    assert first["captures_attempted"] == 1

    second = run_pregame_capture_cycle(
        "NBA",
        now=NOW + timedelta(minutes=5),
        fetcher=_nba_fetcher(board),
        events=[_event(ESPN_ID, 85)],
        receipts=first["receipts"],
    )
    assert second["captures_attempted"] == 0
    assert second["plan"]["due_event_ids"] == []
    assert second["plan"]["events"][0]["decision"] == "FRESH"


def test_nhl_cycle_never_infers_a_confirmed_starter():
    landing = _nhl_landing(flags=(False, False))
    cycle = run_pregame_capture_cycle(
        "NHL",
        now=NOW,
        fetcher=_nhl_fetcher(landing),
        events=[_event(NHL_ID, 120, sport="NHL")],
    )

    assert cycle["captures_attempted"] == 1
    receipt = cycle["receipts"][0]
    assert receipt["status"] == "CAPTURED_GOALIE_CANDIDATES_ONLY"
    assert receipt["confirmed_starter_ids"] == {"home": None, "away": None}
    assert receipt["goalie_evidence_package"] is None
    assert cycle["starter_inference_allowed"] is False
    assert cycle["captures_candidates_only"] == 1
    assert "NHL_CAPTURE_STARTERS_NOT_PUBLISHED" in cycle["blockers"]


def test_nhl_cycle_records_a_source_marked_starter_without_inventing_workloads():
    landing = _nhl_landing(flags=(True, True))
    cycle = run_pregame_capture_cycle(
        "NHL",
        now=NOW,
        fetcher=_nhl_fetcher(landing),
        events=[_event(NHL_ID, 120, sport="NHL")],
    )

    receipt = cycle["receipts"][0]
    assert receipt["status"] == "CAPTURED_GOALIE_EVIDENCE_INCOMPLETE"
    assert receipt["confirmed_starter_ids"] == {"home": "8484000", "away": "8485000"}
    assert receipt["verification"]["status"] == "HELD"
    assert "NHL_GOALIE_HOME_REPLACEMENT_WORKLOAD_INVALID" in receipt["blockers"]
    assert cycle["starter_inference_allowed"] is False


def test_cycle_survives_a_per_event_transport_failure():
    def broken(url: str) -> SourceFetch:
        raise CaptureTransportError("event source down")

    cycle = run_pregame_capture_cycle(
        "NBA",
        now=NOW,
        fetcher=broken,
        events=[_event(ESPN_ID, 90)],
    )
    assert cycle["status"] == "CYCLE_COMPLETE"
    assert cycle["captures"][0]["status"] == "CAPTURED_PREGAME_EVIDENCE_INSUFFICIENT"
    assert cycle["captures"][0]["blockers"] == ["PREGAME_CAPTURE_SOURCE_UNAVAILABLE"]
    # A receipt with no source response cannot be provenance-verified.
    assert cycle["captures"][0]["capture_verified"] is False


def test_retention_keeps_first_latest_and_state_transitions_without_deleting():
    duplicate = _receipt(-40)
    receipts = [
        _receipt(-40),
        duplicate,
        _receipt(-30, status="CAPTURED_GOALIE_CANDIDATES_ONLY"),
        _receipt(-20, status="CAPTURED_GOALIE_CANDIDATES_ONLY"),
        _receipt(-10, status="CAPTURED_GOALIE_STARTERS_CONFIRMED", confirmed={"home": "8484000", "away": None}),
        _receipt(-5, status="CAPTURED_GOALIE_EVIDENCE_INCOMPLETE", confirmed={"home": "8484000", "away": None}),
        _receipt(-1),
    ]
    result = select_retained_receipts(receipts, max_receipts=6)

    retained_times = [item["captured_at_utc"] for item in result["retained"]]
    assert retained_times[0] == (NOW - timedelta(minutes=40)).isoformat()
    assert retained_times[-1] == (NOW - timedelta(minutes=1)).isoformat()
    statuses = {item["status"] for item in result["retained"]}
    assert "CAPTURED_GOALIE_STARTERS_CONFIRMED" in statuses
    assert result["retained_count"] == 6
    assert result["superseded_count"] == 0
    assert result["deletion_performed"] is False
    assert result["deduplicated_sha256"] == [duplicate["capture_sha256"]]


def test_retention_cap_is_deterministic_and_preserves_superseded_receipts():
    receipts = [_receipt(-minute) for minute in range(40, 0, -3)]
    first = select_retained_receipts(receipts, max_receipts=4)
    second = select_retained_receipts(receipts, max_receipts=4)

    assert first["retained"] == second["retained"]
    assert first["retained_count"] == 4
    assert first["superseded_count"] == len(receipts) - 4
    assert first["retained"][0] is not None
    assert first["can_execute"] is False


def test_schedule_rejects_unknown_sport_and_naive_clock():
    with pytest.raises(ValueError):
        window_for("MLB")
    with pytest.raises(ValueError):
        plan_pregame_capture_cycle("NBA", now=datetime(2026, 11, 5, 17, 0), events=[])
    assert NHL_WINDOW.open_minutes_before_start == 180
    assert NBA_WINDOW.close_minutes_before_start == 10
