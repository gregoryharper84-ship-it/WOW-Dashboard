"""Offline adversarial regression suite for read-only CI metrics."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wow_ci_timing.py"
spec = importlib.util.spec_from_file_location("wow_ci_timing", SCRIPT)
assert spec and spec.loader
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
SHA = "a" * 40


def sample():
    run = {"id": 12, "name": "wow-verify", "head_sha": SHA, "status": "completed",
           "conclusion": "success", "created_at": "2026-11-06T10:00:00Z"}
    jobs = {"total_count": 2, "jobs": [
        {"run_id": 12, "name": "collection", "status": "completed", "conclusion": "success",
         "started_at": "2026-11-06T10:00:20Z", "completed_at": "2026-11-06T10:02:20Z",
         "steps": [{"name": "collect", "started_at": "2026-11-06T10:01:00Z",
                    "completed_at": "2026-11-06T10:02:00Z"}]},
        {"run_id": 12, "name": "shard", "status": "completed", "conclusion": "success",
         "started_at": "2026-11-06T10:02:20Z", "completed_at": "2026-11-06T10:05:20Z",
         "steps": [{"name": "pytest", "started_at": "2026-11-06T10:03:00Z",
                    "completed_at": "2026-11-06T10:05:00Z"}]},
    ]}
    return run, jobs


def test_successful_measurement_includes_queue_and_end_to_end():
    run, jobs = sample()
    result = ci.audit_run(run, jobs, SHA)
    assert result["status"] == "MEASURED"
    assert result["queue_seconds"] == 20
    assert result["execution_wall_seconds"] == 300
    assert result["end_to_end_seconds"] == 320
    assert result["slowest_job"] == "shard"
    assert result["step_timing_coverage"] == "COMPLETE"
    assert result["can_execute"] is False
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_missing_job_timing_cannot_be_claimed_as_measured():
    run, jobs = sample()
    del jobs["jobs"][0]["started_at"]
    result = ci.audit_run(run, jobs, SHA)
    assert result["status"] == "TIMINGS_UNAVAILABLE"
    assert "end_to_end_seconds" not in result


def test_missing_step_timings_are_reported_not_invented():
    run, jobs = sample()
    jobs["jobs"][1]["steps"][0].pop("started_at")
    result = ci.audit_run(run, jobs, SHA)
    assert result["status"] == "MEASURED"
    assert result["step_timing_coverage"] == "PARTIAL_OR_UNAVAILABLE"


def test_stale_or_invalid_head_never_passes():
    run, jobs = sample()
    assert ci.audit_run(run, jobs, "b" * 40)["status"] == "HEAD_SHA_MISMATCH"
    assert ci.audit_run(run, jobs, "not-a-sha")["status"] == "HEAD_SHA_MISMATCH"


def test_incomplete_page_never_passes():
    run, jobs = sample()
    jobs["total_count"] = 3
    assert ci.audit_run(run, jobs, SHA)["status"] == "JOBS_PAGE_INCOMPLETE"


def test_missing_total_count_never_passes():
    run, jobs = sample()
    del jobs["total_count"]
    assert ci.audit_run(run, jobs, SHA)["status"] == "JOBS_PAGE_INCOMPLETE"


def test_running_job_never_passes():
    run, jobs = sample()
    jobs["jobs"][1]["status"] = "in_progress"
    assert ci.audit_run(run, jobs, SHA)["status"] == "JOBS_NOT_TERMINAL"


def test_failed_jobs_preserve_negative_evidence():
    run, jobs = sample()
    jobs["jobs"][1]["conclusion"] = "failure"
    assert ci.audit_run(run, jobs, SHA)["status"] == "MEASURED_WITH_FAILURE"


def test_job_run_mismatch_never_passes():
    run, jobs = sample()
    jobs["jobs"][1]["run_id"] = 13
    assert ci.audit_run(run, jobs, SHA)["status"] == "JOB_RUN_ID_MISMATCH"


def test_naive_date_or_end_before_start_fails_closed():
    run, jobs = sample()
    jobs["jobs"][1]["started_at"] = "2026-11-06T10:02:20"
    assert ci.audit_run(run, jobs, SHA)["status"] == "TIMINGS_UNAVAILABLE"
    run, jobs = sample()
    jobs["jobs"][1]["completed_at"] = "2026-11-06T09:02:20Z"
    assert ci.audit_run(run, jobs, SHA)["status"] == "TIMINGS_UNAVAILABLE"


def test_run_created_after_jobs_is_invalid():
    run, jobs = sample()
    run["created_at"] = "2026-11-06T11:00:00Z"
    assert ci.audit_run(run, jobs, SHA)["status"] == "RUN_TIMING_INVALID"
