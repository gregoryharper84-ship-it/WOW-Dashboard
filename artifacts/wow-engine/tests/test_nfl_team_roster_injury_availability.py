"""Regression tests for NFL team-roster official injury availability and Daily deployed_sha."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import nfl_prop_auto_hydration as nfl
from v17.player_availability_gate import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNAVAILABLE,
    AVAILABILITY_UNVERIFIED,
    PLAYER_AVAILABILITY_UNVERIFIED,
    PLAYER_RULED_OUT,
    evaluate_player_availability,
)


CAPTURED = datetime(2026, 11, 22, 1, 0, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
FRESH_INJ_DATE = "2026-11-21T18:54Z"
STALE_INJ_DATE = "2026-11-15T18:00Z"


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP_{self.status_code}")

    def json(self) -> dict:
        return self._payload


def _complete_roster_payload(
    *,
    team_abbr: str = "BUF",
    offense_items: list[dict] | None = None,
    defense_items: list[dict] | None = None,
    ir_items: list[dict] | None = None,
    ps_items: list[dict] | None = None,
    include_ir_group: bool = True,
) -> dict:
    groups: list[dict] = [
        {
            "position": "offense",
            "items": offense_items
            if offense_items is not None
            else [
                {
                    "id": "3918298",
                    "displayName": "Josh Allen",
                    "status": {"type": "active"},
                    "injuries": [],
                },
                {
                    "id": "4573699",
                    "displayName": "Jackson Hawes",
                    "status": {"type": "active"},
                    "injuries": [{"status": "Questionable", "date": FRESH_INJ_DATE}],
                },
                {
                    "id": "4385690",
                    "displayName": "Dalton Kincaid",
                    "status": {"type": "active"},
                    "injuries": [{"status": "Doubtful", "date": FRESH_INJ_DATE}],
                },
            ],
        },
        {
            "position": "defense",
            "items": defense_items
            if defense_items is not None
            else [
                {
                    "id": "4379778",
                    "displayName": "Christian Benford",
                    "status": {"type": "active"},
                    "injuries": [{"status": "Out", "date": FRESH_INJ_DATE}],
                },
            ],
        },
        {"position": "specialTeam", "items": []},
    ]
    if include_ir_group:
        groups.append(
            {
                "position": "injuredReserveOrOut",
                "items": ir_items
                if ir_items is not None
                else [
                    {
                        "id": "4242433",
                        "displayName": "Joshua Palmer",
                        "status": {"type": "day-to-day"},
                        "injuries": [{"status": "Injured Reserve", "date": "2026-11-09T22:24Z"}],
                    }
                ],
            }
        )
    groups.append(
        {
            "position": "practiceSquad",
            "items": ps_items
            if ps_items is not None
            else [
                {
                    "id": "9990001",
                    "displayName": "Practice Player",
                    "status": {"type": "practice-squad"},
                    "injuries": [],
                }
            ],
        }
    )
    return {
        "timestamp": "2026-11-22T01:00:00Z",
        "team": {"id": "2", "abbreviation": team_abbr},
        "athletes": groups,
    }


def test_healthy_active_player_clears_with_fresh_complete_team_roster_report():
    payload = _complete_roster_payload()
    evidence = nfl._team_roster_injury_availability(
        athlete_id="3918298",
        team="BUF",
        event_id="401873070",
        official_start=KICKOFF,
        captured=CAPTURED,
        http_get=lambda *a, **k: _Resp(payload),
    )
    assert evidence["basis"] == "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert evidence["fresh"] is True
    assert evidence["report_complete"] is True
    assert evidence["source"] == nfl.TEAM_ROSTER_INJURY_SOURCE
    assert evidence["captured_at"] == "2026-11-21T18:54:00+00:00"
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.blocker is None
    assert decision.risk is False
    assert decision.can_execute is False


def test_questionable_player_clears_with_risk_designation():
    payload = _complete_roster_payload()
    evidence = nfl._team_roster_injury_availability(
        athlete_id="4573699",
        team="BUF",
        event_id="401873070",
        official_start=KICKOFF,
        captured=CAPTURED,
        http_get=lambda *a, **k: _Resp(payload),
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.designation == "QUESTIONABLE"
    assert decision.risk is True
    assert decision.blocker is None


@pytest.mark.parametrize(
    "athlete_id,expected_designation",
    [
        ("4379778", "OUT"),
        ("4242433", "INJURED_RESERVE"),
        ("9990001", "INACTIVE"),
    ],
)
def test_out_ir_and_practice_squad_players_are_ruled_out(athlete_id: str, expected_designation: str):
    payload = _complete_roster_payload()
    evidence = nfl._team_roster_injury_availability(
        athlete_id=athlete_id,
        team="BUF",
        event_id="401873070",
        official_start=KICKOFF,
        captured=CAPTURED,
        http_get=lambda *a, **k: _Resp(payload),
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNAVAILABLE
    assert decision.blocker == PLAYER_RULED_OUT
    assert decision.designation == expected_designation


def test_stale_team_injury_report_fails_closed_as_unverified():
    payload = _complete_roster_payload(
        offense_items=[
            {"id": "3918298", "displayName": "Josh Allen", "status": {"type": "active"}, "injuries": []},
        ],
        defense_items=[
            {
                "id": "4379778",
                "displayName": "Christian Benford",
                "status": {"type": "active"},
                "injuries": [{"status": "Out", "date": STALE_INJ_DATE}],
            },
        ],
    )
    evidence = nfl._team_roster_injury_availability(
        athlete_id="3918298",
        team="BUF",
        event_id="401873070",
        official_start=KICKOFF,
        captured=CAPTURED,
        http_get=lambda *a, **k: _Resp(payload),
    )
    assert evidence["fresh"] is False
    assert evidence["report_complete"] is False
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_incomplete_roster_groups_fail_closed_as_unverified():
    payload = _complete_roster_payload(include_ir_group=False)
    evidence = nfl._team_roster_injury_availability(
        athlete_id="3918298",
        team="BUF",
        event_id="401873070",
        official_start=KICKOFF,
        captured=CAPTURED,
        http_get=lambda *a, **k: _Resp(payload),
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_team_identity_mismatch_fails_closed_as_unverified():
    payload = _complete_roster_payload(team_abbr="KC")
    evidence = nfl._team_roster_injury_availability(
        athlete_id="3918298",
        team="BUF",
        event_id="401873070",
        official_start=KICKOFF,
        captured=CAPTURED,
        http_get=lambda *a, **k: _Resp(payload),
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_retrieval_failure_fails_closed_as_unverified():
    def explode(*args, **kwargs):
        raise RuntimeError("ESPN_503")

    evidence = nfl._team_roster_injury_availability(
        athlete_id="3918298",
        team="BUF",
        event_id="401873070",
        official_start=KICKOFF,
        captured=CAPTURED,
        http_get=explode,
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_async_poll_exposes_deployed_sha(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from v17.daily_async_runtime import install_daily_async_routes

    sha = "0" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", sha)

    class _FakeExec:
        def __init__(self, data):
            self.data = data

    class _FakeTable:
        def __init__(self, rows):
            self._rows = rows

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def execute(self):
            return _FakeExec(self._rows)

    class _FakeDB:
        def table(self, _name):
            return _FakeTable(
                [
                    {
                        "run_id": "v17-daily-async-test",
                        "run_status": "COMPLETED",
                        "submitted_at": "2026-11-22T01:00:00Z",
                        "started_at": "2026-11-22T01:00:01Z",
                        "completed_at": "2026-11-22T01:00:05Z",
                        "attempt_count": 1,
                        "result_run_id": "v17-daily-inner",
                        "result_payload": {"run_id": "v17-daily-inner", "deployed_sha": sha},
                        "last_error_code": None,
                        "can_execute": False,
                    }
                ]
            )

    app = FastAPI()
    install_daily_async_routes(
        app,
        auth_callable=lambda: None,
        db_client_fn=lambda: _FakeDB(),
        market_api=object(),
        event_api=object(),
    )
    client = TestClient(app)
    resp = client.get("/v17/daily-snapshot-run/v17-daily-async-test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deployed_sha"] == sha
    assert body["result"]["deployed_sha"] == sha
    assert body["can_execute"] is False
