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
    _parse_iso_ts,
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
        "event_id": "espn-401873053",
        "sport": "NFL",
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
        "event_id": "espn-401873053",
        "sport": "NFL",
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
        "report_timestamp": (CAPTURED - timedelta(minutes=60)).isoformat(),
        "team": "DET",
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
        "event_id": "espn-401873053",
        "team": "DET",
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


# ---------------------------------------------------------------------------
# Regressions for CodeRabbit / Incident #2131 Findings
# ---------------------------------------------------------------------------
def test_regression_4576456208_boolean_attestations_string_false_fails_closed():
    # String "false" must NOT satisfy authenticated or report_complete
    report = {
        "authenticated": "false",
        "report_complete": "false",
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "entries": [],
        "team": "DET",
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
    assert ev.basis != "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert evaluate_player_availability(ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED


def test_regression_4576456212_post_kickoff_official_report_fails_closed():
    # Report published after kickoff cannot grant pregame clearance
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (KICKOFF + timedelta(minutes=15)).isoformat(),
        "entries": [],
        "team": "DET",
    }
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=KICKOFF + timedelta(minutes=20),
    )
    assert ev.fresh is False
    assert ev.basis != "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert evaluate_player_availability(ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED


def test_regression_4576456219_malformed_entries_collection_fails_closed():
    # entries=[None] or missing entries field must invalidate completeness
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "entries": [None],
        "team": "DET",
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
    assert ev.basis != "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert evaluate_player_availability(ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED

    # Missing entries/injuries field completely
    report_no_entries = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "team": "DET",
    }
    ev2 = parse_nfl_official_game_status_report(
        report_no_entries,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev2.report_complete is False
    assert ev2.basis != "FRESH_OFFICIAL_REPORT_UNLISTED"


def test_regression_4576456221_conflicting_athlete_id_same_name_does_not_match():
    # Official report: entry has same name but different non-empty athlete_id
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "team": "DET",
        "entries": [
            {"athlete_id": "9999999", "athlete_name": "Amon-Ra St. Brown", "team": "DET", "game_status": "Out"},
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
    # Target 4361788 does NOT match 9999999, so target is unlisted (cleared)
    assert ev.designation != "OUT"
    assert ev.basis == "FRESH_OFFICIAL_REPORT_UNLISTED"

    # Inactives: entry has same name but conflicting athlete_id
    inact = parse_nfl_gameday_inactives(
        {
            "authenticated": True,
            "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
            "inactives": [
                {"athlete_id": "9999999", "athlete_name": "Amon-Ra St. Brown", "team": "DET"},
            ],
        },
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert inact.is_inactive is False

    # ESPN event summary: entry has same name but conflicting athlete_id
    espn_ev = parse_espn_event_summary_injuries(
        {
            "event_id": "espn-401873053",
            "sport": "NFL",
            "injuries": [
                {
                    "team": {"abbreviation": "DET"},
                    "injuries": [
                        {
                            "athlete": {"id": "9999999", "displayName": "Amon-Ra St. Brown"},
                            "status": "Out",
                            "date": (CAPTURED - timedelta(hours=1)).isoformat(),
                        }
                    ],
                }
            ]
        },
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert espn_ev.designation != "OUT"


def test_regression_4576456225_out_of_scope_report_does_not_clear_player():
    # Report explicitly scoped to MIA must NOT clear unlisted DET player
    report_mia = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "team": "MIA",
        "entries": [],
    }
    ev = parse_nfl_official_game_status_report(
        report_mia,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.basis != "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert ev.report_complete is False

    # Report scoped to different event must NOT clear
    report_diff_event = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "event_id": "espn-FOREIGN-EVENT",
        "entries": [],
        "team": "DET",
    }
    ev2 = parse_nfl_official_game_status_report(
        report_diff_event,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev2.basis != "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert ev2.report_complete is False


def test_regression_4576456229_nested_espn_malformed_objects_fail_closed_safely():
    # None team or None athlete does not raise AttributeError
    malformed = {
        "event_id": "espn-401873053",
        "sport": "NFL",
        "injuries": [
            {"team": None, "injuries": [{"athlete": None, "status": "Out"}]},
            {"team": {"abbreviation": "DET"}, "injuries": [None, "string_entry", {"athlete": None}]},
        ]
    }
    ev = parse_espn_event_summary_injuries(
        malformed,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert ev.designation is None
    assert ev.evidence_status == "SUPPORTING_FEED_CLEARANCE_NOT_PROVEN"


def test_regression_4576456233_unauthenticated_or_stale_inactives_does_not_preempt_fresh_clearance():
    # Fresh complete official clearance
    official = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=60)).isoformat(),
        "team": "DET",
        "entries": [],
    }
    # Case 1: Unauthenticated inactives report listing the player
    unauth_inactives = {
        "authenticated": False,
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
        gameday_inactives=unauth_inactives,
    )
    # Unauthenticated inactive must NOT preempt official clearance!
    assert ev.basis == "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert ev.is_inactive is False
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_AVAILABLE

    # Case 2: Stale inactives report (older than 2h max) listing the player
    stale_inactives = {
        "authenticated": True,
        "report_timestamp": (CAPTURED - timedelta(hours=3)).isoformat(),
        "inactives": [
            {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown", "team": "DET"},
        ],
    }
    ev_stale = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        official_game_status=official,
        gameday_inactives=stale_inactives,
    )
    # Stale inactive must NOT preempt fresh official clearance!
    assert ev_stale.basis == "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert ev_stale.is_inactive is False
    dec_stale = evaluate_player_availability(ev_stale.to_role_availability())
    assert dec_stale.status == AVAILABILITY_AVAILABLE


def test_regression_conflicting_official_entries_for_same_athlete_fails_closed():
    # Report has two entries for athlete 4361788 with conflicting designations
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "team": "DET",
        "entries": [
            {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown", "team": "DET", "game_status": "Questionable"},
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
    assert ev.designation is None
    assert ev.report_complete is False
    assert ev.evidence_status == "REPORT_ENTRY_CONFLICT_UNVERIFIED"
    assert evaluate_player_availability(ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED


def test_regression_foreign_event_listed_athlete_not_authoritative():
    # Foreign event report with an entry for athlete 4361788
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "event_id": "espn-FOREIGN-EVENT",
        "team": "DET",
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
    assert ev.report_complete is False
    assert ev.evidence_status == "REPORT_SCOPE_MISMATCH"

    # In compose, out-of-scope report is NOT authoritative
    comp_ev = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        official_game_status=report,
    )
    assert comp_ev.source == "NFL_COMPOSITE_AVAILABILITY_FALLBACK"


@pytest.mark.parametrize(
    "scope_overrides",
    [
        {"event_id": "espn-FOREIGN-EVENT"},
        {"sport": "NBA"},
        {"team": "MIA"},
    ],
)
def test_regression_out_of_scope_official_out_cannot_rule_player_out(scope_overrides):
    # A foreign-event, foreign-sport, or foreign-team OUT must not become decisive.
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "entries": [
            {
                "athlete_id": "4361788",
                "athlete_name": "Amon-Ra St. Brown",
                "team": "DET",
                "game_status": "Out",
            },
        ],
    }
    report.update(scope_overrides)
    ev = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.evidence_status == "REPORT_SCOPE_MISMATCH"
    assert ev.designation is None
    assert ev.risk is False
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_regression_foreign_event_official_conflict_does_not_suppress_current_event_espn():
    # A conflicting report for another event is not authoritative for this event.
    foreign_conflicting_official = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=30)).isoformat(),
        "event_id": "espn-FOREIGN-EVENT",
        "team": "DET",
        "entries": [
            {
                "athlete_id": "4361788",
                "athlete_name": "Amon-Ra St. Brown",
                "team": "DET",
                "game_status": "Questionable",
            },
            {
                "athlete_id": "4361788",
                "athlete_name": "Amon-Ra St. Brown",
                "team": "DET",
                "game_status": "Out",
            },
        ],
    }
    # Source identity must be explicit; the parser must not relabel a summary
    # as the caller's event when event/sport metadata is omitted.
    current_event_espn = {
        "event_id": "espn-401873053",
        "sport": "NFL",
        "injuries": [
            {
                "team": {"abbreviation": "DET"},
                "injuries": [
                    {
                        "athlete": {"id": "4361788", "displayName": "Amon-Ra St. Brown"},
                        "status": "Out",
                        "date": (CAPTURED - timedelta(minutes=15)).isoformat(),
                    }
                ],
            }
        ]
    }
    comp_ev = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        official_game_status=foreign_conflicting_official,
        espn_event_summary=current_event_espn,
    )
    assert comp_ev.source == SOURCE_ESPN_EVENT_INJURIES
    assert comp_ev.designation == "OUT"
    assert comp_ev.fresh is True
    decision = evaluate_player_availability(comp_ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNAVAILABLE
    assert decision.blocker == PLAYER_RULED_OUT


@pytest.mark.parametrize(
    "scope_overrides",
    [
        {"event_id": "espn-FOREIGN-EVENT"},
        {"sport": "NBA"},
        {"team": "MIA"},
    ],
)
def test_regression_foreign_scope_gameday_inactives_cannot_preempt_current_event_clearance(scope_overrides):
    foreign_inactives = {
        "authenticated": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=30)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "inactives": ["Amon-Ra St. Brown"],
    }
    foreign_inactives.update(scope_overrides)
    ev = parse_nfl_gameday_inactives(
        foreign_inactives,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.evidence_status == "REPORT_SCOPE_MISMATCH"
    assert ev.is_inactive is False
    assert ev.designation is None
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED

    # Invalid-scope inactives must not preempt current-event official clearance.
    current_official = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "entries": [],
    }
    composed = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        official_game_status=current_official,
        gameday_inactives=foreign_inactives,
    )
    assert composed.source == SOURCE_NFL_OFFICIAL_GAME_STATUS
    assert composed.basis == "FRESH_OFFICIAL_REPORT_UNLISTED"
    assert evaluate_player_availability(composed.to_role_availability()).status == AVAILABILITY_AVAILABLE


