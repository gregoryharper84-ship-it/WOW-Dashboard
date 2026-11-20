"""Read-only, fail-closed GitHub Actions timing audit for WOW V17.

Usage:
  python scripts/wow_ci_timing.py --run-json run.json --jobs-json jobs.json \
      --expected-head <40-character SHA>

Fetch run and *all pages* of jobs from GitHub using a separately authenticated
client. This utility neither queries the network nor changes CI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _seconds(start: Any, end: Any) -> float | None:
    a, b = _utc(start), _utc(end)
    if a is None or b is None or b < a:
        return None
    return round((b - a).total_seconds(), 3)


def audit_run(run: dict[str, Any], jobs_page: dict[str, Any], expected_head: str) -> dict[str, Any]:
    """Return typed evidence; never turn incomplete timestamps into PASS."""
    output: dict[str, Any] = {
        "run_id": run.get("id"),
        "workflow": run.get("name"),
        "expected_head": expected_head,
        "observed_head": run.get("head_sha"),
        "status": "UNVERIFIED",
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "jobs": [],
    }
    if not SHA_RE.fullmatch(expected_head) or run.get("head_sha") != expected_head:
        output["status"] = "HEAD_SHA_MISMATCH"
        return output
    if not isinstance(run.get("id"), int) or isinstance(run.get("id"), bool):
        output["status"] = "RUN_ID_UNRESOLVED"
        return output
    if run.get("status") != "completed" or run.get("conclusion") is None:
        output["status"] = "RUN_NOT_TERMINAL"
        return output
    jobs = jobs_page.get("jobs")
    total = jobs_page.get("total_count")
    if (not isinstance(jobs, list) or not isinstance(total, int) or isinstance(total, bool)
            or len(jobs) != total or total == 0):
        output["status"] = "JOBS_PAGE_INCOMPLETE"
        return output
    if any(not isinstance(job, dict) or job.get("run_id") not in (None, run["id"])
           for job in jobs):
        output["status"] = "JOB_RUN_ID_MISMATCH"
        return output
    if any(job.get("status") != "completed" or job.get("conclusion") is None for job in jobs):
        output["status"] = "JOBS_NOT_TERMINAL"
        return output
    first, last = [], []
    missing = []
    for job in jobs:
        start, end = _utc(job.get("started_at")), _utc(job.get("completed_at"))
        duration = _seconds(job.get("started_at"), job.get("completed_at"))
        if start is None or end is None or duration is None:
            missing.append(job.get("name", "unnamed"))
        else:
            first.append(start)
            last.append(end)
        steps = job.get("steps")
        step_rows: list[dict[str, Any]] = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_rows.append({"name": step.get("name"),
                                  "duration_seconds": _seconds(step.get("started_at"), step.get("completed_at"))})
        output["jobs"].append({"name": job.get("name"), "conclusion": job.get("conclusion"),
                               "duration_seconds": duration, "steps": step_rows})
    if missing:
        output["status"] = "TIMINGS_UNAVAILABLE"
        output["missing_timing_jobs"] = missing
        return output
    created = _utc(run.get("created_at"))
    if created is None or min(first) < created:
        output["status"] = "RUN_TIMING_INVALID"
        return output
    output["queue_seconds"] = round((min(first) - created).total_seconds(), 3)
    output["execution_wall_seconds"] = round((max(last) - min(first)).total_seconds(), 3)
    output["end_to_end_seconds"] = round((max(last) - created).total_seconds(), 3)
    output["slowest_job"] = max(output["jobs"], key=lambda x: x["duration_seconds"])["name"]
    output["status"] = "MEASURED" if run["conclusion"] == "success" and all(
        job["conclusion"] == "success" for job in jobs
    ) else "MEASURED_WITH_FAILURE"
    output["step_timing_coverage"] = (
        "COMPLETE" if all(j["steps"] and all(s["duration_seconds"] is not None for s in j["steps"])
                          for j in output["jobs"]) else "PARTIAL_OR_UNAVAILABLE"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-json", type=Path, required=True)
    parser.add_argument("--jobs-json", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    result = audit_run(json.loads(args.run_json.read_text()),
                       json.loads(args.jobs_json.read_text()), args.expected_head)
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "MEASURED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
