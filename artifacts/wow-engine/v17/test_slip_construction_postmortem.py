"""Slip-builder retrospective is independent of fitted prediction postmortem.

All named events/participants in this test are synthetic and no entry was
submitted or pregame model-probability receipt was created.
"""
from __future__ import annotations

import copy

import pytest

from v17.postmortem_engine import (
    PostmortemLegInput,
    PostmortemPositionInput,
    PostmortemRunInput,
    build_postmortem_payload,
)
from v17.slip_construction_postmortem import build_slip_construction_postmortem


def _leg(row, position, event, participant, result, *, market="MONEYLINE", line=None):
    return PostmortemLegInput(
        row_key=row,
        position_reference=position,
        sport="NBA",
        event_id=event,
        participant=participant,
        market=market,
        side="MORE" if line is not None else "WIN",
        selection=(
            f"{participant} MORE {line} {market}" if line is not None
            else f"{participant} wins"
        ),
        exact_line=line,
        actual_stat=None if line is None else (10 if result == "WIN" else 1),
        official_result="SYNTHETIC",
        settled_result=result,
        settlement_source="SYNTHETIC_TEST_NOT_AN_OFFICIAL_SOURCE",
    )


def _batch():
    first = _leg("A:hinge", "A", "event-1", "Team-X", "LOSS")
    second = _leg("A:other", "A", "event-2", "Player-Y", "WIN", market="POINTS", line=7.5)
    legs = [
        first, second,
        _leg("B:hinge", "B", "event-1", "Team-X", "LOSS"),
        _leg("B:other", "B", "event-1", "Player-Z", "WIN", market="POINTS", line=7.5),
        _leg("C:hinge", "C", "event-1", "Team-X", "LOSS"),
        _leg("C:other", "C", "event-3", "Player-W", "WIN", market="ASSISTS", line=3.5),
    ]
    positions = [
        PostmortemPositionInput(
            position_reference="A", position_structure="HYPOTHETICAL_2P_POWER",
            underlying_market_count=2, entry_cost=0, gross_return=0, all_or_nothing=True
        ),
        PostmortemPositionInput(
            position_reference="B", position_structure="HYPOTHETICAL_2P_POWER",
            underlying_market_count=2, entry_cost=0, gross_return=0, all_or_nothing=True
        ),
        PostmortemPositionInput(
            position_reference="C", position_structure="HYPOTHETICAL_2P_FLEX",
            underlying_market_count=2, entry_cost=0, gross_return=0, all_or_nothing=False
        ),
    ]
    return PostmortemRunInput(
        research_run_id="synthetic-nov23-slip-process",
        slate_date="2026-11-23",
        source_type="HYPOTHETICAL_NOT_SUBMITTED",
        preserve=["specific line and actual settlement"],
        regression_checks=["no invented P/price/ROI", "separate builder from sporting model"],
        legs=legs,
        positions=positions,
    )


def test_build_payload_contains_separate_builder_retro_without_model_attribution():
    payload = build_postmortem_payload(_batch())
    builder = payload["slip_construction_postmortem"]
    pick = payload["summary"]
    assert builder["scope"] == "SLIP_BUILD_PROCESS_SEPARATE_FROM_SPORTING_FORECAST_GRADING"
    assert builder["position_count"] == 3
    assert builder["distinct_selection_count"] == 4
    assert builder["unique_selection_result_counts"] == {
        "LOSS": 1, "PUSH": 0, "VOID": 0, "WIN": 3
    }
    assert builder["shared_losing_hinge_count"] == 1
    assert builder["affected_all_or_nothing_position_count"] == 2
    hinge = builder["shared_losing_hinges"][0]
    assert hinge["position_references"] == ["A", "B", "C"]
    assert hinge["all_or_nothing_positions"] == ["A", "B"]
    assert pick["distinct_selections"] == builder["distinct_selection_count"]
    assert pick["matched_pregame_count"] == 0
    assert pick["calibration_eligible_count"] == 0
    assert pick["roi"] is None
    assert builder["independent_joint_probability"] is None
    assert builder["modeled_expected_value"] is None
    assert builder["authenticated_submission_verified"] is False
    assert builder["realized_roi_verified"] is False
    assert builder["can_execute"] is False
    assert payload["can_execute"] is False