def test_regression_in_scope_gameday_inactives_string_inherits_report_team_scope():
    in_scope = {
        "authenticated": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=30)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "inactives": ["Amon-Ra St. Brown"],
    }
    ev = parse_nfl_gameday_inactives(
        in_scope,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.evidence_status == "RETRIEVED"
    assert ev.is_inactive is True
    assert ev.designation == "INACTIVE"
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNAVAILABLE
    assert decision.blocker == PLAYER_RULED_OUT


@pytest.mark.parametrize(
    "scope_metadata",
    [
        {},
        {"event_id": "espn-401873053", "sport": "NFL"},
        {"event_id": "espn-401873053", "sport": "NFL", "teams": ["DET", "MIA"]},
    ],
)
@pytest.mark.parametrize(
    "target_entry",
    [
        "Amon-Ra St. Brown",
        {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown"},
    ],
)
def test_regression_untagged_inactive_entry_cannot_inherit_sibling_mapping_team(scope_metadata, target_entry):
    # A different tagged entry must not lend its team scope to this untagged player.
    report = {
        "authenticated": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=30)).isoformat(),
        "inactives": [
            {"athlete_id": "999", "athlete_name": "Other Player", "team": "DET"},
            target_entry,
        ],
    }
    report.update(scope_metadata)
    ev = parse_nfl_gameday_inactives(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.is_inactive is False
    assert ev.designation is None
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_regression_contradictory_report_team_and_teams_metadata_fails_closed():
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=1)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "teams": ["MIA"],
        "entries": [
            {
                "athlete_id": "4361788",
                "athlete_name": "Amon-Ra St. Brown",
                "team": "DET",
                "game_status": "Out",
            },
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
    assert ev.evidence_status == "REPORT_SCOPE_MISMATCH"
    assert ev.designation is None
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_regression_naive_datetime_is_normalized_as_utc(monkeypatch):
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("tzset is unavailable on this platform")
    naive = datetime(2026, 11, 15, 15, 0, 0)
    try:
        with monkeypatch.context() as scoped:
            scoped.setenv("TZ", "EST5")
            time.tzset()
            assert _parse_iso_ts(naive) == naive.replace(tzinfo=timezone.utc)
    finally:
        time.tzset()


def test_regression_unidentifiable_official_entry_blocks_unlisted_clearance():
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "entries": [{"game_status": "Out"}],
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
    assert ev.basis is None
    assert ev.designation is None
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_regression_untagged_official_target_entry_cannot_borrow_sibling_team_scope():
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "entries": [
            {
                "athlete_id": "999",
                "athlete_name": "Other Player",
                "team": "DET",
                "game_status": "Questionable",
            },
            {
                "athlete_id": "4361788",
                "athlete_name": "Amon-Ra St. Brown",
                "game_status": "Available",
            },
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
    assert ev.report_complete is False
    assert ev.basis is None
    assert ev.designation is None
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_regression_untagged_name_only_official_entry_cannot_decide():
    """Event identity alone cannot attribute a name-only status to this team."""
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=45)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "entries": [
            {"athlete_name": "Amon-Ra St. Brown", "game_status": "Available"},
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
    assert ev.designation is None
    assert ev.basis is None
    assert ev.report_complete is False
    dec = evaluate_player_availability(ev.to_role_availability())
    assert dec.status == AVAILABILITY_UNVERIFIED
    assert dec.blocker == PLAYER_AVAILABILITY_UNVERIFIED


@pytest.mark.parametrize(
    "authenticated,report_timestamp",
    [
        (False, CAPTURED - timedelta(minutes=30)),
        (True, CAPTURED - timedelta(days=1)),
    ],
)
def test_regression_official_listed_out_requires_authentication_and_freshness(authenticated, report_timestamp):
    report = {
        "authenticated": authenticated,
        "report_complete": True,
        "report_timestamp": report_timestamp.isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "entries": [
            {
                "athlete_id": "4361788",
                "athlete_name": "Amon-Ra St. Brown",
                "team": "DET",
                "game_status": "Out",
            }
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
    assert ev.designation is None
    assert ev.risk is False
    assert ev.report_complete is False
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


@pytest.mark.parametrize(
    "authenticated,report_timestamp",
    [
        (False, CAPTURED - timedelta(minutes=30)),
        (True, CAPTURED - timedelta(days=1)),
    ],
)
def test_regression_gameday_inactive_requires_authentication_and_freshness(authenticated, report_timestamp):
    report = {
        "authenticated": authenticated,
        "report_timestamp": report_timestamp.isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "inactives": [
            {
                "athlete_id": "4361788",
                "athlete_name": "Amon-Ra St. Brown",
                "team": "DET",
            }
        ],
    }
    ev = parse_nfl_gameday_inactives(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert ev.is_inactive is False
    assert ev.designation is None
    assert ev.report_complete is False
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


@pytest.mark.parametrize(
    "summary_overrides",
    [
        {"event_id": "espn-FOREIGN-EVENT", "sport": "NFL"},
        {"event_id": "espn-401873053", "sport": "NBA"},
        {"event_id": None, "sport": "NFL"},
        {"event_id": "espn-401873053", "sport": None},
    ],
)
def test_regression_espn_out_requires_matching_event_and_nfl_sport(summary_overrides):
    summary = {
        "event_id": "espn-401873053",
        "sport": "NFL",
        "injuries": [
            {
                "team": {"abbreviation": "DET"},
                "injuries": [
                    {
                        "athlete": {"id": "4361788", "displayName": "Amon-Ra St. Brown"},
                        "status": "Out",
                        "date": (CAPTURED - timedelta(minutes=30)).isoformat(),
                    }
                ],
            }
        ],
    }
    summary.update(summary_overrides)
    ev = parse_espn_event_summary_injuries(
        summary,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert ev.designation is None
    assert ev.evidence_status == "REPORT_SCOPE_MISMATCH"
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_regression_espn_empty_team_metadata_cannot_emit_out():
    summary = {
        "event_id": "espn-401873053",
        "sport": "NFL",
        "injuries": [
            {
                "team": {"abbreviation": ""},
                "injuries": [
                    {
                        "athlete": {"id": "4361788", "displayName": "Amon-Ra St. Brown"},
                        "status": "Out",
                        "date": (CAPTURED - timedelta(minutes=30)).isoformat(),
                    }
                ],
            }
        ],
    }
    ev = parse_espn_event_summary_injuries(
        summary,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert ev.designation is None
    assert ev.evidence_status == "SUPPORTING_FEED_CLEARANCE_NOT_PROVEN"
    decision = evaluate_player_availability(ev.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_regression_espn_post_kickoff_or_stale_entry_not_decisive():
    # ESPN entry published after kickoff
    post_kickoff_espn = {
        "event_id": "espn-401873053",
        "sport": "NFL",
        "injuries": [
            {
                "team": {"abbreviation": "DET"},
                "injuries": [
                    {
                        "athlete": {"id": "4361788", "displayName": "Amon-Ra St. Brown"},
                        "status": "Out",
                        "date": (KICKOFF + timedelta(minutes=15)).isoformat(),
                    }
                ],
            }
        ]
    }
    ev = parse_espn_event_summary_injuries(
        post_kickoff_espn,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=KICKOFF + timedelta(minutes=20),
    )
    assert ev.fresh is False
    assert ev.designation is None
    assert evaluate_player_availability(ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED


def test_regression_4576456234_undated_espn_out_does_not_produce_decisive_out():
    # ESPN entry with OUT but no date
    undated_espn = {
        "event_id": "espn-401873053",
        "sport": "NFL",
        "injuries": [
            {
                "team": {"abbreviation": "DET"},
                "injuries": [
                    {
                        "athlete": {"id": "4361788", "displayName": "Amon-Ra St. Brown"},
                        "status": "Out",
                        # No date!
                    }
                ],
            }
        ]
    }
    ev = parse_espn_event_summary_injuries(
        undated_espn,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        captured=CAPTURED,
    )
    assert ev.designation is None
    assert ev.fresh is False
    assert evaluate_player_availability(ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED

    # In compose, undated ESPN must not produce OUT
    comp_ev = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        espn_event_summary=undated_espn,
    )
    assert comp_ev.designation is None
    assert evaluate_player_availability(comp_ev.to_role_availability()).status == AVAILABILITY_UNVERIFIED


def test_regression_official_report_age_parameter_respected_in_compose():
    # Official report is 3 hours old (older than default 2h max_official_report_age_seconds = 7200)
    # Default max_espn_report_age_seconds is 24h (86400).
    stale_official = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(hours=3)).isoformat(),
        "team": "DET",
        "event_id": "espn-401873053",
        "entries": [],
    }
    comp_ev = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        official_game_status=stale_official,
    )
    assert comp_ev.fresh is False
    assert comp_ev.basis != "FRESH_OFFICIAL_REPORT_UNLISTED"
    role_av = comp_ev.to_role_availability()
    assert evaluate_player_availability(role_av).status == AVAILABILITY_UNVERIFIED


def test_regression_official_conflict_not_overridden_by_espn_out():
    # Official report contains duplicate conflicting entries for same athlete (Questionable vs Out)
    conflicting_official = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=30)).isoformat(),
        "team": "DET",
        "event_id": "espn-401873053",
        "entries": [
            {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown", "game_status": "Questionable"},
            {"athlete_id": "4361788", "athlete_name": "Amon-Ra St. Brown", "game_status": "Out"},
        ],
    }
    # ESPN has a fresh dated Out entry
    fresh_espn = {
        "injuries": [
            {
                "team": {"abbreviation": "DET"},
                "injuries": [
                    {
                        "athlete": {"id": "4361788", "displayName": "Amon-Ra St. Brown"},
                        "status": "Out",
                        "date": (CAPTURED - timedelta(minutes=15)).isoformat(),
                    }
                ],
            }
        ]
    }
    comp_ev = compose_nfl_event_availability(
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
        official_game_status=conflicting_official,
        espn_event_summary=fresh_espn,
    )
    # Must preserve the detected official-report conflict: fail closed to UNVERIFIED, NOT UNAVAILABLE!
    assert comp_ev.evidence_status == "REPORT_ENTRY_CONFLICT_UNVERIFIED"
    role_av = comp_ev.to_role_availability()
    assert evaluate_player_availability(role_av).status == AVAILABILITY_UNVERIFIED

# Exact adverse-parser cases from independent review on PR #2669.
# These tests preserve the existing fail-closed repair; shadow-only, no publish.
@pytest.mark.parametrize("uninterpretable_entry", [
    {},
    {"team": "DET", "game_status": "Out"},
])
def test_adversarial_complete_report_with_uninterpretable_entry_cannot_clear_player(uninterpretable_entry):
    report = {
        "authenticated": True,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=30)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "entries": [uninterpretable_entry],
    }
    evidence = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert evidence.report_complete is False
    assert evidence.basis is None
    assert evidence.designation is None
    decision = evaluate_player_availability(evidence.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


@pytest.mark.parametrize("authenticated,age_minutes,designation", [
    (False, 30, "Out"),
    (True, 1440, "Injured Reserve"),
])
def test_adversarial_direct_official_parser_rejects_untrusted_or_stale_ruleout(
    authenticated, age_minutes, designation
):
    report = {
        "authenticated": authenticated,
        "report_complete": True,
        "report_timestamp": (CAPTURED - timedelta(minutes=age_minutes)).isoformat(),
        "event_id": "espn-401873053",
        "sport": "NFL",
        "team": "DET",
        "entries": [{
            "athlete_id": "4361788",
            "athlete_name": "Amon-Ra St. Brown",
            "team": "DET",
            "game_status": designation,
        }],
    }
    evidence = parse_nfl_official_game_status_report(
        report,
        athlete_id="4361788",
        athlete_name="Amon-Ra St. Brown",
        team="DET",
        event_id="espn-401873053",
        event_start=KICKOFF,
        captured=CAPTURED,
    )
    assert evidence.designation is None
    assert evidence.risk is False
    assert evidence.report_complete is False
    decision = evaluate_player_availability(evidence.to_role_availability())
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED
