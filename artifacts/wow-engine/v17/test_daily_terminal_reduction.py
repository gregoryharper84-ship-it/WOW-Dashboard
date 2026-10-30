"""Unit contract for the Daily lowest-terminal reducer."""
import pytest

from v17.daily_terminal_reduction import (
    RUN_INVALID_TERMINAL_UPGRADE,
    assert_no_terminal_upgrade,
    classify_stage_status,
    lowest_stage_terminal,
    rank_of,
    reduce_row_terminal,
)


def test_terminal_rank_orders_rejection_below_hold():
    assert rank_of("REJECTED") < rank_of("HELD")
    assert rank_of("PURGED") < rank_of("REJECTED")
    assert rank_of("INVALID") < rank_of("PURGED")
    assert rank_of("COMPLETED") > rank_of("HELD")


def test_lowest_terminal_wins_across_stages():
    assert lowest_stage_terminal(["HELD", "REJECTED"]) == "REJECTED"
    assert lowest_stage_terminal(["HELD", "HELD"]) == "HELD"
    assert lowest_stage_terminal(["REJECTED", "PURGED"]) == "PURGED"


def test_unknown_stage_status_fails_closed_to_held():
    assert lowest_stage_terminal(["MYSTERY"]) == "HELD"
    assert reduce_row_terminal(["MYSTERY"])["final_terminal"] == "HELD"


def test_rejection_is_never_softened_into_a_hold():
    reduction = reduce_row_terminal(["REJECTED", "HELD"])
    assert reduction["final_terminal"] == "REJECTED"
    assert reduction["lowest_stage_terminal"] == "REJECTED"


def test_approved_stage_exception_is_scoped_and_audited():
    reduction = reduce_row_terminal(["COMPLETED", "REJECTED"])
    assert reduction["final_terminal"] == "COMPLETED"
    assert reduction["approved_stage_exception_applied"] is True


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"terminal_label": "NO_LOW_PROBABILITY", "pick_rejected": True}, "REJECTED"),
        ({"terminal_label": "REJECT_OOD", "pick_rejected": True}, "REJECTED"),
        ({"terminal_label": "SLATE_PURGE", "pick_rejected": True}, "PURGED"),
        ({"probability_publishable": True, "rank_eligible": True}, "COMPLETED"),
        ({"probability_publishable": True, "rank_eligible": False}, "HELD"),
        ({"probability_qualification": {"pick_rejected": True}}, "REJECTED"),
        ({}, "HELD"),
        ("not-a-dict", "HELD"),
    ],
)
def test_stage_classification_reads_the_controlling_model_receipt(payload, expected):
    assert classify_stage_status(payload) == expected


def test_a_softened_terminal_is_caught_and_reverted():
    upgraded = assert_no_terminal_upgrade(
        {"lowest_stage_terminal": "REJECTED", "final_terminal": "HELD", "stage_terminals": ["REJECTED"]}
    )
    assert upgraded["final_terminal"] == "REJECTED"
    assert upgraded["terminal_upgraded_from_rejection"] is True
    assert RUN_INVALID_TERMINAL_UPGRADE in upgraded["blockers"]
