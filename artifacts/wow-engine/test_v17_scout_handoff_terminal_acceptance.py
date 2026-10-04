from v17 import scout_handoff_terminal_acceptance as terminal


def _complete_summary():
    return {
        "source_run_id": "run-1",
        "research_run_id": "research-1",
        "status": "COMPLETE",
        "candidate_jobs": 2,
        "specialist_processing_seen": 2,
        "rows_in": 2,
        "rows_completed": 1,
        "rows_held": 0,
        "rows_rejected": 1,
        "row_accounting_pass": True,
        "reconciliation_pass": True,
        "terminal_code_counts": {"MODEL_INPUTS_INSUFFICIENT": 1},
        "jobs": [
            {
                "candidate_id": "a",
                "terminal": True,
                "can_execute": False,
                "request_payload": {"row_key": "a"},
            },
            {
                "candidate_id": "b",
                "terminal": True,
                "can_execute": False,
                "request_payload": {"event_key": "MLB:1"},
            },
        ],
        "state_events": [
            {"candidate_id": "a", "state": "MODEL_EVALUATED", "can_execute": False},
            {"candidate_id": "b", "state": "HANDOFF_BLOCKED", "can_execute": False},
        ],
        "can_execute": False,
    }


def test_terminal_summary_accepts_reconciled_completed_and_typed_rejected_rows():
    result = terminal.validate_terminal_summary(_complete_summary())

    assert result["status"] == "PASS"
    assert result["rows_in"] == 2
    assert result["rows_completed"] == 1
    assert result["rows_rejected"] == 1
    assert result["blockers"] == []
    assert result["can_execute"] is False


def test_terminal_summary_rejects_probability_authority_payload_and_execution_drift():
    summary = _complete_summary()
    summary["jobs"][0]["request_payload"]["model_probability"] = 0.9
    summary["jobs"][1]["can_execute"] = True

    result = terminal.validate_terminal_summary(summary)

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert any(code.startswith("SCOUT_PROBABILITY_AUTHORITY_VIOLATION:") for code in result["blockers"])
    assert "SCOUT_EXECUTION_GOVERNANCE_VIOLATION" in result["blockers"]


def test_terminal_summary_requires_complete_accounted_worker_processed_run():
    summary = _complete_summary()
    summary.update({
        "status": "IN_PROGRESS",
        "candidate_jobs": 3,
        "rows_in": 2,
        "rows_completed": 1,
        "rows_held": 1,
        "rows_rejected": 0,
        "row_accounting_pass": False,
        "reconciliation_pass": False,
        "specialist_processing_seen": 0,
    })

    result = terminal.validate_terminal_summary(summary)

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    for blocker in (
        "SCOUT_TERMINAL_ACCEPTANCE_RUN_NOT_COMPLETE",
        "SCOUT_TERMINAL_ACCEPTANCE_CANDIDATE_COUNT_MISMATCH",
        "SCOUT_TERMINAL_ACCEPTANCE_ROW_ACCOUNTING_NOT_PROVEN",
        "SCOUT_TERMINAL_ACCEPTANCE_RECONCILIATION_NOT_PROVEN",
        "SCOUT_TERMINAL_ACCEPTANCE_WORKER_PATH_NOT_PROVEN",
    ):
        assert blocker in result["blockers"]


def test_wait_for_terminal_polls_until_complete(monkeypatch):
    pending = {
        "source_run_id": "run-1",
        "status": "IN_PROGRESS",
        "rows_in": 2,
        "rows_completed": 0,
        "rows_held": 2,
        "rows_rejected": 0,
        "can_execute": False,
    }
    complete = _complete_summary()
    responses = iter([
        {"ok": True, "http_status": 200, "body": pending, "can_execute": False},
        {"ok": True, "http_status": 200, "body": complete, "can_execute": False},
    ])
    monkeypatch.setattr(terminal, "_get_status", lambda *args, **kwargs: next(responses))

    clock = iter([0, 0, 1, 1])
    result = terminal.wait_for_terminal(
        origin="https://engine.example",
        source_run_id="run-1",
        token="token",
        timeout_seconds=10,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: next(clock),
    )

    assert result["status"] == "PASS"


def test_wait_for_terminal_timeout_is_typed(monkeypatch):
    monkeypatch.setattr(
        terminal,
        "_get_status",
        lambda *args, **kwargs: {
            "ok": True,
            "http_status": 200,
            "body": {
                "source_run_id": "run-1",
                "status": "IN_PROGRESS",
                "rows_in": 1,
                "rows_completed": 0,
                "rows_held": 1,
                "rows_rejected": 0,
                "row_accounting_pass": True,
                "reconciliation_pass": False,
                "jobs": [{"request_payload": {}, "can_execute": False}],
                "state_events": [],
                "can_execute": False,
            },
            "can_execute": False,
        },
    )
    clock = iter([0, 0, 2, 2])

    result = terminal.wait_for_terminal(
        origin="https://engine.example",
        source_run_id="run-1",
        token="token",
        timeout_seconds=1,
        sleep_fn=lambda _: None,
        monotonic_fn=lambda: next(clock),
    )

    assert result["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert "SCOUT_TERMINAL_ACCEPTANCE_TIMEOUT" in result["blockers"]
    assert result["can_execute"] is False
