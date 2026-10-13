"""Evidence-only GitHub Actions PostgreSQL registry-throttle retry (#1693).

One same-run, same-head, failed-jobs-only retry. No status fabrication, no
release/merge/deploy action, no fallback to old SHA, no executed-test retry.
Protected workflow integration must be independently approved.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
WORKFLOWS = {
    "wow-engine-verify": ".github/workflows/wow-engine-verify.yml",
    "wow-verify": ".github/workflows/wow-verify.yml",
}
AGGREGATES = frozenset({
    "WOW governed probability backend",
    "WOW required-three regression",
    "WOW additional required regression",
})
POSTGRES_JOBS = frozenset({
    "WOW required-three collection contract",
    "WOW required-three shard / gate-engine",
    "WOW required-three shard / services",
    "WOW required-three shard / application",
    "WOW additional shard / kalshi",
    "WOW additional shard / validation",
    "WOW durable Agent Runtime integration",
    "WOW governed backend shard / 0",
    "WOW governed backend shard / 1",
    "WOW governed backend shard / 2",
    "WOW governed backend shard / 3",
})
SHA = re.compile(r"^[a-f0-9]{40}$")
RATE_LIMIT = re.compile(r"\btoomanyrequests:\s*(?:Data limit exceeded|Rate exceeded)\b", re.I)
PULL = "docker pull public.ecr.aws/docker/library/postgres:16"
MAX_LOG_BYTES = 2_000_000
MAX_JOBS = 60
MAX_IMAGE_FAILURES = 4


class RetryBlocked(RuntimeError):
    """Bounded, typed refusal; required workflow remains red."""


@dataclass(frozen=True)
class Verdict:
    state: str
    reason: str
    run_id: int | None
    head_sha: str | None
    failed_job_ids: tuple[int, ...] = ()

    def receipt(self) -> dict[str, Any]:
        return {
            "state": self.state, "reason": self.reason,
            "run_id": self.run_id, "head_sha": self.head_sha,
            "failed_job_ids": list(self.failed_job_ids),
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }


def classify(
    event: dict[str, Any],
    jobs_response: dict[str, Any],
    fetch_job_log: Callable[[int], str],
) -> Verdict:
    if not isinstance(event, dict):
        raise RetryBlocked("EVENT_INVALID")
    run = event.get("workflow_run")
    if not isinstance(run, dict):
        raise RetryBlocked("WORKFLOW_RUN_MISSING")
    rid, sha = run.get("id"), run.get("head_sha")
    if type(rid) is not int or rid <= 0 or not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise RetryBlocked("RUN_IDENTITY_INVALID")
    name = run.get("name")
    if (name not in WORKFLOWS or run.get("path") != WORKFLOWS[name]
            or (event.get("repository") or {}).get("full_name") != REPO
            or (run.get("head_repository") or {}).get("full_name") != REPO):
        raise RetryBlocked("WORKFLOW_PROVENANCE_INVALID")
    if run.get("status") != "completed":
        raise RetryBlocked("WORKFLOW_NOT_COMPLETED")
    if run.get("conclusion") != "failure":
        return Verdict("NO_ACTION", "WORKFLOW_NOT_FAILED", rid, sha)
    attempt = run.get("run_attempt")
    if type(attempt) is not int or attempt < 1:
        raise RetryBlocked("RUN_ATTEMPT_UNVERIFIED")
    if attempt != 1:
        return Verdict("RETRY_EXHAUSTED", "ONE_RETRY_MAXIMUM", rid, sha)
    if run.get("event") not in ("pull_request", "push"):
        raise RetryBlocked("WORKFLOW_TRIGGER_UNTRUSTED")
    if not isinstance(jobs_response, dict) or type(jobs_response.get("total_count")) is not int:
        raise RetryBlocked("JOBS_RESPONSE_INVALID")
    jobs = jobs_response.get("jobs")
    if (not isinstance(jobs, list) or len(jobs) > MAX_JOBS
            or jobs_response["total_count"] != len(jobs)):
        raise RetryBlocked("JOB_INVENTORY_TRUNCATED")
    failures = [j for j in jobs if isinstance(j, dict) and j.get("conclusion") == "failure"]
    if not failures:
        raise RetryBlocked("FAILED_RUN_WITHOUT_FAILED_JOBS")
    if any(not isinstance(j, dict) for j in jobs):
        raise RetryBlocked("JOB_RECORD_INVALID")
    image_ids: list[int] = []
    for job in failures:
        job_id, job_name = job.get("id"), job.get("name")
        if type(job_id) is not int or job_id <= 0 or job.get("status") != "completed":
            raise RetryBlocked("JOB_IDENTITY_INVALID")
        if job_name in AGGREGATES:
            continue
        if job_name not in POSTGRES_JOBS:
            raise RetryBlocked("NON_INFRA_FAILURE:" + str(job_name)[:80])
        # Infrastructure service startup occurs BEFORE any executable user
        # step. A test failure, even if it prints a matching string, is never
        # an eligible registry throttle.
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise RetryBlocked("JOB_STEPS_UNVERIFIED:" + job_name)
        infra_failure = [
            step for step in steps
            if isinstance(step, dict) and step.get("name") == "Initialize containers"
            and step.get("status") == "completed" and step.get("conclusion") == "failure"
        ]
        if len(infra_failure) != 1:
            raise RetryBlocked("CONTAINER_INITIALIZATION_FAILURE_MISSING:" + job_name)
        runner_steps = {"Set up job", "Initialize containers",
                        "Stop containers", "Complete job"}
        if any(
            not isinstance(step, dict)
            or (step.get("name") not in runner_steps
                and step.get("conclusion") != "skipped")
            or (step.get("name") in runner_steps
                and step.get("conclusion") not in ("success", "failure", "skipped"))
            for step in steps
        ):
            raise RetryBlocked("EXECUTED_TEST_OR_MALFORMED_STEPS:" + job_name)
        log = fetch_job_log(job_id)
        if not isinstance(log, str) or len(log.encode("utf-8")) > MAX_LOG_BYTES:
            raise RetryBlocked("JOB_LOG_MISSING_OR_OVERSIZED")
        if PULL not in log or not RATE_LIMIT.search(log):
            raise RetryBlocked("THROTTLE_EVIDENCE_MISSING:" + job_name)
        if "Docker pull failed with exit code 1" not in log:
            raise RetryBlocked("DOCKER_SERVICE_START_FAILURE_UNVERIFIED")
        image_ids.append(job_id)
    if not image_ids or len(image_ids) > MAX_IMAGE_FAILURES:
        raise RetryBlocked("IMAGE_FAILURE_COUNT_INVALID")
    return Verdict("RETRY_ALLOWED", "CI_INFRA_IMAGE_PULL_RATE_LIMIT", rid, sha, tuple(image_ids))


def verify_live_run(event_run: dict[str, Any], live: dict[str, Any]) -> None:
    """Reject racey reruns, changed revisions and mismatched GitHub evidence."""
    if not isinstance(live, dict):
        raise RetryBlocked("CURRENT_RUN_UNREADABLE")
    for key in ("id", "head_sha", "run_attempt", "status", "conclusion", "path", "event"):
        if live.get(key) != event_run.get(key):
            raise RetryBlocked("RUN_MOVED_OR_RETRIED:" + key)
    if (live.get("status") != "completed" or
            type(live.get("run_attempt")) is not int or live["run_attempt"] < 1):
        raise RetryBlocked("RUN_NOT_COMPLETED_OR_ATTEMPT_INVALID")


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], check=False, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=35)
    if result.returncode:
        raise RetryBlocked("GITHUB_API_OPERATION_FAILED")
    if len(result.stdout) > MAX_LOG_BYTES:
        raise RetryBlocked("GITHUB_EVIDENCE_OVERSIZED")
    return result.stdout.decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        with open(args.event, encoding="utf-8") as fh:
            event = json.load(fh)
        run = event.get("workflow_run") if isinstance(event, dict) else None
        if not isinstance(run, dict) or type(run.get("id")) is not int:
            raise RetryBlocked("WORKFLOW_RUN_MISSING")
        if not os.getenv("GH_TOKEN"):
            raise RetryBlocked("GH_TOKEN_MISSING")
        rid = run["id"]
        def require_current_run() -> None:
            live = json.loads(gh("api", f"repos/{REPO}/actions/runs/{rid}"))
            verify_live_run(run, live)
        require_current_run()
        raw = gh("api", f"repos/{REPO}/actions/runs/{rid}/jobs?per_page=100&filter=latest")
        jobs = json.loads(raw)
        result = classify(event, jobs, lambda job_id: gh("run", "view",
                          "--repo", REPO, "--job", str(job_id), "--log"))
        receipt = result.receipt()
        if args.execute and result.state == "RETRY_ALLOWED":
            # Recheck immediately before the single mutation: a concurrent
            # operator retry must fail closed rather than duplicate work.
            require_current_run()
            gh("api", "--method", "POST",
               f"repos/{REPO}/actions/runs/{rid}/rerun-failed-jobs")
            receipt["state"] = "RETRY_DISPATCHED"
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (RetryBlocked, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        why = str(exc) if isinstance(exc, RetryBlocked) else "EVIDENCE_UNAVAILABLE"
        print(json.dumps({
            "state": "BLOCKED_WITH_EXACT_REASON", "reason": why, "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
