"""Regression tests for read-only NFL probability quality audit (#1939)."""
from datetime import datetime, timedelta, timezone

import pytest

from v17.nfl_accuracy_audit import (
    CAN_EXECUTE, audit_anytime_td, audit_moneyline, audit_nfl,
)

START = datetime(2026, 10, 18, 17, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 21, 20, tzinfo=timezone.utc)


def ml_row(ident="r1", minutes=-60, p=.65):
    return {
        "event_prediction_id": ident, "official_event_id": "game1",
        "event_start_time_utc": START.isoformat(),
        "created_at": (START + timedelta(minutes=minutes)).isoformat(),
        "model_timestamp": (START - timedelta(hours=2)).isoformat(),
        "home_team": "HOME", "away_team": "AWAY", "selected_participant": "HOME",
        "calibrated_selection_probability": p,
        "calibrated_selection_lower_bound": min(.55, p),
        "calibrated_selection_upper_bound": max(.75, p),
        "model_package_valid": True, "provenance_complete": True,
        "model_probability_publishable": True, "can_execute": False,
    }


def game(home=31, away=17, home_win=True, tie=False):
    return {"game_id": "game1", "home_score": home, "away_score": away,
            "home_win": home_win, "tie": tie}


def grade(ident="r1", p=.65, outcome=1):
    return {"official_event_id": "game1", "event_prediction_id": ident,
            "selected_participant": "HOME", "calibrated_probability": p,
            "outcome": outcome}


def td_row(ident="t1", direction="MORE", minutes=-40, p=.4):
    return {
        "prediction_id": ident, "event_id": "game1",
        "created_at": (START + timedelta(minutes=minutes)).isoformat(),
        "model_timestamp": (START - timedelta(hours=2)).isoformat(),
        "event_start_time": START.isoformat(), "player": "Player A",
        "sport": "NFL", "stat_type": "ANYTIME_TD", "line": .5,
        "direction": direction, "calibrated_probability": p,
        "can_execute": False,
    }


def test_moneyline_latest_pregame_snapshot_and_no_leakage():
    rows = [ml_row("older", -120, .62), ml_row("winner", -30, .7),
            ml_row("after_kickoff", 2, .99)]
    actual = audit_moneyline(rows, [game()], [grade("winner", .7)], as_of=NOW)
    assert actual["canonical_pregame_events"] == 1
    assert actual["duplicate_snapshots"] == 1
    assert actual["excluded_snapshots"] == {"NOT_PREGAME": 1}
    assert actual["metrics_for_reconciled_events_only"]["n"] == 1
    assert actual["metrics_for_reconciled_events_only"]["brier"] == pytest.approx(.09)


def test_ml_immutable_identity_mismatch_not_counted():
    report = audit_moneyline([ml_row()], [game()], [grade("wrong")], as_of=NOW)
    assert report["reconciliation_issues"] == {"IMMUTABLE_GRADE_MISMATCH": 1}
    assert report["metrics_for_reconciled_events_only"]["n"] == 0


def test_ml_settlement_orientation_conflict_not_counted():
    report = audit_moneyline([ml_row()], [game(home_win=False)], [grade()], as_of=NOW)
    assert report["reconciliation_issues"] == {"HOME_WIN_SCORE_CONFLICT": 1}


def test_ml_expected_tie_and_future_do_not_create_fake_incidents():
    tie = audit_moneyline([ml_row()], [game(17, 17, False, True)],
                          [grade()], as_of=NOW)
    assert tie["expected_ungraded"] == {"TIE_EXCLUDED": 1}
    future = audit_moneyline([ml_row()], [], [],
                             as_of=START-timedelta(days=1))
    assert future["expected_ungraded"] == {"PREGAME_PENDING": 1}
    assert not future["reconciliation_issues"]


def test_moneyline_execution_authority_is_not_accepted():
    unsafe = ml_row()
    unsafe["can_execute"] = True
    report = audit_moneyline([unsafe], [game()], [grade()], as_of=NOW)
    assert report["excluded_snapshots"] == {"CAN_EXECUTE_TRUE": 1}


def test_td_two_sides_are_one_underlying_market():
    report = audit_anytime_td([td_row("a", "MORE"), td_row("b", "LESS")],
                              [], as_of=NOW)
    assert report["directional_theses"] == 2
    assert report["underlying_player_markets"] == 1
    assert report["paired_more_less_markets"] == 1
    assert report["settlement_dispositions"] == {"UNSETTLED_AFTER_KICKOFF": 2}
    assert report["independent_graded_markets"]["n"] == 0


def test_td_grades_do_not_double_count_opposite_directions():
    preds = [td_row("a", "MORE", -40, .4),
             td_row("b", "LESS", -20, .6)]
    settlements = [{"prediction_id": "a", "hit": False, "void": False, "push": False},
                   {"prediction_id": "b", "hit": True, "void": False, "push": False}]
    report = audit_anytime_td(preds, settlements, as_of=NOW)
    assert report["settled_directional_theses"] == 2
    assert report["independent_graded_markets"]["n"] == 1
    assert report["independent_graded_markets"]["brier"] == pytest.approx(.16)


def test_td_future_is_pending_not_a_missing_settlement_incident():
    report = audit_anytime_td([td_row()], [], as_of=START-timedelta(days=1))
    assert report["settlement_dispositions"] == {"PREGAME_PENDING": 1}


def test_read_only_governed_contract_invariant():
    assert CAN_EXECUTE is False
    result = audit_nfl([], [], [], [], [], as_of=NOW)
    assert result["can_execute"] is False
    assert result["read_only"] is True
