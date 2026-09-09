from types import SimpleNamespace

from v17.top10_model_reconciliation import (
    TOP10_INCOMPLETE_MODEL_RECONCILIATION,
    enforce_top10_completion,
    has_typed_blocker,
    has_valid_model_package,
    is_required_top10_row,
    reconcile_top10_rows,
)


def _row(index: int, *, sport: str = "MLB", stat_type: str = "PITCHER_STRIKEOUTS"):
    return SimpleNamespace(row_key=f"row-{index}", sport=sport, stat_type=stat_type)


def _model(row_id: str, probability: float = 0.64, lower: float = 0.58):
    return {
        "row_key": row_id,
        "terminal_status": "COMPLETED",
        "code": "MODEL_QUALIFIED",
        "model_evaluated": True,
        "result": {
            "prediction": {
                "calibrated_probability": probability,
                "calibrated_probability_lower_bound": lower,
            }
        },
    }


def _blocker(row_id: str, code: str = "MODEL_INPUTS_INSUFFICIENT"):
    return {
        "row_key": row_id,
        "terminal_status": "HELD",
        "code": code,
        "model_evaluated": False,
        "detail": {"specialist_invoked": False},
    }


def test_ten_ranked_plus_one_unresolved_is_incomplete():
    rows = [_row(i) for i in range(1, 12)]
    outcomes = [_model(f"row-{i}") for i in range(1, 11)]
    outcomes.append(
        {
            "row_key": "row-11",
            "terminal_status": "HELD",
            "code": "UNRESOLVED",
            "model_evaluated": False,
        }
    )

    report = reconcile_top10_rows(rows, outcomes)

    assert report["rows_in_scope"] == 11
    assert report["rows_with_valid_model_package"] == 10
    assert report["rows_with_typed_blocker"] == 0
    assert report["balanced"] is False
    assert report["completion_blocker"] == TOP10_INCOMPLETE_MODEL_RECONCILIATION
    assert report["unreconciled_row_ids"] == ["row-11"]


def test_typed_blockers_remain_in_denominator_without_filler():
    rows = [_row(i) for i in range(1, 12)]
    outcomes = [_model(f"row-{i}") for i in range(1, 10)]
    outcomes += [
        _blocker("row-10", "MODEL_INPUTS_INSUFFICIENT"),
        _blocker("row-11", "MODEL_SCORER_FAILED"),
    ]

    report = reconcile_top10_rows(rows, outcomes)

    assert report["rows_in_scope"] == 11
    assert report["rows_with_valid_model_package"] == 9
    assert report["rows_with_typed_blocker"] == 2
    assert report["balanced"] is True
    assert report["completion_blocker"] is None


def test_missing_receipt_fails_exact_once_reconciliation():
    rows = [_row(1), _row(2)]
    report = reconcile_top10_rows(rows, [_model("row-1")])

    assert report["balanced"] is False
    assert report["omitted_row_ids"] == ["row-2"]
    assert report["completion_blocker"] == TOP10_INCOMPLETE_MODEL_RECONCILIATION


def test_duplicate_receipt_fails_exact_once_reconciliation():
    rows = [_row(1)]
    report = reconcile_top10_rows(rows, [_model("row-1"), _model("row-1")])

    assert report["balanced"] is False
    assert report["duplicate_receipt_row_ids"] == ["row-1"]


def test_model_package_requires_calibrated_probability_and_lower_bound():
    outcome = _model("row-1")
    del outcome["result"]["prediction"]["calibrated_probability_lower_bound"]

    assert has_valid_model_package(outcome) is False


def test_action_attempt_with_pre_specialist_model_unavailable_is_a_typed_blocker():
    outcome = _blocker("row-1", "MODEL_UNAVAILABLE")
    outcome["scoring_attempted"] = True
    outcome["detail"]["specialist_invoked"] = False

    assert has_typed_blocker(outcome) is True


def test_post_specialist_invocation_model_unavailable_is_not_a_typed_blocker():
    outcome = _blocker("row-1", "MODEL_UNAVAILABLE")
    outcome["scoring_attempted"] = True
    outcome["detail"]["specialist_invoked"] = True

    assert has_typed_blocker(outcome) is False


def test_typed_scorer_failure_is_accepted_after_invocation():
    outcome = _blocker("row-1", "MODEL_SCORER_FAILED")
    outcome["scoring_attempted"] = True
    outcome["detail"]["specialist_invoked"] = True

    assert has_typed_blocker(outcome) is True


def test_scope_is_targeted_to_requested_families():
    assert is_required_top10_row(_row(1, stat_type="1IP")) is True
    assert is_required_top10_row(_row(1, stat_type="Fantasy Score")) is True
    assert is_required_top10_row(_row(1, stat_type="Pitches 95+ MPH")) is True
    assert is_required_top10_row(_row(1, sport="TENNIS", stat_type="ACES")) is True
    assert is_required_top10_row(_row(1, sport="SOCCER", stat_type="PASSES")) is True
    assert is_required_top10_row(_row(1, sport="NBA", stat_type="POINTS")) is False


def test_enforcement_blocks_completion_but_preserves_rows():
    rows = [_row(i) for i in range(1, 12)]
    outcomes = [_model(f"row-{i}") for i in range(1, 11)]
    outcomes.append(
        {
            "row_key": "row-11",
            "terminal_status": "HELD",
            "code": "NOT_CALLED",
            "model_evaluated": False,
        }
    )
    original = {
        "ok": True,
        "run_controller_status": "DEGRADED",
        "reconciliation_pass": True,
        "rows": outcomes,
        "can_execute": False,
    }

    enforced = enforce_top10_completion(original, rows)

    assert enforced["ok"] is False
    assert enforced["run_controller_status"] == "BLOCKED"
    assert enforced["reconciliation_pass"] is False
    assert enforced["completion_blocker"] == TOP10_INCOMPLETE_MODEL_RECONCILIATION
    assert enforced["rows"] == outcomes
    assert enforced["can_execute"] is False
