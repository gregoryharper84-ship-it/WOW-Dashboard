from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from v17.pregame_source_capture import (
    CAPTURE_SCHEMA,
    CaptureTransportError,
    SourceFetch,
    capture_nba_pregame,
    capture_nhl_goalie_evidence,
    verify_capture_receipt,
    write_capture_artifact,
)

NOW = datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=5)
ESPN_ID = "401909936"
NHL_ID = "2026020251"


def _iso(value: datetime) -> str:
    return value.isoformat()


def _fetch(url: str, payload) -> SourceFetch:
    body = json.dumps(payload).encode("utf-8")
    return SourceFetch(
        url=url,
        status=200,
        received_at_utc=NOW,
        body=body,
        http_date="Thu, 05 Nov 2026 17:00:00 GMT",
        content_type="application/json;charset=utf-8",
    )


def _espn_board(state: str = "pre", start: datetime = START) -> dict:
    return {
        "events": [
            {
                "id": ESPN_ID,
                "date": _iso(start),
                "status": {"type": {"state": state}},
                "competitions": [
                    {
                        "id": ESPN_ID,
                        "date": _iso(start),
                        "venue": {"fullName": "Footprint Center"},
                        "status": {"type": {"state": state}},
                        "competitors": [
                            {"homeAway": "home", "team": {"id": "21", "abbreviation": "PHX", "displayName": "Suns"}},
                            {"homeAway": "away", "team": {"id": "19", "abbreviation": "ORL", "displayName": "Magic"}},
                        ],
                    }
                ],
            }
        ]
    }


def _last_five(team_id: str, margins: list[int], opponent_id: str) -> dict:
    events = []
    for index, margin in enumerate(margins):
        own, other = 110, 110 - margin
        events.append(
            {
                "id": f"40190000{index}",
                "gameDate": _iso(START - timedelta(days=2 + index)),
                "homeTeamId": team_id,
                "awayTeamId": opponent_id,
                "homeTeamScore": str(own),
                "awayTeamScore": str(other),
            }
        )
    return {"team": {"id": team_id}, "events": events}


def _espn_summary(start: datetime = START) -> dict:
    return {
        "header": {
            "competitions": [
                {
                    "id": ESPN_ID,
                    "date": _iso(start),
                    "competitors": [
                        {"id": "21", "homeAway": "home", "team": {"id": "21"}},
                        {"id": "19", "homeAway": "away", "team": {"id": "19"}},
                    ],
                }
            ]
        },
        "injuries": [
            {
                "team": {"id": "21", "abbreviation": "PHX"},
                "injuries": [
                    {
                        "athlete": {"id": "4277", "displayName": "Jordan Goodwin"},
                        "status": "Day-To-Day",
                        "date": _iso(NOW - timedelta(hours=2)),
                    }
                ],
            },
            {
                "team": {"id": "19", "abbreviation": "ORL"},
                "injuries": [
                    {
                        "athlete": {"id": "4433", "displayName": "Jonathan Isaac"},
                        "status": "Out",
                        "date": _iso(NOW - timedelta(hours=3)),
                    }
                ],
            },
        ],
        "lastFiveGames": [
            _last_five("21", [9, -3, 4, 12, -7], "19"),
            _last_five("19", [2, 5, -6, 1, 8], "21"),
        ],
    }


def _nba_fetcher(board=None, summary=None):
    payloads = {
        "scoreboard": board if board is not None else _espn_board(),
        "summary": summary if summary is not None else _espn_summary(),
    }

    def fetcher(url: str) -> SourceFetch:
        return _fetch(url, payloads["summary" if "summary" in url else "scoreboard"])

    return fetcher


def _nhl_landing(
    *,
    state: str = "FUT",
    start: datetime = START,
    home_flags: tuple[bool, ...] = (False, False),
    away_flags: tuple[bool, ...] = (False, False),
    event_id: str = NHL_ID,
) -> dict:
    def goalies(flags: tuple[bool, ...], base: int) -> dict:
        leaders = []
        for index, flag in enumerate(flags):
            entry = {
                "playerId": base + index,
                "name": {"default": f"G. Goalie{index}"},
                "gamesPlayed": 10 + index,
                "savePctg": 0.91 - index * 0.01,
                "gaa": 2.5,
                "record": "5-3-1",
            }
            if flag:
                entry["starting"] = True
            leaders.append(entry)
        return {"leaders": leaders, "teamTotals": {"record": "9-3-3"}}

    return {
        "id": event_id,
        "gameState": state,
        "startTimeUTC": _iso(start),
        "season": 20262027,
        "homeTeam": {"id": 8, "abbrev": "MTL"},
        "awayTeam": {"id": 68, "abbrev": "UTA"},
        "venue": {"default": "Bell Centre"},
        "matchup": {"goalieComparison": {"homeTeam": goalies(home_flags, 8484000), "awayTeam": goalies(away_flags, 8485000)}},
    }


