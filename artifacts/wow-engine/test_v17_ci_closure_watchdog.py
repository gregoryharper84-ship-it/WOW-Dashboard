from __future__ import annotations

from datetime import datetime, timezone

from v17 import ci_closure_watchdog as subject


NOW = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)


def test_cancelled_before_start_is_infrastructure_and_retries_once() -> None:
    result = subject.classify_required_run(
        {
            "status": "completed",
            "conclusion": "failure",
            "run_attempt": 1,
            "created_at": "2026-10-05T19:00:00Z",
        },
        [
            {
                "name": "WOW Render and Action release contracts",
                "status": "completed",
                "conclusion": "cancelled",
                "steps": [],
            }
        ],
        now=NOW,
    )
    assert result["classification"] == subject.CI_JOB_CANCELLED_BEFORE_START
    assert result["recommended_action"] == "RERUN_FAILED_JOBS_ONCE"
    assert result["retry_allowed"] is True


def test_cancelled_before_start_does_not_retry_forever() -> None:
    result = subject.classify_required_run(
        {"status": "completed", "conclusion": "failure", "run_attempt": 2},
        [{"name": "contract", "status": "completed", "conclusion": "cancelled", "steps": []}],
        now=NOW,
    )
    assert result["classification"] == subject.CI_JOB_CANCELLED_BEFORE_START
    assert result["retry_allowed"] is False
    assert result["recommended_action"] == "WAIT_AND_RECHECK"


def test_real_failed_job_remains_required_gate_failure() -> None:
    result = subject.classify_required_run(
        {"status": "completed", "conclusion": "failure", "run_attempt": 1},
        [{
            "name": "tests",
            "status": "completed",
            "conclusion": "failure",
            "steps": [{"name": "pytest", "conclusion": "failure"}],
        }],
        now=NOW,
    )
    assert result["classification"] == subject.CI_REQUIRED_GATE_FAILED
    assert result["recommended_action"] == "INSPECT_REQUIRED_GATE_FAILURE"


def test_old_queued_run_is_capacity_starvation() -> None:
    result = subject.classify_required_run(
        {
            "status": "queued",
            "conclusion": None,
            "run_attempt": 1,
            "created_at": "2026-10-05T19:40:00Z",
        },
        [],
        now=NOW,
        starvation_seconds=600,
    )
    assert result["classification"] == subject.CI_CAPACITY_STARVATION
    assert result["queued_age_seconds"] == 1200


def test_green_run_is_not_retried() -> None:
    result = subject.classify_required_run(
        {"status": "completed", "conclusion": "success", "run_attempt": 1},
        [],
        now=NOW,
    )
    assert result["classification"] == subject.CI_GREEN
    assert result["recommended_action"] == "NONE"


def test_objective_progress_signals_do_not_claim_closure() -> None:
    pr = {
        "state": "open",
        "head": {"sha": "head"},
        "base": {"sha": "base"},
    }
    checks = [
        {"name": "WOW V17 rapid affected regression", "status": "completed", "conclusion": "success"},
        {"name": "WOW required-three regression", "status": "completed", "conclusion": "success"},
        {"name": "WOW governed probability backend", "status": "queued", "conclusion": None},
    ]
    assert subject.objective_progress_signals(pr, checks) == [
        "PR_CREATED",
        "PERSISTED_HEAD_COMMIT",
        "FOCUSED_REGRESSION_GREEN",
        "REQUIRED_THREE_GREEN",
    ]
