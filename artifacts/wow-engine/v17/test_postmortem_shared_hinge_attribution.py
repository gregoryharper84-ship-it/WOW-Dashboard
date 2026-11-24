"""Synthetic cross-slip retrospective attribution regression.

No fixture here is a real market, submitted entry, or certified model forecast.
"""
from __future__ import annotations

import pytest

from v17.postmortem_engine import (
    PostmortemLegInput,
    PostmortemPositionInput,
    PostmortemRunInput,
    build_postmortem_payload,
)


def leg(row: str, pos: str, *, event: str, sport: str = "NBA", result: str = "LOSS"):
    return PostmortemLegInput(
        row_key=row,
        position_reference=pos,
        sport=sport,
        event_id=event,
        participant="Synthetic Player",
        market="REBOUNDS",
        side="MORE",
        selection="Synthetic Player MORE 7.5 REBOUNDS",
        exact_line=7.5,
        actual_stat=6 if result == "LOSS" else 9,
        official_result="SYNTHETIC_ONLY",
        settled_result=result,
        settlement_source="SYNTHETIC_TEST_DATA_NOT_OFFICIAL",
    )


def batch(legs, *, structures=None):
    structures = structures or {}
    refs = sorted({l.position_reference for l in legs})
    return PostmortemRunInput(
        research_run_id="synthetic-shared-loss-regression",
        slate_date="2026-11-23",
        source_type="SYNTHETIC_CHAT_PROPOSALS_NEVER_SUBMITTED",
        preserve=["literal pregame card identity; no fabricated receipt"],
        regression_checks=["do not turn shared observed losses into fitted probabilities"],
        legs=legs,
        positions=[
            PostmortemPositionInput(
                position_reference=ref,
                position_structure=structures.get(ref, "HYPOTHETICAL_POWER"),
                underlying_market_count=sum(l.position_reference == ref for l in legs),
                entry_cost=0,
                gross_return=0,
                all_or_nothing=(structures.get(ref) != "FLEX"),
            )
            for ref in refs
        ],
    )


def test_shared_losing_hinge_deduplicates_unique_outcomes_not_card_occurrences():
    data = batch([
        leg("A:h", "A", event="e1"),
        leg("A:w", "A", event="e2", result="WIN"),
        leg("B:h", "B", event="e1"),
        leg("B:w", "B", event="e3", result="WIN"),
        leg("C:w1", "C", event="e4", result="WIN"),
        leg("C:w2", "C", event="e5", result="WIN"),
    ])
    payload = build_postmortem_payload(data)
    summary = payload["summary"]
    assert (summary["legs"], summary["wins"], summary["losses"]) == (6, 4, 2)
    assert summary["distinct_selections"] == 5
    assert summary["distinct_selection_results"] == {
        "LOSS": 1, "PUSH": 0, "VOID": 0, "WIN": 4
    }
    assert summary["distinct_selection_hit_rate"] == pytest.approx(0.8)
    assert summary["shared_losing_hinge_count"] == 1
    hinge = summary["shared_losing_hinges"][0]
    assert hinge["positions_repeating_loss"] == ["A", "B"]
    assert hinge["all_or_nothing_positions_impacted"] == ["A", "B"]
    assert len(hinge["exact_observed_identity_hash"]) == 64
    assert summary["all_or_nothing_positions_impacted_by_shared_losses"] == 2
    assert summary["all_leg_clear_positions"] == 1
    assert summary["calibration_eligible_count"] == 0
    assert summary["roi"] is None
    assert summary["non_losing_positions"] == 0
    assert summary["profitable_positions"] == 0
    assert summary["reported_positive_entry_positions"] == 0
    assert payload["can_execute"] is False


def test_repeated_exact_pick_with_conflicting_settlement_is_rejected():
    data = batch([
        leg("A:h", "A", event="same", result="LOSS"),
        leg("B:h", "B", event="same", result="WIN"),
    ])
    with pytest.raises(ValueError, match="CONFLICTING_EXACT_SELECTION_SETTLEMENT"):
        build_postmortem_payload(data)


def test_same_player_market_name_in_different_events_is_not_one_hinge():
    data = batch([
        leg("A:h", "A", event="first", result="LOSS"),
        leg("B:h", "B", event="second", result="WIN"),
    ])
    summary = build_postmortem_payload(data)["summary"]
    assert summary["distinct_selections"] == 2
    assert summary["shared_losing_hinge_count"] == 0


def test_flex_repeated_loss_not_mislabeled_as_all_or_nothing_kill():
    data = batch([
        leg("A:h", "A", event="same"),
        leg("B:h", "B", event="same"),
    ], structures={"B": "FLEX"})
    summary = build_postmortem_payload(data)["summary"]
    assert summary["shared_losing_hinge_count"] == 1
    assert summary["all_or_nothing_positions_impacted_by_shared_losses"] == 1
    assert summary["shared_losing_hinges"][0]["all_or_nothing_positions_impacted"] == ["A"]
    assert summary["roi"] is None