def _nhl_schedule(start: datetime = START) -> dict:
    return {
        "gameWeek": [
            {
                "date": "2026-11-05",
                "games": [
                    {
                        "id": NHL_ID,
                        "startTimeUTC": _iso(start),
                        "homeTeam": {"abbrev": "MTL"},
                        "awayTeam": {"abbrev": "UTA"},
                    }
                ],
            }
        ]
    }


def _nhl_fetcher(landing=None, schedule=None):
    def fetcher(url: str) -> SourceFetch:
        if "club-schedule-season" in url:
            return _fetch(url, schedule if schedule is not None else _nhl_schedule())
        return _fetch(url, landing if landing is not None else _nhl_landing())

    return fetcher


def test_nba_pregame_capture_freezes_real_source_evidence_and_holds_on_gaps():
    receipt = capture_nba_pregame(ESPN_ID, now=NOW, fetcher=_nba_fetcher())

    assert receipt["schema"] == CAPTURE_SCHEMA
    assert receipt["official_event_id"] == f"espn-{ESPN_ID}"
    assert receipt["event"]["state"] == "PRE"
    assert receipt["event"]["home"]["abbreviation"] == "PHX"
    assert receipt["event"]["away"]["abbreviation"] == "ORL"
    assert receipt["evidence_package"]["recent_margins"] == {
        "home": [9.0, -3.0, 4.0, 12.0, -7.0],
        "away": [2.0, 5.0, -6.0, 1.0, 8.0],
    }
    assert receipt["rest_days"] == {"home": 1, "away": 1}
    assert receipt["injury_report"]["21"][0]["status"] == "Day-To-Day"
    assert receipt["status"] == "CAPTURED_PREGAME_EVIDENCE_INSUFFICIENT"
    for blocker in (
        "NBA_CONTEXT_ROTATION_MISSING",
        "NBA_CONTEXT_SHOT_QUALITY_MISSING",
        "NBA_CONTEXT_PACE_MISSING",
        "NBA_CONTEXT_SHOT_PROFILE_MISSING",
        "NBA_CONTEXT_TRAVEL_MISSING",
    ):
        assert blocker in receipt["blockers"]
    assert receipt["derivation"]["features_available"] is False
    assert len(receipt["source_responses"]) == 2
    assert all(len(item["payload_sha256"]) == 64 for item in receipt["source_responses"])
    assert receipt["research_only"] is True
    assert receipt["fitted_model"] is False
    assert receipt["probability_publishable"] is False
    assert receipt["rank_eligible"] is False
    assert receipt["can_execute"] is False
    assert len(receipt["capture_sha256"]) == 64

    verified = verify_capture_receipt(receipt)
    assert verified["status"] == "VERIFIED_RESEARCH_CAPTURE"
    assert verified["blockers"] == []


def test_nba_capture_artifact_is_immutable_and_deterministic(tmp_path):
    receipt = capture_nba_pregame(ESPN_ID, now=NOW, fetcher=_nba_fetcher())
    first = write_capture_artifact(receipt, tmp_path)
    second = write_capture_artifact(receipt, tmp_path)
    assert first == second
    assert first.parent.name == "nba"
    assert first.read_text(encoding="utf-8") == json.dumps(
        receipt, sort_keys=True, separators=(",", ":")
    ) + "\n"
    assert verify_capture_receipt(json.loads(first.read_text()))["status"] == (
        "VERIFIED_RESEARCH_CAPTURE"
    )


