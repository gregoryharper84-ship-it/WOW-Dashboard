from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17.deadline_aware_recovery_priority import prioritize_recovery_tasks

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


def task(**overrides):
    record = {
        "task_id": "T-1",
        "component": "nba_shared_hydration_producer",
        "owner": "DATA_INTEGRATION_ENGINEER",
        "blocked_candidates": 12,
        "shared_dependency": True,
        "earliest_event_start_at": "2026-11-28T23:00:00Z",
        "estimated_fix_minutes": 30,
    }
    record.update(overrides)
    return record


def test_shared_producer_unblocking_most_candidates_ranks_first():
    producer = task()
    report_only = task(
        task_id="T-2",
        component="weekly_reporting",
        blocked_candidates=1,
        shared_dependency=False,
        earliest_event_start_at="2026-11-28T21:00:00Z",
    )
    result = prioritize_recovery_tasks([report_only, producer], now=NOW)
    assert [item["task_id"] for item in result["ranked"]] == ["T-1", "T-2"]
    assert result["ranked"][0]["timeliness"] == "TIMELY"
    assert "shared producer" in result["ranked"][0]["rationale"]
    assert result["can_execute"] is False


def test_repair_that_cannot_finish_before_the_event_is_at_risk_not_first():
    tight = task(
        task_id="T-tight",
        blocked_candidates=40,
        earliest_event_start_at="2026-11-28T12:20:00Z",
        estimated_fix_minutes=60,
    )
    roomy = task(task_id="T-room", blocked_candidates=5, shared_dependency=False)
    result = prioritize_recovery_tasks([tight, roomy], now=NOW)
    assert [item["task_id"] for item in result["ranked"]] == ["T-room", "T-tight"]
    assert result["ranked"][1]["timeliness"] == "AT_RISK"


def test_expired_work_is_reported_last_but_never_dropped():
    missed = task(
        task_id="T-missed",
        blocked_candidates=99,
        earliest_event_start_at="2026-11-28T11:00:00Z",
    )
    live = task(task_id="T-live", blocked_candidates=2, shared_dependency=False)
    result = prioritize_recovery_tasks([missed, live], now=NOW)
    assert [item["task_id"] for item in result["ranked"]] == ["T-live", "T-missed"]
    assert result["expired_tasks"] == ["T-missed"]
    assert result["summary"]["MISSED_DEADLINE"] == 1


def test_unproven_window_ranks_between_at_risk_and_missed():
    unknown = task(task_id="T-unknown", blocked_candidates=50,
                   earliest_event_start_at=None)
    at_risk = task(task_id="T-risk", blocked_candidates=1,
                   earliest_event_start_at="2026-11-28T12:15:00Z",
                   estimated_fix_minutes=45)
    missed = task(task_id="T-missed", blocked_candidates=100,
                  earliest_event_start_at="2026-11-28T09:00:00Z")
    result = prioritize_recovery_tasks([missed, unknown, at_risk], now=NOW)
    assert [item["timeliness"] for item in result["ranked"]] == [
        "AT_RISK",
        "UNKNOWN_WINDOW",
        "MISSED_DEADLINE",
    ]


def test_malformed_tasks_are_reported_not_silently_skipped():
    result = prioritize_recovery_tasks(
        [task(), {"task_id": "T-bad", "component": "x", "blocked_candidates": -3}], now=NOW
    )
    assert [item["task_id"] for item in result["ranked"]] == ["T-1"]
    assert result["blocked"][0]["task_id"] == "T-bad"
    assert "TASK_1_BLOCKED_CANDIDATES_UNATTESTED" in result["blocked"][0]["blockers"]
    assert "TASK_1_ESTIMATED_FIX_UNATTESTED" in result["blocked"][0]["blockers"]


def test_duplicate_task_ids_are_never_ranked_twice():
    result = prioritize_recovery_tasks(
        [task(), task(component="other")], now=NOW
    )
    assert len(result["ranked"]) == 1
    assert "TASK_1_DUPLICATE_TASK_ID" in result["blocked"][0]["blockers"]


def test_ordering_is_deterministic_and_preserves_ownership():
    a = task(task_id="T-9", blocked_candidates=4, shared_dependency=False,
             earliest_event_start_at="2026-11-28T18:00:00Z")
    b = task(task_id="T-3", blocked_candidates=4, shared_dependency=False,
             earliest_event_start_at="2026-11-28T18:00:00Z")
    result = prioritize_recovery_tasks([a, b], now=NOW)
    assert [item["task_id"] for item in result["ranked"]] == ["T-3", "T-9"]
    assert result["ranked"][0]["owner"] == "DATA_INTEGRATION_ENGINEER"
    assert result["ranked"][0]["rank"] == 1


def test_naive_now_is_rejected():
    with pytest.raises(ValueError, match="NOW_MUST_BE_TIMEZONE_AWARE"):
        prioritize_recovery_tasks([task()], now=datetime(2026, 11, 28, 12, 0))


def test_empty_queue_is_not_an_error():
    result = prioritize_recovery_tasks([], now=NOW)
    assert result["ranked"] == []
    assert result["expired_tasks"] == []
