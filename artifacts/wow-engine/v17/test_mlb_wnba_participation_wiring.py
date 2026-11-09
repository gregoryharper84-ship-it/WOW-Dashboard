"""MLB and WNBA hydration must supply official pregame participation evidence.

Three evidence dimensions are kept separate on purpose:

* ``designation`` — an injury/availability designation (``OUT``, ``DOUBTFUL``…);
* ``basis`` — the official participation fact that named the player for the game
  (probable starter, posted batting order, fresh report with no listing);
* provenance — source, athlete, event, team, capture time, freshness, report
  completeness.

These tests exercise the real hydration entry points with their network seams
replaced, then assert the emitted ``role_status`` clears (or fails to clear) the
same gate the request boundary uses.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import prop_auto_hydration as mlb
import wnba_prop_auto_hydration as wnba
from v17.player_availability_gate import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNVERIFIED,
    PLAYER_RULED_OUT,
    evaluate_player_availability,
    participation_evidence,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

EVENT_START = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
NOW = datetime.now(timezone.utc)

_SCHEDULE = {
    "official_game_pk": 999,
    "official_game_date": EVENT_START,
    "side": "HOME",
    "team": "HOM",
    "team_id": 1,
    "opponent": "AWY",
    "opponent_team_id": 2,
    "venue": "Test Park",
    "schedule_status": "Scheduled",
    "starter_status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE",
}


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _schedule_payload(probable_id: int) -> dict:
    return {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 999,
                        "gameDate": EVENT_START,
                        "status": {"detailedState": "Scheduled"},
                        "venue": {"name": "Test Park"},
                        "teams": {
                            "home": {
                                "team": {"id": 1, "abbreviation": "HOM"},
                                "probablePitcher": {"id": probable_id, "fullName": "Test Pitcher"},
                            },
                            "away": {"team": {"id": 2, "abbreviation": "AWY"}},
                        },
                    }
                ]
            }
        ]
    }


# ---------------------------------------------------------------------------
# MLB — probable starter is a role basis, never an injury clearance
# ---------------------------------------------------------------------------
def test_mlb_probable_starter_row_records_role_basis_and_provenance(monkeypatch):
    monkeypatch.setattr(mlb, "_resolve_player_id", lambda *a, **k: (123, "Test Pitcher"))
    monkeypatch.setattr(mlb, "_schedule_context", lambda *a, **k: dict(_SCHEDULE))
    monkeypatch.setattr(mlb, "_game_log", lambda *a, **k: ([5.0] * 10, [{"season": 2026}] * 10))
    monkeypatch.setattr(mlb, "_hydrate_opponent_context", lambda *a, **k: (None, "NOT_REQUESTED"))

    payload = mlb.auto_hydrate_prop_evidence(
        sport="MLB",
        player="Test Pitcher",
        stat_type="PITCHER_STRIKEOUTS",
        event_start_time=EVENT_START,
        now=NOW,
    )

    evidence = payload["role_status"]["availability"]
    assert evidence["basis"] == "PROBABLE_STARTER"
    assert evidence["confirmation"] == "PROBABLE"
    assert "designation" not in evidence  # role is not an injury designation
    assert evidence["fresh"] is True
    assert evidence["athlete_id"] == "123"
    assert evidence["event_id"] == "999"
    assert evidence["team"] == "HOM"

    decision = evaluate_player_availability(payload["role_status"])
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.basis == "PROBABLE_STARTER"
    assert decision.confirmation == "PROBABLE"
    assert decision.designation is None
    # Provisional: the final pregame refresh must recheck the same source.
    assert decision.risk is True


def test_mlb_workload_row_also_carries_role_basis(monkeypatch):
    splits = [
        (
            2026,
            {
                "date": (NOW - timedelta(days=7 * index + 3)).date().isoformat(),
                "stat": {
                    "gamesStarted": 1,
                    "inningsPitched": "6.0",
                    "strikeOuts": 6,
                    "numberOfPitches": 95,
                    "strikes": 61,
                },
                "opponent": {"abbreviation": "AWY"},
            },
        )
        for index in range(10)
    ]
    monkeypatch.setattr(mlb, "_resolve_player_id", lambda *a, **k: (123, "Test Pitcher"))
    monkeypatch.setattr(mlb, "_schedule_context", lambda *a, **k: dict(_SCHEDULE))
    monkeypatch.setattr(
        "prop_auto_hydration_workload.fetch_cross_season_pitching_splits",
        lambda *a, **k: (splits, [2026]),
    )

    payload = mlb.auto_hydrate_prop_evidence(
        sport="MLB",
        player="Test Pitcher",
        stat_type="PITCHING_OUTS",
        event_start_time=EVENT_START,
        now=NOW,
    )
    role = payload["role_status"]
    assert role["availability"]["basis"] == "PROBABLE_STARTER"
    assert evaluate_player_availability(role).status == AVAILABILITY_AVAILABLE


def test_late_scratch_removes_the_starter_before_any_probability(monkeypatch):
    """A player no longer named probable must fail hydration, not score."""
    monkeypatch.setattr(mlb, "_resolve_player_id", lambda *a, **k: (123, "Test Pitcher"))
    with pytest.raises(mlb.PropAutoHydrationError) as excinfo:
        mlb._schedule_context(
            123,
            event_start=datetime.fromisoformat(EVENT_START),
            http_get=lambda *a, **k: _Response(_schedule_payload(777)),
        )
    assert excinfo.value.code == "MLB_STARTER_STATUS_UNRESOLVED"


def test_posted_batting_order_is_confirmed_not_provisional():
    evidence = participation_evidence(
        basis="POSTED_BATTING_ORDER",
        confirmation="CONFIRMED",
        source="MLB_STATS_API_OFFICIAL_BATTING_ORDER",
        captured_at=NOW.isoformat(),
        fresh=True,
        athlete_id=1,
        event_id=2,
        team="HOM",
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.risk is False
    assert decision.confirmation == "CONFIRMED"


def test_unposted_batting_order_is_unverified():
    evidence = participation_evidence(
        basis=None,
        source="MLB_STATS_API_OFFICIAL_BATTING_ORDER",
        captured_at=NOW.isoformat(),
        evidence_status="OFFICIAL_BATTING_ORDER_NOT_POSTED",
        fresh=True,
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_EVIDENCE_OFFICIAL_BATTING_ORDER_NOT_POSTED"


# ---------------------------------------------------------------------------
# WNBA — fresh official report, unlisted means no reported designation
# ---------------------------------------------------------------------------
def _wnba_payload(monkeypatch, availability_report: dict) -> dict:
    game = {
        "gameId": "1022600200",
        "gameDateTimeUTC": EVENT_START,
        "gameStatusText": "Scheduled",
        "homeTeam": {"teamId": 1, "teamCity": "Las", "teamName": "Vegas", "teamTricode": "LVA"},
        "awayTeam": {"teamId": 2, "teamCity": "Ind", "teamName": "Indiana", "teamTricode": "IND"},
    }
    monkeypatch.setattr(wnba, "_schedule", lambda *a, **k: game)
    monkeypatch.setattr(
        wnba,
        "_resolve_player_and_team",
        lambda *a, **k: {
            "roster": {"PLAYER": "Test Player", "PLAYER_ID": "456", "POSITION": "G"},
            "team": game["homeTeam"],
            "opponent": game["awayTeam"],
        },
    )
    monkeypatch.setattr(
        wnba,
        "_player_game_log",
        lambda *a, **k: (
            [12.0] * 10,
            [
                {
                    "date": "2026-08-01",
                    "game_id": "1",
                    "team": "LVA",
                    "matchup": "LVA@IND",
                    "minutes": 30.0,
                }
            ]
            * 10,
        ),
    )
    monkeypatch.setattr(
        wnba,
        "_latest_injury_report",
        lambda *a, **k: ("https://example.test/report.pdf", NOW, "Injury Report"),
    )
    monkeypatch.setattr(wnba, "_availability_from_report", lambda *a, **k: dict(availability_report))
    return wnba.hydrate_wnba_prop_evidence(
        player="Test Player",
        stat_type="POINTS",
        event_start_time=EVENT_START,
        now=NOW,
    )


def test_wnba_unlisted_player_records_report_basis_and_completeness(monkeypatch):
    payload = _wnba_payload(
        monkeypatch,
        {
            "availability": "NOT_LISTED_ON_FRESH_OFFICIAL_INJURY_REPORT",
            "designation": None,
            "injury_reason": None,
        },
    )
    evidence = payload["role_status"]["availability"]
    assert evidence["basis"] == "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert evidence["report_complete"] is True
    assert evidence["fresh"] is True
    assert evidence["athlete_id"] == "456"
    assert evidence["event_id"] == "1022600200"
    decision = evaluate_player_availability(payload["role_status"])
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.designation is None
    # Unlisted on a complete report clears the reported-injury dimension only;
    # minutes and role remain separate modelling concerns.
    assert decision.risk is False


def test_wnba_explicitly_available_player_is_cleared(monkeypatch):
    payload = _wnba_payload(
        monkeypatch,
        {"availability": "AVAILABLE", "designation": "AVAILABLE", "injury_reason": None},
    )
    decision = evaluate_player_availability(payload["role_status"])
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.designation == "AVAILABLE"


def test_wnba_unsubmitted_report_never_clears():
    text = "Injury Report LVA@IND Las Vegas Aces not yet submitted"
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        wnba._availability_from_report(
            text,
            player_name="Test Player",
            team_name="Las Vegas Aces",
            opponent_name="Indiana Fever",
            matchup="LVA@IND",
            game_date="08/01/2026",
        )
    assert excinfo.value.code == "WNBA_INJURY_REPORT_NOT_SUBMITTED"


# ---------------------------------------------------------------------------
# The canonical builder cannot silently manufacture clearance
# ---------------------------------------------------------------------------
def test_builder_without_designation_or_basis_is_unverified():
    evidence = participation_evidence(source="TEST", captured_at=NOW.isoformat(), fresh=True)
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_DESIGNATION_MISSING"


def test_builder_reports_a_failed_retrieval_as_unverified():
    evidence = participation_evidence(
        designation="AVAILABLE",
        source="TEST",
        captured_at=NOW.isoformat(),
        evidence_status="RETRIEVAL_FAILED",
    )
    assert evaluate_player_availability({"availability": evidence}).status == AVAILABILITY_UNVERIFIED


def test_stale_evidence_is_unverified_even_with_a_playable_basis():
    evidence = participation_evidence(
        basis="PROBABLE_STARTER",
        source="TEST",
        captured_at=NOW.isoformat(),
        fresh=False,
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_EVIDENCE_STALE"


def test_incomplete_report_is_unverified_even_when_unlisted():
    evidence = participation_evidence(
        basis="FRESH_OFFICIAL_REPORT_UNLISTED",
        source="TEST",
        captured_at=NOW.isoformat(),
        fresh=True,
        report_complete=False,
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_REPORT_INCOMPLETE"


def test_explicit_out_designation_beats_a_playable_basis():
    evidence = participation_evidence(
        designation="OUT",
        basis="PROBABLE_STARTER",
        source="TEST",
        captured_at=NOW.isoformat(),
        fresh=True,
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status != AVAILABILITY_AVAILABLE
    assert decision.blocker == PLAYER_RULED_OUT
    assert decision.designation == "OUT"


def test_doubtful_remains_playable_but_flagged():
    evidence = participation_evidence(
        designation="DOUBTFUL",
        source="TEST",
        captured_at=NOW.isoformat(),
        fresh=True,
        athlete_id="1",
        event_id="2",
        team="TEST",
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.risk is True