def test_nba_capture_rejects_non_pregame_past_or_unknown_events():
    result = capture_nba_pregame(
        ESPN_ID, now=NOW, fetcher=_nba_fetcher(board=_espn_board(state="in"))
    )
    assert result["status"] == "CAPTURED_PREGAME_EVIDENCE_INSUFFICIENT"
    assert "NBA_CAPTURE_EVENT_NOT_PREGAME" in result["blockers"]

    result = capture_nba_pregame(
        ESPN_ID, now=NOW, fetcher=_nba_fetcher(board=_espn_board(start=NOW - timedelta(minutes=1)))
    )
    assert "NBA_CAPTURE_KICKOFF_NOT_FUTURE" in result["blockers"]

    result = capture_nba_pregame("401900001", now=NOW, fetcher=_nba_fetcher())
    assert "NBA_CAPTURE_EVENT_NOT_FOUND" in result["blockers"]


def test_nba_capture_rejects_source_identity_and_injury_time_defects():
    summary = _espn_summary(start=START + timedelta(hours=1))
    result = capture_nba_pregame(ESPN_ID, now=NOW, fetcher=_nba_fetcher(summary=summary))
    assert "NBA_CAPTURE_SOURCE_IDENTITY_MISMATCH" in result["blockers"]

    summary = _espn_summary()
    summary["injuries"][0]["injuries"][0]["date"] = "2026-11-05T15:00:00"
    result = capture_nba_pregame(ESPN_ID, now=NOW, fetcher=_nba_fetcher(summary=summary))
    assert "NBA_CAPTURE_INJURY_TIMESTAMP_INVALID" in result["blockers"]

    summary = _espn_summary()
    summary["injuries"][0]["injuries"][0]["date"] = _iso(NOW + timedelta(minutes=5))
    result = capture_nba_pregame(ESPN_ID, now=NOW, fetcher=_nba_fetcher(summary=summary))
    assert "NBA_CAPTURE_INJURY_TIMESTAMP_IN_FUTURE" in result["blockers"]


def test_nba_capture_rejects_bad_argument_and_transport_failure():
    with pytest.raises(ValueError):
        capture_nba_pregame("espn-401909936", now=NOW, fetcher=_nba_fetcher())
    with pytest.raises(ValueError):
        capture_nba_pregame(ESPN_ID, now=datetime(2026, 11, 5, 17, 0), fetcher=_nba_fetcher())

    def broken(url: str) -> SourceFetch:
        raise CaptureTransportError("transport down")

    result = capture_nba_pregame(ESPN_ID, now=NOW, fetcher=broken)
    assert result["status"] == "CAPTURED_PREGAME_EVIDENCE_INSUFFICIENT"
    assert result["blockers"] == ["PREGAME_CAPTURE_SOURCE_UNAVAILABLE"]
    assert result["can_execute"] is False


def test_nhl_capture_freezes_published_candidates_without_claiming_a_starter():
    receipt = capture_nhl_goalie_evidence(NHL_ID, now=NOW, fetcher=_nhl_fetcher())

    assert receipt["official_event_id"] == NHL_ID
    assert receipt["event"]["home_team"] == "MTL"
    assert receipt["event"]["away_team"] == "UTA"
    assert receipt["status"] == "CAPTURED_GOALIE_CANDIDATES_ONLY"
    assert receipt["blockers"] == ["NHL_CAPTURE_STARTERS_NOT_PUBLISHED"]
    assert receipt["confirmed_starter_ids"] == {"home": None, "away": None}
    assert [item["goalie_id"] for item in receipt["goalie_candidates"]["home"]] == [
        "8484000",
        "8484001",
    ]
    assert receipt["goalie_candidates"]["away"][0]["save_pctg"] == pytest.approx(0.91)
    assert receipt["goalie_evidence_package"] is None
    assert receipt["verification"] is None
    assert receipt["can_execute"] is False
    assert verify_capture_receipt(receipt)["status"] == "VERIFIED_RESEARCH_CAPTURE"


