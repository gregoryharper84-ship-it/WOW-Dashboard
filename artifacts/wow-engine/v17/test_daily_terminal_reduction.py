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



@pytest.mark.parametrize(
    "payload",
    [
        {
            "probability_publishable": True,
            "rank_eligible": True,
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
            "sporting_probability_completed": True,
        },
        {
            "probability_publishable": True,
            "rank_eligible": True,
            "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
            "nfl_stage_receipt": {"status": "HOLD", "blockers": ["NFL_FORWARD_CERTIFICATION_UNPROVEN"]},
        },
        {
            "probability_publishable": True,
            "rank_eligible": True,
            "nfl_stage_receipt": {"status": "HOLD"},
        },
        {
            "probability_publishable": True,
            "rank_eligible": True,
            "probability_qualification": {"terminal_ceiling": "MODEL_QUALIFIED_HOLD"},
        },
    ],
)
def test_controlling_hold_overrides_stale_wrapper_approval(payload):
    assert classify_stage_status(payload) == "HELD"
    audited = assert_no_terminal_upgrade(reduce_row_terminal([classify_stage_status(payload)]))
    assert audited["stage_terminals"] == ["HELD"]
    assert audited["lowest_stage_terminal"] == "HELD"
    assert audited["final_terminal"] == "HELD"
    assert audited["approved_stage_exception_applied"] is False


@pytest.mark.parametrize("blocking", ["HELD", "INVALID", "PURGED"])
def test_approved_stage_cannot_override_an_unresolved_or_invalid_stage(blocking):
    audited = assert_no_terminal_upgrade(reduce_row_terminal(["COMPLETED", blocking]))
    assert audited["stage_terminals"] == ["COMPLETED", blocking]
    assert audited["lowest_stage_terminal"] == blocking
    assert audited["final_terminal"] == blocking
    assert audited["approved_stage_exception_applied"] is False


def test_approved_complementary_prop_direction_stays_completed():
    audited = assert_no_terminal_upgrade(reduce_row_terminal(["REJECTED", "COMPLETED"]))
    assert audited["stage_terminals"] == ["REJECTED", "COMPLETED"]
    assert audited["lowest_stage_terminal"] == "REJECTED"
    assert audited["final_terminal"] == "COMPLETED"
    assert audited["approved_stage_exception_applied"] is True


def test_invalid_audit_upgrade_to_completed_fails_closed():
    upgraded = assert_no_terminal_upgrade({
        "stage_terminals": ["COMPLETED", "HELD"],
        "lowest_stage_terminal": "HELD",
        "final_terminal": "COMPLETED",
    })
    assert upgraded["final_terminal"] == "HELD"
    assert upgraded["terminal_upgraded_from_rejection"] is True
    assert RUN_INVALID_TERMINAL_UPGRADE in upgraded["blockers"]


@pytest.mark.parametrize(
    "stages,claimed_lowest,claimed_final,exception,expected",
    [
        (["COMPLETED"], "HELD", "COMPLETED", True, "HELD"),
        (["REJECTED", "COMPLETED"], "HELD", "COMPLETED", True, "REJECTED"),
        (["COMPLETED", "HELD"], "REJECTED", "COMPLETED", True, "REJECTED"),
        (["COMPLETED", "REJECTED"], "REJECTED", "COMPLETED", False, "REJECTED"),
        (["COMPLETED", "MYSTERY"], "HELD", "COMPLETED", True, "HELD"),
    ],
)
def test_forged_reduction_audit_never_softens_terminal(
    stages, claimed_lowest, claimed_final, exception, expected,
):
    audited = assert_no_terminal_upgrade({
        "stage_terminals": stages,
        "lowest_stage_terminal": claimed_lowest,
        "final_terminal": claimed_final,
        "approved_stage_exception_applied": exception,
    })
    assert audited["final_terminal"] == expected
    assert audited["terminal_upgraded_from_rejection"] is True
    assert RUN_INVALID_TERMINAL_UPGRADE in audited["blockers"]


def test_empty_reduction_audit_fails_closed_even_if_marked_approved():
    audited = assert_no_terminal_upgrade({
        "stage_terminals": [],
        "lowest_stage_terminal": "COMPLETED",
        "final_terminal": "COMPLETED",
        "approved_stage_exception_applied": True,
    })
    assert audited["final_terminal"] == "HELD"
    assert audited["terminal_upgraded_from_rejection"] is True
    assert RUN_INVALID_TERMINAL_UPGRADE in audited["blockers"]


def test_a_softened_terminal_is_caught_and_reverted():
    upgraded = assert_no_terminal_upgrade(
        {"lowest_stage_terminal": "REJECTED", "final_terminal": "HELD", "stage_terminals": ["REJECTED"]}
    )
    assert upgraded["final_terminal"] == "REJECTED"
    assert upgraded["terminal_upgraded_from_rejection"] is True
    assert RUN_INVALID_TERMINAL_UPGRADE in upgraded["blockers"]
