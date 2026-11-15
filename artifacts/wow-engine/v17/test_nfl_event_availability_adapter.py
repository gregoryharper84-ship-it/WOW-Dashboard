"""Comprehensive contract and negative regression tests for the NFL event availability adapter.

Tests all permitted interpretations and fail-closed invariants established by
governed WOW V17 direction:
1. Complete, authenticated pregame official report (clears unlisted, applies designations).
2. Official game-day inactive report (rules inactive players out, does not clear unlisted).
3. Dated ESPN per-game injury entries (records stated designations, does not clear unlisted).
4. Undated ESPN athlete profile (fails closed, insufficient for live clearance).
5. nflverse injury archive (retrospective/historical only).
6. Multi-source composite resolution order.
7. Integration with evaluate_player_availability gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from v17.nfl_event_availability_adapter import (
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    SOURCE_ESPN_ATHLETE_PROFILE,
    SOURCE_ESPN_EVENT_INJURIES,
    SOURCE_NFL_OFFICIAL_GAME_STATUS,
    SOURCE_NFL_OFFICIAL_INACTIVES,
    compose_nfl_event_availability,
    normalize_nfl_designation,
    parse_espn_event_summary_injuries,
    parse_nfl_gameday_inactives,
    parse_nfl_official_game_status_report,
)
from v17.player_availability_gate import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNAVAILABLE,
    AVAILABILITY_UNVERIFIED,
    PLAYER_AVAILABILITY_UNVERIFIED,
    PLAYER_RULED_OUT,
    evaluate_player_availability,
)

CAPTURED = datetime(2026, 11, 15, 16, 0, 0, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 11, 15, 18, 0, 0, tzinfo=timezone.utc)


def test_invariants_preserved():
    assert CAN_EXECUTE is False
    assert PROBABILITY_PUBLISHABLE is False


def test_designation_normalization():
    assert normalize_nfl_designation("Out") == "OUT"
    assert normalize_nfl_designation("O") == "OUT"
    assert normalize_nfl_designation("Ruled Out") == "OUT"
    assert normalize_nfl_designation("Inactive") == "INACTIVE"
    assert normalize_nfl_designation("Injured Reserve") == "IR"
    assert normalize_nfl_designation("Questionable") == "QUESTIONABLE"
    assert normalize_nfl_designation("Q") == "QUESTIONABLE"
    assert normalize_nfl_designation("Doubtful") == "DOUBTFUL"
    assert normalize_nfl_designation("Cleared") == "AVAILABLE"
    assert normalize_nfl_designation("Full Participation") == "AVAILABLE"
    assert normalize_nfl_designation(None) is None


# ---------------------------------------------------------------------------
# 1. Complete authenticated pregame official report
# ---------------------------------------------------------------------------
def test_official_report_unlisted_player_is_cleared():
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=2)).isoformat(),
        "entries": [
            {"athlete_id": "999", "athlete_name": "Other Player", "team": "DET", "game_status": "Out"},
        ],
    }
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.source == SOURCE_NFL_OFFICIAL_GAME_STATUS
    assert ev.fresh is True
    assert ev.report_complete is True
    assert ev.basis == "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert ev.designation is None

    # Gate evaluation
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_AVAILABLE
    assert dec.blocker is None
    assert dec.risk is False


def test_official_report_listed_out_rules_player_out():
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=2)).isoformat(),
        "entries": [
            {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown", "team": "DET", "game_status": "Out"},
        ],
    }
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.designation == "OUT"
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNAVAILABLE
    assert dec.blocker == PLAYER_RULED_OUT


def test_official_report_listed_questionable_marks_available_with_risk():
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=2)).isoformat(),
        "entries": [
            {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown", "team": "DET", "game_status": "Questionable"},
        ],
    }
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.designation == "QUESTIONABLE"
    assert ev.risk is True
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_AVAILABLE
    assert dec.blocker is None
    assert dec.risk is True


def test_incomplete_official_report_unlisted_player_fails_closed():
    report = {
        "authenticated": True,
        "report_complete": False,  # INCOMPLETE!
        "report_timestamp": (CAPTURED - timedelta(hours=2)).isoformat(),
        "entries": [],
    }
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.report_complete is False
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNVERIFIED
    assert dec.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_unauthenticated_official_report_unlisted_player_fails_closed():
    report = {
        "authenticated": False,  # UNAUTHENTICATED
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=2)).isoformat(),
        "entries": [],
    }
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.report_complete is False
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNVERIFIED
    assert dec.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_stale_official_report_fails_closed():
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(days=4)).isoformat(),  # 4 days old!
        "entries": [],
    }
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.fresh is False
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNVERIFIED
    assert dec.blocker == PLAYER_AVAILABILITY_UNVERIFIED


# ---------------------------------------------------------------------------
# 2. Official game-day inactive report
# ---------------------------------------------------------------------------
def test_gameday_inactives_rules_listed_player_out():
    inactives = {
        "authenticated": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
        "inactives": [
            {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown", "team": "DET"},
            "Cade Mays",
        ],
    }
    ev = parse_nfl_gameday_inactives(
        inactives,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert ev.source == SOURCE_NFL_OFFICIAL_INACTIVES
    assert ev.is_inactive is True
    assert ev.designation == "INACTIVE"
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNAVAILABLE
    assert dec.blocker == PLAYER_RULED_OUT


def test_gameday_inactives_unlisted_player_is_not_cleared_by_inactives_alone():
    inactives = {
        "authenticated": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
        "inactives": [
            {"athlete_id": "999", "athlete_name": "Other Player", "team": "DET"},
        ],
    }
    ev = parse_nfl_gameday_inactives(
        inactives,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert ev.is_inactive is False
    assert ev.report_complete is False
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNVERIFIED
    assert dec.blocker == PLAYER_AVAILABILITY_UNVERIFIED


# ---------------------------------------------------------------------------
# 3. Dated ESPN per-game injury feed
# ---------------------------------------------------------------------------
def test_espn_event_summary_stated_out_rules_player_out():
    summary = {
        "injuries": [
            {
                "team": {"abbreviation": "DET"},
                "injuries": [
                    {
                        "athlete": {"id": "4361788", "displayName": "Amon-Ra St. Brown"},
                        "status": "Out",
                        "date": "2026-11-14T20:00Z",
                    }
                ],
            }
        ]
    }
    ev = parse_espn_event_summary_injuries(
        summary,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert ev.source == SOURCE_ESPN_EVENT_INJURIES
    assert ev.designation == "OUT"
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNAVAILABLE
    assert dec.blocker == PLAYER_RULED_OUT


def test_espn_event_summary_unlisted_player_does_not_clear():
    summary = {
        "injuries": [
            {
                "team": {"abbreviation": "DET"},
                "injuries": [
                    {
                        "athlete": {"id": "999", "displayName": "Other Player"},
                        "status": "Questionable",
                        "date": "2026-11-14T20:00Z",
                    }
                ],
            }
        ]
    }
    ev = parse_espn_event_summary_injuries(
        summary,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert ev.report_complete is False
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNVERIFIED
    assert dec.blocker == PLAYER_AVAILABILITY_UNVERIFIED


# ---------------------------------------------------------------------------
# 4. Composite multi-source composition
# ---------------------------------------------------------------------------
def test_composite_inactives_preempts_pregame_clearance():
    # Even if pregame report previously showed unlisted/cleared,
    # gameday inactives rules the player OUT!
    official = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=4)).isoformat(),
        "entries": [],
    }
    inactives = {
        "authenticated": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
        "inactives": [
            {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown", "team": "DET"},
        ],
    }
    ev = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        official_game_status=official,
        gameday_inactives=inactives,
    )
    assert ev.is_inactive is True
    assert ev.designation == "INACTIVE"
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNAVAILABLE
    assert dec.blocker == PLAYER_RULED_OUT


def test_composite_complete_official_report_clears_active_player():
    official = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "entries": [],
    }
    inactives = {
        "authenticated": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
        "inactives": ["Other Player"],
    }
    ev = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        official_game_status=official,
        gameday_inactives=inactives,
    )
    assert ev.source == SOURCE_NFL_OFFICIAL_GAME_STATUS
    assert ev.basis == "FRESH_OFFICIAL_REPORT_UNLISTED"
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_AVAILABLE
    assert dec.blocker is None


def test_custom_governed_freshness_threshold():
    # 24-hour report is stale under default 2h threshold, but fresh when
    # caller passes an authorized 24-hour window.
    official = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=20)).isoformat(),
        "entries": [],
    }
    default_ev = parse_nfl_official_game_status_report(
        official,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert default_ev.fresh is False

    custom_ev = parse_nfl_official_game_status_report(
        official,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        max_report_age_seconds=24 * 3600,
    )
    assert custom_ev.fresh is True


def test_composite_undated_profile_and_archive_alone_fail_closed():
    ev = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        espn_athlete_profile={"athlete": {"id": "4361788", "injuries": []}},
        nflverse_row={"player": "Amon-Ra St. Brown", "week": 10},
    )
    assert ev.report_complete is False
    assert ev.fresh is False
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNVERIFIED
    assert dec.blocker == PLAYER_AVAILABILITY_UNVERIFIED


# ---------------------------------------------------------------------------
# 5. Robustness, name matching, team aliases, and malformed inputs
# ---------------------------------------------------------------------------
def test_name_matching_handles_accents_and_case():
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "entries": [
            {"athlete_id": "", "athlete_name": "Amon-Rá St. Brown", "team": "Detroit Lions", "game_status": "Questionable"},
        ],
    }
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.designation == "QUESTIONABLE"
    assert ev.risk is True


def test_malformed_entries_and_payloads_fail_closed_safely():
    # Null / empty payloads
    ev = parse_nfl_official_game_status_report(
        {},
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.report_complete is False
    assert evaluate_player_availability(ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED

    # Inactives with corrupted entry
    inactives_ev = parse_nfl_gameday_inactives(
        {"inactives": [None, 12345, {}]},
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert inactives_ev.is_inactive is False
    assert evaluate_player_availability(inactives_ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED


def test_shadow_mode_constant():
    from v17.nfl_event_availability_adapter import ADAPTER_MODE
    assert ADAPTER_MODE == "SHADOW_ONLY"
