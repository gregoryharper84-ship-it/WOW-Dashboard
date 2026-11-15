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
    # Unauthenticated inactives report listing the player
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


def test_regression_4576456234_undated_espn_out_does_not_produce_decisive_out():
    # ESPN entry with OUT but no date
    undated_espn = {
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