def test_same_game_and_cross_slip_hinge_are_separate_problems():
    payload = build_postmortem_payload(_batch())
    cards = {x["position_reference"]: x for x in payload["slip_construction_postmortem"]["cards"]}
    assert cards["A"]["same_event_extra_legs"] == 0
    assert cards["B"]["same_event_extra_legs"] == 1
    assert "SAME_EVENT_DEPENDENCY_UNVERIFIED" in cards["B"]["construction_flags"]
    assert "SAME_EVENT_DEPENDENCY_UNVERIFIED" not in cards["A"]["construction_flags"]
    assert "CROSS_SLIP_SHARED_THESIS_EXPOSURE" in cards["A"]["construction_flags"]
    assert cards["A"]["observed_result"] == "HAS_RECORDED_LOSS"
    assert cards["C"]["shared_loss_hinge_exposure"] is False
    assert cards["C"]["observed_result"] == "HAS_RECORDED_LOSS"
    assert cards["C"]["accepted_ticket_settlement"] is None
    assert all(x["realized_profit_verified"] is False for x in cards.values())


def test_report_does_not_mutate_canonical_postmortem_records():
    payload = build_postmortem_payload(_batch())
    legs, positions = copy.deepcopy(payload["legs"]), copy.deepcopy(payload["positions"])
    report = build_slip_construction_postmortem(legs, positions)
    assert legs == payload["legs"]
    assert positions == payload["positions"]
    assert report == payload["slip_construction_postmortem"]


def test_long_power_and_void_do_not_imply_full_sportsbook_loss():
    items = [
        {"row_key": f"x{i}", "position_reference": "X", "sport": "NBA",
         "event_id": f"e{i}", "participant": f"p{i}", "market": "POINTS",
         "side": "MORE", "selection": f"p{i} points", "exact_line": 5.5,
         "settled_result": "VOID" if i == 3 else "WIN"}
        for i in range(4)
    ]
    report = build_slip_construction_postmortem(
        items, [{"position_reference": "X", "position_structure": "POWER",
                 "underlying_market_count": 4, "all_or_nothing": True}]
    )
    card = report["cards"][0]
    assert "FOUR_PLUS_LEGS_FRAGILITY" in card["construction_flags"]
    assert card["observed_result"] == "PUSH_OR_VOID_REQUIRES_PLATFORM_SETTLEMENT"
    assert card["observed_all_legs_clear"] is False
    assert card["accepted_ticket_settlement"] is None


def test_conflicting_exact_results_rejected_by_both_reports():
    legs = [
        {"row_key": "a", "position_reference": "A", "sport": "NHL",
         "event_id": "e1", "participant": "P", "market": "POINTS", "side": "MORE",
         "selection": "P MORE 0.5", "exact_line": 0.5, "settled_result": "WIN"},
        {"row_key": "b", "position_reference": "B", "sport": "NHL",
         "event_id": "e1", "participant": "P", "market": "POINTS", "side": "MORE",
         "selection": "P MORE 0.5", "exact_line": 0.5, "settled_result": "LOSS"},
    ]
    positions = [
        {"position_reference": x, "underlying_market_count": 1,
         "all_or_nothing": True} for x in "AB"
    ]
    with pytest.raises(ValueError, match="CONFLICTING_EXACT_SELECTION_SETTLEMENT"):
        build_slip_construction_postmortem(legs, positions)


def test_research_report_never_claims_verified_economics_from_user_reported_stakes():
    items = [
        {"row_key": "x", "position_reference": "S", "sport": "NFL",
         "event_id": "e1", "participant": "Team-X", "market": "MONEYLINE",
         "side": "WIN", "selection": "Team-X to win", "exact_line": None,
         "settled_result": "WIN"},
    ]
    report = build_slip_construction_postmortem(
        items,
        [{"position_reference": "S", "position_structure": "POWER",
          "underlying_market_count": 1, "entry_cost": 20.0,
          "gross_return": 200.0, "all_or_nothing": True}],
    )
    assert report["cards"][0]["observed_result"] == "ALL_RECORDED_LEGS_WIN"
    assert report["modeled_expected_value"] is None
    assert report["accepted_quote_verified"] is False
    assert report["realized_roi_verified"] is False