def test_nhl_capture_records_explicit_starters_but_never_invents_workloads():
    landing = _nhl_landing(home_flags=(True, False), away_flags=(False, True))
    receipt = capture_nhl_goalie_evidence(
        NHL_ID, now=NOW, fetcher=_nhl_fetcher(landing=landing)
    )

    assert receipt["status"] == "CAPTURED_GOALIE_EVIDENCE_INCOMPLETE"
    assert receipt["confirmed_starter_ids"] == {"home": "8484000", "away": "8485001"}
    assert receipt["goalie_evidence_package"]["confirmation"]["starters"]["home"]["status"] == "CONFIRMED"
    assert receipt["identity_receipt"]["identity_source"].startswith(
        "https://api-web.nhle.com/v1/club-schedule-season/MTL/"
    )
    assert receipt["verification"]["status"] == "HELD"
    assert "NHL_GOALIE_HOME_REPLACEMENT_WORKLOAD_INVALID" in receipt["blockers"]
    assert "NHL_GOALIE_AWAY_REPLACEMENT_WORKLOAD_INVALID" in receipt["blockers"]
    assert len(receipt["source_responses"]) == 2
    assert verify_capture_receipt(receipt)["status"] == "VERIFIED_RESEARCH_CAPTURE"


def test_nhl_capture_rejects_non_pregame_identity_and_ambiguous_starters():
    receipt = capture_nhl_goalie_evidence(
        NHL_ID, now=NOW, fetcher=_nhl_fetcher(landing=_nhl_landing(state="LIVE"))
    )
    assert "NHL_CAPTURE_EVENT_NOT_PREGAME" in receipt["blockers"]

    receipt = capture_nhl_goalie_evidence(
        NHL_ID,
        now=NOW,
        fetcher=_nhl_fetcher(landing=_nhl_landing(start=NOW - timedelta(minutes=1))),
    )
    assert "NHL_CAPTURE_KICKOFF_NOT_FUTURE" in receipt["blockers"]

    receipt = capture_nhl_goalie_evidence(
        NHL_ID, now=NOW, fetcher=_nhl_fetcher(landing=_nhl_landing(event_id="2026020252"))
    )
    assert "NHL_CAPTURE_EVENT_IDENTITY_MISMATCH" in receipt["blockers"]

    receipt = capture_nhl_goalie_evidence(
        NHL_ID,
        now=NOW,
        fetcher=_nhl_fetcher(landing=_nhl_landing(home_flags=(True, True), away_flags=(True, False))),
    )
    assert "NHL_CAPTURE_MULTIPLE_CONFIRMED_GOALIES" in receipt["blockers"]
    assert receipt["status"] == "CAPTURED_GOALIE_EVIDENCE_INCOMPLETE"


def test_nhl_capture_flags_schedule_identity_mismatch_and_transport_failure():
    schedule = _nhl_schedule(start=START + timedelta(hours=2))
    receipt = capture_nhl_goalie_evidence(
        NHL_ID,
        now=NOW,
        fetcher=_nhl_fetcher(landing=_nhl_landing(home_flags=(True, False), away_flags=(True, False)), schedule=schedule),
    )
    assert "NHL_CAPTURE_IDENTITY_MISMATCH" in receipt["blockers"]
    assert receipt["identity_receipt"] is None
    assert "NHL_GOALIE_CANONICAL_IDENTITY_RECEIPT_MISSING" in receipt["blockers"]

    def broken(url: str) -> SourceFetch:
        raise CaptureTransportError("transport down")

    result = capture_nhl_goalie_evidence(NHL_ID, now=NOW, fetcher=broken)
    assert result["status"] == "CAPTURED_GOALIE_CANDIDATES_ONLY"
    assert result["blockers"] == ["PREGAME_CAPTURE_SOURCE_UNAVAILABLE"]


def test_capture_receipt_rejects_tampering_and_authority_claims():
    receipt = capture_nba_pregame(ESPN_ID, now=NOW, fetcher=_nba_fetcher())

    tampered = json.loads(json.dumps(receipt))
    tampered["evidence_package"]["recent_margins"]["home"][0] = 99.0
    result = verify_capture_receipt(tampered)
    assert result["status"] == "HELD"
    assert "PREGAME_CAPTURE_RECEIPT_DIGEST_MISMATCH" in result["blockers"]

    claimed = json.loads(json.dumps(receipt))
    claimed["can_execute"] = True
    result = verify_capture_receipt(claimed)
    assert "PREGAME_CAPTURE_AUTHORITY_CLAIMED" in result["blockers"]

    assert verify_capture_receipt(None)["blockers"] == ["PREGAME_CAPTURE_RECEIPT_MISSING"]
    with pytest.raises(ValueError):
        write_capture_artifact(claimed, "/tmp/wow-capture-should-not-write")
