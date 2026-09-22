import v17.pick_request_run_control as subject


def _safe_pending(**overrides):
    row = {
        "row_key": "row-1",
        "status": "UNRESOLVED",
        "code": "DURABLE_ROW_PENDING_SAFE_TO_RESUME",
        "retry_allowed": True,
        "match_count": 0,
        "matches": [],
        "resume": {
            "request_id": "board-1",
            "row_key": "row-1",
            "contract": "REUSE_EXACT_BOARD_REQUEST_ID_AND_ROW_KEY",
            "can_execute": False,
        },
        "can_execute": False,
    }
    row.update(overrides)
    return row


def test_exact_durable_pending_contract_is_safe_for_run_control():
    assert subject._durable_pending_resume_is_safe(
        _safe_pending(),
        request_id="board-1",
        row_key="row-1",
    ) is True


def test_durable_pending_wrong_request_id_remains_unsafe():
    assert subject._durable_pending_resume_is_safe(
        _safe_pending(),
        request_id="different-board",
        row_key="row-1",
    ) is False


def test_durable_pending_wrong_row_contract_remains_unsafe():
    row = _safe_pending(
        resume={
            "request_id": "board-1",
            "row_key": "other-row",
            "contract": "REUSE_EXACT_BOARD_REQUEST_ID_AND_ROW_KEY",
            "can_execute": False,
        }
    )
    assert subject._durable_pending_resume_is_safe(
        row,
        request_id="board-1",
        row_key="row-1",
    ) is False


def test_blocked_and_matched_receipt_states_are_not_safe_pending_resume():
    blocked = {
        "row_key": "row-1",
        "status": "BLOCKED",
        "code": "PREDICTION_RECEIPT_INTEGRITY_MISMATCH",
        "retry_allowed": False,
        "can_execute": False,
    }
    matched = {
        "row_key": "row-1",
        "status": "MATCHED",
        "code": "IMMUTABLE_PREGAME_PREDICTION_MATCHED",
        "match_count": 1,
        "matches": [{"is_immutable_pregame": True}],
        "can_execute": False,
    }

    assert subject._durable_pending_resume_is_safe(
        blocked,
        request_id="board-1",
        row_key="row-1",
    ) is False
    assert subject._durable_pending_resume_is_safe(
        matched,
        request_id="board-1",
        row_key="row-1",
    ) is False
