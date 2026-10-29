"""Post-single-writer-mutex provider breaker for queued engineering workers.

Read-only authenticated GitHub evidence. No provider/network invocation and no
branch writes or probability/trading authority. A stale dispatcher decision
must never permit a new provider attempt after the preceding worker failed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from typing import Any, Callable

try:
    from .engineering_provider_failover import classify_provider_failure
except ImportError:
    from engineering_provider_failover import classify_provider_failure

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
WORKFLOWS = {
    "openai": ("wow-v17-chatgpt-engineering-worker.yml",
               "WOW V17 ChatGPT engineering team"),
    "anthropic": ("wow-v17-claude-engineering-worker.yml",
                  "WOW V17 Claude fallback engineering team"),
}
AGENT_STEP = "Engineering Lead agent"


def admission(provider: str, run_id: int, read: Callable[[list[str]], str],
              *, run_attempt: int = 1, now: dt.datetime | None = None) -> dict[str, Any]:
    def held(code: str) -> dict[str, Any]:
        return {"status": "HELD", "code": code, "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER"}

    if (provider not in WORKFLOWS or type(run_id) is not int or run_id <= 0
            or type(run_attempt) is not int or not 1 <= run_attempt <= 10):
        return held("PROVIDER_WORKER_IDENTITY_INVALID")
    workflow, job_name = WORKFLOWS[provider]
    clock = now or dt.datetime.now(dt.timezone.utc)
    if clock.tzinfo is None:
        return held("PROVIDER_WORKER_CLOCK_INVALID")
    seen: set[int] = set()
    expected_total: int | None = None
    count = 0
    current_seen = False
    # Bounded pagination, never infer first-use from a truncated run inventory.
    for page in range(1, 51):
        try:
            raw = json.loads(read([
                "gh", "api",
                f"repos/{REPO}/actions/workflows/{workflow}/runs?per_page=100&page={page}",
            ]))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return held("PROVIDER_WORKER_INVENTORY_UNVERIFIED")
        if not isinstance(raw, dict) or type(raw.get("total_count")) is not int:
            return held("PROVIDER_WORKER_INVENTORY_UNVERIFIED")
        total, entries = raw["total_count"], raw.get("workflow_runs")
        if total < 0 or not isinstance(entries, list) or len(entries) != min(100, total-count):
            return held("PROVIDER_WORKER_INVENTORY_GAP_OR_DRIFT")
        if expected_total is None:
            expected_total = total
        if total != expected_total:
            return held("PROVIDER_WORKER_INVENTORY_GAP_OR_DRIFT")
        for run in entries:
            if not isinstance(run, dict) or type(run.get("id")) is not int:
                return held("PROVIDER_WORKER_RUN_INVALID")
            ident = run["id"]
            if ident in seen:
                return held("PROVIDER_WORKER_RUN_DUPLICATED")
            seen.add(ident)
            is_self = ident == run_id
            if is_self:
                current_seen = True
                if (run.get("status") not in ("queued", "pending", "in_progress")
                        or run.get("run_attempt") != run_attempt):
                    return held("PROVIDER_WORKER_SELF_INVALID")
            elif run.get("status") != "completed":
                return held("PROVIDER_WORKER_OTHER_INFLIGHT")
            attempt = run.get("run_attempt")
            if type(attempt) is not int or not 1 <= attempt <= 10:
                return held("PROVIDER_WORKER_ATTEMPT_INVALID")
            finished = attempt - 1 if is_self else attempt
            for number in range(finished, 0, -1):
                try:
                    jobs = json.loads(read([
                        "gh", "api",
                        f"repos/{REPO}/actions/runs/{ident}/attempts/{number}/jobs?per_page=100",
                    ]))
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    return held("PROVIDER_WORKER_JOB_EVIDENCE_UNAVAILABLE")
                if not isinstance(jobs, dict) or type(jobs.get("total_count")) is not int:
                    return held("PROVIDER_WORKER_JOB_EVIDENCE_INVALID")
                rows = jobs.get("jobs")
                if jobs["total_count"] != 1 or not isinstance(rows, list) or len(rows) != 1:
                    return held("PROVIDER_WORKER_JOB_EVIDENCE_INVALID")
                job = rows[0]
                if (not isinstance(job, dict) or job.get("run_id") != ident
                        or job.get("name") != job_name or job.get("status") != "completed"):
                    return held("PROVIDER_WORKER_JOB_PROVENANCE_INVALID")
                steps = job.get("steps")
                if not isinstance(steps, list):
                    return held("PROVIDER_WORKER_STEPS_INVALID")
                lead = [step for step in steps if isinstance(step, dict)
                        and step.get("name") == AGENT_STEP]
                if len(lead) != 1:
                    return held("PROVIDER_WORKER_AGENT_STEP_INVALID")
                outcome = lead[0].get("conclusion")
                if outcome == "skipped":
                    continue
                if outcome not in ("success", "failure"):
                    return held("PROVIDER_WORKER_AGENT_STEP_UNKNOWN")
                # A successful agent is not a healthy run if subsequent
                # validation or delivery failed.
                if job.get("conclusion") == "success" and outcome == "success":
                    if not current_seen:
                        return held("PROVIDER_WORKER_CURRENT_RUN_UNVERIFIED")
                    return {"status": "ADMITTED", "code": "PREVIOUS_WORKER_HEALTHY",
                            "can_execute": False}
                stamp = lead[0].get("completed_at") or job.get("completed_at")
                try:
                    completed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    if completed.tzinfo is None:
                        raise ValueError("naive timestamp")
                    age = (clock-completed).total_seconds()
                except (ValueError, TypeError, AttributeError):
                    return held("PROVIDER_WORKER_FAILURE_TIME_INVALID")
                if age < 0:
                    return held("PROVIDER_WORKER_FUTURE_FAILURE")
                try:
                    logs = read([
                        "gh", "run", "view", str(ident), "--repo", REPO,
                        "--attempt", str(number), "--log-failed",
                    ])
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    return held("PROVIDER_WORKER_FAILURE_LOG_UNVERIFIED")
                finding = classify_provider_failure(provider, logs)
                if (finding.code.startswith(("OPENAI_API_", "ANTHROPIC_API_"))
                        and age < 120 * 60):
                    return held("PROVIDER_WORKER_CIRCUIT_BREAKER:"+finding.code)
                if finding.code.startswith(("OPENAI_API_", "ANTHROPIC_API_")):
                    if not current_seen:
                        return held("PROVIDER_WORKER_CURRENT_RUN_UNVERIFIED")
                    return {"status": "ADMITTED", "code": "PROVIDER_COOLDOWN_EXPIRED_PROBE",
                            "can_execute": False}
                # Unclassified, policy and code faults need a typed repair,
                # not an automatic provider retry.
                return held("PROVIDER_WORKER_FAILED_REVIEW_REQUIRED:"+finding.code)
        count += len(entries)
        if count == total:
            if not current_seen:
                return held("PROVIDER_WORKER_CURRENT_RUN_UNVERIFIED")
            return {"status": "ADMITTED", "code": "NO_PREVIOUS_PROVIDER_INVOCATION",
                    "can_execute": False}
    return held("PROVIDER_WORKER_INVENTORY_PAGE_CAP")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=sorted(WORKFLOWS), required=True)
    args = parser.parse_args()
    if os.getenv("GITHUB_REPOSITORY") != REPO or os.getenv("GITHUB_REF") != "refs/heads/main":
        print(json.dumps({"status": "HELD", "code": "PROVIDER_WORKER_REF_INVALID",
                          "can_execute": False}))
        return 1
    rid = os.getenv("GITHUB_RUN_ID", "")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")
    if (not rid.isdecimal() or not attempt.isdecimal()
            or not 1 <= int(attempt) <= 10 or not os.getenv("GH_TOKEN")):
        print(json.dumps({"status": "HELD", "code": "PROVIDER_WORKER_AUTH_INVALID",
                          "can_execute": False}))
        return 1

    def read(command: list[str]) -> str:
        completed = subprocess.run(command, text=True, capture_output=True,
                                   check=False, timeout=25)
        if completed.returncode or len(completed.stdout) > 1048576:
            raise ValueError("PROVIDER_WORKER_API_UNVERIFIED")
        return completed.stdout

    result = admission(args.provider, int(rid), read, run_attempt=int(attempt))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ADMITTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
