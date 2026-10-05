"""Deterministic CI closure classification for WOW engineering repair PRs.

This module classifies repository/CI state only. It never changes sporting
probability behavior and never grants merge, deploy, publication, or execution
authority.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CI_GREEN = "CI_GREEN"
CI_PENDING = "CI_PENDING"
CI_CAPACITY_STARVATION = "CI_CAPACITY_STARVATION"
CI_JOB_CANCELLED_BEFORE_START = "CI_JOB_CANCELLED_BEFORE_START"
CI_REQUIRED_GATE_FAILED = "CI_REQUIRED_GATE_FAILED"

_RERUN = "RERUN_FAILED_JOBS_ONCE"
_WAIT = "WAIT_AND_RECHECK"
_INSPECT = "INSPECT_REQUIRED_GATE_FAILURE"
_NONE = "NONE"


def _aware(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bad_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {"success", "neutral", "skipped"}
    return [
        job
        for job in jobs
        if str(job.get("conclusion") or "").lower() not in allowed
        and str(job.get("status") or "").lower() == "completed"
    ]


def _cancelled_before_start(job: dict[str, Any]) -> bool:
    if str(job.get("conclusion") or "").lower() != "cancelled":
        return False
    steps = job.get("steps")
    return not isinstance(steps, list) or len(steps) == 0


def classify_required_run(
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    starvation_seconds: int = 600,
) -> dict[str, Any]:
    """Classify one exact-head required workflow without conflating code and CI failures."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    attempt = int(run.get("run_attempt") or 1)

    if status == "completed" and conclusion == "success":
        return {
            "classification": CI_GREEN,
            "recommended_action": _NONE,
            "retry_allowed": False,
            "run_attempt": attempt,
        }

    if status == "queued":
        created = _aware(run.get("created_at"))
        age = max(0.0, (now - created).total_seconds()) if created else 0.0
        classification = CI_CAPACITY_STARVATION if age >= starvation_seconds else CI_PENDING
        return {
            "classification": classification,
            "recommended_action": _WAIT,
            "retry_allowed": False,
            "run_attempt": attempt,
            "queued_age_seconds": int(age),
        }

    if status in {"in_progress", "requested", "waiting", "pending"}:
        return {
            "classification": CI_PENDING,
            "recommended_action": _WAIT,
            "retry_allowed": False,
            "run_attempt": attempt,
        }

    if status == "completed" and conclusion in {"failure", "cancelled", "timed_out"}:
        bad = _bad_jobs(jobs)
        if bad and all(_cancelled_before_start(job) for job in bad):
            retry_allowed = attempt < 2
            return {
                "classification": CI_JOB_CANCELLED_BEFORE_START,
                "recommended_action": _RERUN if retry_allowed else _WAIT,
                "retry_allowed": retry_allowed,
                "run_attempt": attempt,
                "bad_jobs": [str(job.get("name") or "") for job in bad],
            }
        return {
            "classification": CI_REQUIRED_GATE_FAILED,
            "recommended_action": _INSPECT,
            "retry_allowed": False,
            "run_attempt": attempt,
            "bad_jobs": [str(job.get("name") or "") for job in bad],
        }

    return {
        "classification": CI_PENDING,
        "recommended_action": _WAIT,
        "retry_allowed": False,
        "run_attempt": attempt,
    }


def objective_progress_signals(
    pr: dict[str, Any],
    checks: list[dict[str, Any]],
) -> list[str]:
    """Return durable engineering progress signals without claiming closure."""
    signals: list[str] = []
    if str(pr.get("state") or "").lower() == "open":
        signals.append("PR_CREATED")
    head_sha = str(((pr.get("head") or {}).get("sha") or "")).strip()
    base_sha = str(((pr.get("base") or {}).get("sha") or "")).strip()
    if head_sha and base_sha and head_sha != base_sha:
        signals.append("PERSISTED_HEAD_COMMIT")

    by_name = {
        str(check.get("name") or ""): (
            str(check.get("status") or "").lower(),
            str(check.get("conclusion") or "").lower(),
        )
        for check in checks
    }
    if by_name.get("WOW V17 rapid affected regression") == ("completed", "success"):
        signals.append("FOCUSED_REGRESSION_GREEN")
    if by_name.get("WOW required-three regression") == ("completed", "success"):
        signals.append("REQUIRED_THREE_GREEN")
    if by_name.get("WOW governed probability backend") == ("completed", "success"):
        signals.append("GOVERNED_BACKEND_GREEN")
    if by_name.get("Trusted exact-head engineering governance") == ("completed", "success"):
        signals.append("TRUSTED_GOVERNANCE_GREEN")
    return signals


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    classify = sub.add_parser("classify")
    classify.add_argument("--run-json", required=True)
    classify.add_argument("--jobs-json", required=True)
    classify.add_argument("--starvation-seconds", type=int, default=600)

    progress = sub.add_parser("progress")
    progress.add_argument("--pr-json", required=True)
    progress.add_argument("--checks-json", required=True)

    args = parser.parse_args()
    if args.command == "classify":
        run = _load(args.run_json)
        jobs_payload = _load(args.jobs_json)
        jobs = jobs_payload.get("jobs", jobs_payload) if isinstance(jobs_payload, dict) else jobs_payload
        print(json.dumps(classify_required_run(run, list(jobs), starvation_seconds=args.starvation_seconds), sort_keys=True))
        return 0

    pr = _load(args.pr_json)
    checks_payload = _load(args.checks_json)
    checks = checks_payload.get("check_runs", checks_payload) if isinstance(checks_payload, dict) else checks_payload
    print(json.dumps({"signals": objective_progress_signals(pr, list(checks))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
