from v17.pick_request_state_reliability_patch import _run_control_receipt_result


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


def test_exact_durable_pending_contract_is_safe_for_run_control_only():
    source = {"request_id": "board-1", "rows": [_safe_pending()], "can_execute": False}

    result = _run_control_receipt_result(source, request_id="board-1")

    row = result["rows"][0]
    assert row["status"] == "NOT_FOUND"
    assert row["run_control_resume_from_durable_pending"] is True
    assert row["code"] == "DURABLE_ROW_PENDING_SAFE_TO_RESUME"
    assert source["rows"][0]["status"] == "UNRESOLVED"
    assert result["can_execute"] is False


def test_durable_pending_wrong_request_id_remains_unresolved():
    result = _run_control_receipt_result(
        {"rows": [_safe_pending()]},
        request_id="different-board",
    )

    assert result["rows"][0]["status"] == "UNRESOLVED"
    assert "run_control_resume_from_durable_pending" not in result["rows"][0]


def test_durable_pending_wrong_row_contract_remains_unresolved():
    row = _safe_pending(
        resume={
            "request_id": "board-1",
            "row_key": "other-row",
            "contract": "REUSE_EXACT_BOARD_REQUEST_ID_AND_ROW_KEY",
            "can_execute": False,
        }
    )
    result = _run_control_receipt_result({"rows": [row]}, request_id="board-1")

    assert result["rows"][0]["status"] == "UNRESOLVED"


def test_blocked_and_matched_receipt_states_are_not_relaxed():
    blocked = {
        "row_key": "row-1",
        "status": "BLOCKED",
        "code": "PREDICTION_RECEIPT_INTEGRITY_MISMATCH",
        "retry_allowed": False,
        "can_execute": False,
    }
    matched = {
        "row_key": "row-2",
        "status": "MATCHED",
        "code": "IMMUTABLE_PREGAME_PREDICTION_MATCHED",
        "match_count": 1,
        "matches": [{"is_immutable_pregame": True}],
        "can_execute": False,
    }

    result = _run_control_receipt_result(
        {"rows": [blocked, matched], "can_execute": False},
        request_id="board-1",
    )

    assert result["rows"][0] == blocked
    assert result["rows"][1] == matched
    assert result["can_execute"] is False
