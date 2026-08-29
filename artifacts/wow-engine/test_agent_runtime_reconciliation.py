import pytest

from agent_runtime.reconciliation import (
    BALANCED,
    UNBALANCED,
    classify_ceiling,
    reconcile,
    reconcile_from_ceilings,
)


def test_balanced_when_sum_matches():
    result = reconcile(rows_in=10, rows_completed=3, rows_held=5, rows_rejected=2)
    assert result.status == BALANCED


def test_unbalanced_when_sum_does_not_match():
    result = reconcile(rows_in=10, rows_completed=3, rows_held=5, rows_rejected=1)
    assert result.status == UNBALANCED


def test_zero_survivors_is_valid_and_balanced():
    result = reconcile(rows_in=7, rows_completed=0, rows_held=0, rows_rejected=7)
    assert result.status == BALANCED


def test_zero_rows_in_is_valid_and_balanced():
    result = reconcile(rows_in=0, rows_completed=0, rows_held=0, rows_rejected=0)
    assert result.status == BALANCED


def test_negative_counts_rejected():
    with pytest.raises(ValueError):
        reconcile(rows_in=5, rows_completed=-1, rows_held=3, rows_rejected=3)


def test_classify_ceiling_buckets():
    assert classify_ceiling("FINAL_APPROVED") == "completed"
    assert classify_ceiling("MODEL_UNAVAILABLE") == "rejected"
    assert classify_ceiling("NO_SPECIALIST_COVERAGE") == "rejected"
    assert classify_ceiling("MODEL_QUALIFIED_HOLD") == "held"
    assert classify_ceiling("SOME_UNRATIFIED_LABEL") == "held"


def test_reconcile_from_ceilings_matches_manual_reconcile():
    ceilings = ["FINAL_APPROVED", "MODEL_QUALIFIED_HOLD", "MODEL_UNAVAILABLE", "NO_SPECIALIST_COVERAGE"]
    result = reconcile_from_ceilings(rows_in=4, terminal_ceilings=ceilings)
    assert result == reconcile(rows_in=4, rows_completed=1, rows_held=1, rows_rejected=2)
    assert result.status == BALANCED
