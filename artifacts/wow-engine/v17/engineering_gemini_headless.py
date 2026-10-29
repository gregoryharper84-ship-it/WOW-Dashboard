"""Opt-in headless Gemini AMRT admission and typed results; no betting or repo writes."""
from __future__ import annotations

import datetime as dt
import argparse
import os
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

TRUE = "true"
MODEL_AUTHORITY = "V17_TERMINAL_REDUCER"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_INCIDENT = re.compile(r"^[1-9][0-9]*$")
_LEASE = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
_BLOCKED_PATHS = (
    ".github/", ".agents/", ".codex/", ".git/", ".gemini/", "GEMINI.md", "scripts/",
    "artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt",
    "artifacts/wow-engine/v17/engineering_gemini_headless.py",
)
_RECOVERY_RUNTIME_PATHS = frozenset({
    "artifacts/wow-engine/v17/engineering_gemini_headless.py",
    ".github/workflows/wow-v17-gemini-engineering-worker.yml",
    ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
})
_EXPLICIT_CODES = (
    ("GEMINI_API_QUOTA_EXHAUSTED", ("resource_exhausted", "quota exceeded", "insufficient quota", "resource exhausted")),
    ("GEMINI_API_RATE_LIMITED", ("rate limit", "429", "too many requests")),
    ("GEMINI_API_AUTH_FAILED", ("invalid api key", "api_key_invalid", "permission_denied", "unauthenticated", "403")),
    ("GEMINI_API_UPSTREAM_UNAVAILABLE", ("503", "502", "unavailable", "internal error")),
)


def admit(enabled: str, spend_confirmed: str, auto_enabled: str, mode: str) -> dict[str, Any]:
    """No API call is admitted without all explicit flags; default routing unchanged."""
    if enabled != TRUE:
        code = "GEMINI_ENGINEERING_DISABLED"
    elif spend_confirmed != TRUE:
        code = "GEMINI_SPEND_CAP_UNCONFIRMED"
    elif mode == "auto" and auto_enabled != TRUE:
        code = "GEMINI_AUTO_ROUTING_DISABLED"
    elif mode not in {"auto", "manual"}:
        code = "GEMINI_ADMISSION_MODE_INVALID"
    else:
        code = "ADMITTED"
    return {"status": "ADMITTED" if code == "ADMITTED" else "HELD", "code": code,
            "provider": "gemini", "can_execute": False, "terminal_authority": MODEL_AUTHORITY}



def recheck_auto_worker_provider(runs: Any, comparison: Any, current_sha: str,
                                 *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Recheck provider-wide Gemini recovery *inside* the existing writer mutex.

    Dispatchers may accept distinct sport leases simultaneously, but a queued
    Gemini worker is not allowed to spend a second canary/cooldown grant.
    The protected worker fetches the latest completed Gemini run at admission,
    after the previous writer finished. Unknown history is always a HOLD.
    """
    def held(code: str) -> dict[str, Any]:
        return {"status": "HELD", "code": code, "can_execute": False,
                "terminal_authority": MODEL_AUTHORITY}

    if not isinstance(current_sha, str) or not _SHA.fullmatch(current_sha):
        return held("GEMINI_WORKER_CURRENT_SHA_INVALID")
    if not isinstance(runs, list) or len(runs) > 1:
        return held("GEMINI_WORKER_HISTORY_UNVERIFIED")
    if not runs:
        return {"status": "ADMITTED", "code": "GEMINI_WORKER_FIRST_AUTO_ATTEMPT",
                "can_execute": False, "terminal_authority": MODEL_AUTHORITY}
    last = runs[0]
    if not isinstance(last, dict) or last.get("status") != "completed":
        return held("GEMINI_WORKER_HISTORY_UNVERIFIED")
    if last.get("conclusion") == "failure":
        # Preserve the existing immutable-SHA one-shot runtime-fix rule.
        return admit_one_shot_recovery(runs, comparison, current_sha)
    if last.get("conclusion") != "success":
        return held("GEMINI_WORKER_PREVIOUS_OUTCOME_UNVERIFIED")
    timestamp = last.get("updatedAt")
    if not isinstance(timestamp, str):
        return held("GEMINI_WORKER_SUCCESS_TIME_UNVERIFIED")
    try:
        finished = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        observed = now if now is not None else dt.datetime.now(dt.timezone.utc)
        if finished.tzinfo is None or observed.tzinfo is None:
            return held("GEMINI_WORKER_SUCCESS_TIME_UNVERIFIED")
        age = observed - finished
    except (ValueError, OverflowError, TypeError):
        return held("GEMINI_WORKER_SUCCESS_TIME_UNVERIFIED")
    if age < dt.timedelta(hours=24):
        return held("GEMINI_WORKER_PROVIDER_COOLDOWN_ACTIVE")
    return {"status": "ADMITTED", "code": "GEMINI_WORKER_COOLDOWN_EXPIRED",
            "can_execute": False, "terminal_authority": MODEL_AUTHORITY}



def latest_actual_provider_attempt(
    runs: Any, lookup_jobs: Any, *, current_run_id: int | None = None,
    current_run_attempt: int | None = None,
) -> dict[str, Any]:
    """Select the latest *actual* Gemini CLI invocation by immutable attempt.

    GitHub reruns preserve run ID, but increase run_attempt. Examine every
    completed earlier attempt, including earlier attempts of this same run.
    An otherwise failed/skipped cooldown run never erases a past CLI attempt.
    Job timestamps, not latest run.updatedAt, govern global chronological order.
    A capped/unknown inventory cannot grant a new provider call.
    """
    def held(code: str) -> dict[str, Any]:
        return {"status": "HELD", "code": code, "can_execute": False}

    if not isinstance(runs, list) or len(runs) > 5000:
        return held("GEMINI_PROVIDER_HISTORY_UNVERIFIED")
    if current_run_id is not None and (
        type(current_run_id) is not int or current_run_id <= 0
        or type(current_run_attempt) is not int or current_run_attempt <= 0
    ):
        return held("GEMINI_PROVIDER_CURRENT_ATTEMPT_INVALID")
    seen: set[int] = set()
    actual: list[tuple[dt.datetime, int, int, dict[str, Any]]] = []
    for run in runs:
        if not isinstance(run, dict):
            return held("GEMINI_PROVIDER_HISTORY_UNVERIFIED")
        ident, attempt = run.get("databaseId"), run.get("attempt")
        if (type(ident) is not int or ident <= 0 or ident in seen
                or type(attempt) is not int or not 1 <= attempt <= 10):
            return held("GEMINI_PROVIDER_RUN_OR_ATTEMPT_INVALID")
        seen.add(ident)
        status = run.get("status")
        is_self = ident == current_run_id
        if is_self:
            if status not in ("queued", "pending", "in_progress") or (
                attempt != current_run_attempt
            ):
                return held("GEMINI_PROVIDER_SELF_ATTEMPT_UNVERIFIED")
        elif status in ("in_progress", "waiting", "requested"):
            return held("GEMINI_PROVIDER_PREDECESSOR_NOT_TERMINAL")
        elif status not in ("completed", "queued", "pending"):
            return held("GEMINI_PROVIDER_RUN_STATUS_UNVERIFIED")
        # Never count the currently running attempt as a finished spend; do
        # inspect ALL previous attempts of this same Actions run ID.
        max_finished_attempt = attempt - 1 if is_self else (
            attempt if status == "completed" else attempt - 1
        )
        for number in range(max_finished_attempt, 0, -1):
            try:
                payload = lookup_jobs(ident, number)
            except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
                return held("GEMINI_PROVIDER_JOB_RECEIPT_UNAVAILABLE")
            if not isinstance(payload, dict) or type(payload.get("total_count")) is not int:
                return held("GEMINI_PROVIDER_JOB_RECEIPT_UNVERIFIED")
            jobs = payload.get("jobs")
            if payload["total_count"] != 1 or not isinstance(jobs, list) or len(jobs) != 1:
                return held("GEMINI_PROVIDER_JOB_RECEIPT_UNVERIFIED")
            job = jobs[0]
            if not isinstance(job, dict) or (
                job.get("run_id") != ident
                or job.get("name") != "Gemini AMRT isolated repair candidate (never a release actor)"
                or job.get("status") != "completed"
            ):
                return held("GEMINI_PROVIDER_JOB_RECEIPT_UNVERIFIED")
            # GitHub-native whole-job skip (e.g. protected-main admission
            # rejects a non-main dispatch) definitively cannot spend Gemini.
            # Do not let a skipped branch dispatch permanently poison routing.
            if job.get("conclusion") == "skipped":
                continue
            steps = job.get("steps")
            if not isinstance(steps, list):
                return held("GEMINI_PROVIDER_STEP_RECEIPT_UNVERIFIED")
            cli_steps = [
                step for step in steps if isinstance(step, dict)
                and step.get("name") == "Execute one bounded headless Gemini CLI candidate session"
            ]
            if len(cli_steps) != 1:
                return held("GEMINI_PROVIDER_STEP_RECEIPT_UNVERIFIED")
            outcome = cli_steps[0].get("conclusion")
            if outcome == "skipped":
                continue  # Authenticated no-spend receipt; do not reset cooldown.
            if outcome not in ("success", "failure"):
                return held("GEMINI_PROVIDER_STEP_OUTCOME_UNVERIFIED")
            job_outcome = job.get("conclusion")
            if job_outcome not in ("success", "failure", "cancelled", "timed_out"):
                return held("GEMINI_PROVIDER_JOB_OUTCOME_UNVERIFIED")
            # CLI transport success is not proof of a healthy canary: the
            # candidate inspection, tests or security fence may fail later.
            # Retain provider-spend timestamp, but classify a failed job as
            # FAILED, requiring a reviewed runtime change before another try.
            effective_outcome = (
                "success" if outcome == "success" and job_outcome == "success"
                else "failure"
            )
            completed_at = cli_steps[0].get("completed_at") or job.get("completed_at")
            if not isinstance(completed_at, str):
                return held("GEMINI_PROVIDER_ATTEMPT_TIME_UNVERIFIED")
            try:
                when = dt.datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    raise ValueError("naive timestamp")
            except (ValueError, OverflowError):
                return held("GEMINI_PROVIDER_ATTEMPT_TIME_UNVERIFIED")
            source_sha = run.get("headSha")
            if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
                return held("GEMINI_PROVIDER_ATTEMPT_SHA_UNVERIFIED")
            actual.append((when, ident, number, {
                "databaseId": ident, "run_attempt": number, "headSha": source_sha,
                "status": "completed", "conclusion": effective_outcome,
                "updatedAt": completed_at,
            }))
    if current_run_id is not None and current_run_id not in seen:
        return held("GEMINI_WORKER_CURRENT_RUN_MISSING")
    latest = max(actual, key=lambda v: (v[0], v[1], v[2]))[3] if actual else None
    return {"status": "VERIFIED_PROVIDER_HISTORY", "run": latest,
            "can_execute": False}



def complete_gemini_run_inventory(read: Any, repo: str, *,
                                  max_pages: int = 50) -> list[dict[str, Any]]:
    """Fetch *all* GitHub Actions runs, not the latest 100.

    GitHub's authenticated total_count and page boundaries certify pagination.
    No short page, missing field, API error, duplicate or cap can imply an
    unspent provider. A bounded 50-page ceiling remains a typed HOLD, not a
    false green. This is read-only and returns no capability token.
    """
    if not 1 <= max_pages <= 50:
        raise ValueError("GEMINI_INVENTORY_PAGE_LIMIT_INVALID")
    result: list[dict[str, Any]] = []
    expected_total: int | None = None
    for page in range(1, max_pages + 1):
        payload = json.loads(read([
            "gh", "api",
            f"repos/{repo}/actions/workflows/wow-v17-gemini-engineering-worker.yml/runs?per_page=100&page={page}",
        ]))
        if not isinstance(payload, dict) or type(payload.get("total_count")) is not int:
            raise ValueError("GEMINI_INVENTORY_TOTAL_UNVERIFIED")
        total, rows = payload["total_count"], payload.get("workflow_runs")
        if total < 0 or not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("GEMINI_INVENTORY_PAGE_INVALID")
        if expected_total is None:
            expected_total = total
        if total != expected_total or len(rows) != min(100, total - len(result)):
            raise ValueError("GEMINI_INVENTORY_DRIFT_OR_GAP")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("GEMINI_INVENTORY_ROW_INVALID")
            result.append({
                "databaseId": row.get("id"),
                "attempt": row.get("run_attempt"),
                "headSha": row.get("head_sha"),
                "status": row.get("status"),
                "conclusion": row.get("conclusion"),
                "updatedAt": row.get("updated_at"),
            })
        if len(result) == total:
            identities = [row["databaseId"] for row in result]
            if len(identities) != len(set(identities)):
                raise ValueError("GEMINI_INVENTORY_DUPLICATED_RUN")
            return result
    raise ValueError("GEMINI_INVENTORY_PAGE_CAP_UNVERIFIED")


def read_provider_history_github() -> dict[str, Any]:
    """The canonical dispatcher uses the same authenticated attempt selector."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo != "gregoryharper84-ship-it/WOW-Dashboard" or not os.environ.get("GH_TOKEN"):
        return {"status": "HELD", "code": "GEMINI_PROVIDER_IDENTITY_UNVERIFIED",
                "can_execute": False}

    def read(args: list[str]) -> str:
        result = subprocess.run(args, capture_output=True, text=True,
                                check=False, timeout=20)
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise ValueError("GEMINI_PROVIDER_API_UNVERIFIED")
        return result.stdout.strip()

    try:
        runs = complete_gemini_run_inventory(read, repo)
        return latest_actual_provider_attempt(
            runs, lambda ident, attempt: json.loads(read([
                "gh", "api",
                f"repos/{repo}/actions/runs/{ident}/attempts/{attempt}/jobs?per_page=100",
            ])),
        )
    except (OSError, TypeError, ValueError, subprocess.TimeoutExpired,
            json.JSONDecodeError):
        return {"status": "HELD", "code": "GEMINI_PROVIDER_HISTORY_UNAVAILABLE",
                "can_execute": False}


def recheck_auto_worker_github(*, now: dt.datetime | None = None) -> dict[str, Any]:
    """Canonical provider recheck after acquiring the shared Actions writer lock.

    The workflow is the only owner of this invocation. Never trust an earlier
    dispatcher route/canary, or classify unreadable GitHub history as healthy.
    No GitHub write operations and no provider credentials are used here.
    """
    def held(code: str) -> dict[str, Any]:
        return {"status": "HELD", "code": code, "can_execute": False,
                "terminal_authority": MODEL_AUTHORITY}

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        return held("GEMINI_WORKER_UNPROTECTED_DISPATCH_REF")
    if repo != "gregoryharper84-ship-it/WOW-Dashboard":
        return held("GEMINI_WORKER_REPOSITORY_UNTRUSTED")
    if (not run_id.isdecimal() or int(run_id) <= 0
            or not run_attempt.isdecimal() or not 1 <= int(run_attempt) <= 10
            or not os.environ.get("GH_TOKEN")):
        return held("GEMINI_WORKER_IDENTITY_UNVERIFIED")

    def read_command(args: list[str]) -> str:
        result = subprocess.run(args, check=False, capture_output=True,
                                text=True, timeout=20)
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise ValueError("GEMINI_WORKER_GITHUB_EVIDENCE_UNVERIFIED")
        return result.stdout.strip()

    try:
        current_sha = read_command(["git", "rev-parse", "HEAD"])
        # The workflow pins checkout to github.sha; compare both independently.
        # A queued invocation is never allowed to claim a different revision.
        dispatch_sha = os.environ.get("GITHUB_SHA", "")
        if not _SHA.fullmatch(current_sha) or not _SHA.fullmatch(dispatch_sha):
            return held("GEMINI_WORKER_CURRENT_SHA_INVALID")
        if current_sha != dispatch_sha:
            return held("GEMINI_WORKER_CHECKOUT_SHA_DRIFT")
        runs = complete_gemini_run_inventory(read_command, repo)
        history = latest_actual_provider_attempt(
            runs,
            lambda candidate_id, attempt: json.loads(read_command([
                "gh", "api",
                f"repos/{repo}/actions/runs/{candidate_id}/attempts/{attempt}/jobs?per_page=100",
            ])),
            current_run_id=int(run_id),
            current_run_attempt=int(run_attempt),
        )
        if history["status"] != "VERIFIED_PROVIDER_HISTORY":
            return held(history["code"])
        last_actual = history["run"]
        selected = [last_actual] if last_actual is not None else []
        comparison: dict[str, Any] = {}
        if selected and selected[0].get("conclusion") == "failure":
            failed_sha = selected[0].get("headSha")
            if not isinstance(failed_sha, str) or not _SHA.fullmatch(failed_sha):
                return held("GEMINI_WORKER_HISTORY_UNVERIFIED")
            if failed_sha != current_sha:
                comparison = json.loads(read_command([
                    "gh", "api", f"repos/{repo}/compare/{failed_sha}...{current_sha}",
                ]))
        decision = recheck_auto_worker_provider(selected, comparison, current_sha, now=now)
        # Durable typed result saved by workflow in an exact-run Actions artifact.
        # The provider itself remains inaccessible to the recheck job.
        return dict(decision, run_id=int(run_id), head_sha=current_sha)
    except (OSError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return held("GEMINI_WORKER_GITHUB_EVIDENCE_UNVERIFIED")



def select_incident(
    manifest: dict[str, Any], issues: list[dict[str, Any]],
    *, target_incident: str = "", requested_lease: str = "GLOBAL",
) -> dict[str, Any]:
    """Existing Conductor manifest + GitHub OPEN issues remain authoritative.

    This selects *research/isolated patch work only*. It never acquires a writer
    lease or grants shared-branch write/merge authority.
    """
    try:
        from v17.engineering_dispatch_queue import build_queue, standard_conflicts_with_rapid
    except ImportError:  # direct script execution from GitHub Actions
        from engineering_dispatch_queue import build_queue, standard_conflicts_with_rapid

    if not _LEASE.fullmatch(requested_lease):
        raise ValueError("GEMINI_LEASE_GROUP_INVALID")
    if target_incident and not _INCIDENT.fullmatch(target_incident):
        raise ValueError("GEMINI_INCIDENT_ID_INVALID")
    queue = build_queue(manifest, issues)
    records = sorted(
        (r for r in queue["records"] if r["work_stream"] == "RESTORATION"),
        key=lambda r: (0 if r["severity"] == "P0" else 1, r["priority_rank"], r["issue_number"]),
    )
    if target_incident:
        records = [r for r in records if r["incident_id"] == target_incident]
    elif requested_lease != "GLOBAL":
        records = [r for r in records if r["lease_group"] == requested_lease]
    if not records:
        return {"status": "NO_ACTION", "code": "GEMINI_NO_ELIGIBLE_EXISTING_INCIDENT", "can_execute": False}
    record = records[0]
    group = record["lease_group"] or "GLOBAL"
    if target_incident and group != requested_lease:
        raise ValueError("GEMINI_INCIDENT_LEASE_GROUP_MISMATCH")
    if record["execution_lane"] == "STANDARD":
        blockers = standard_conflicts_with_rapid(queue, record["incident_id"])
        if blockers:
            return {"status": "NO_ACTION", "code": "GEMINI_STANDARD_CONFLICTS_WITH_P0", "can_execute": False}
    return {
        "status": "SELECTED", "code": "EXISTING_CONDUCTOR_RESEARCH_ONLY",
        "incident_id": record["incident_id"], "issue_number": record["issue_number"],
        "title": record["title"][:200], "lease_group": group,
        "severity": record["severity"], "execution_lane": record["execution_lane"],
        "writer_lease_granted": False, "github_branch_write_allowed": False,
        "can_execute": False, "terminal_authority": MODEL_AUTHORITY,
    }


def classify_cli(exit_code: int, stderr: str, *, timed_out: bool = False,
                 response: Any = None, source_sha: str = "") -> dict[str, Any]:
    """Classify one bounded attempt without persisting log contents or secrets."""
    # The CLI may put its *only* actionable API error in the JSON response,
    # not in stderr. Inspect it in memory, but persist only a fixed typed code.
    # Never save model text, API messages, stderr, or credential fragments.
    error = response.get("error") if isinstance(response, dict) else None
    if isinstance(error, dict):
        hints = " ".join(str(error.get(k, ""))[:2000]
                         for k in ("code", "status", "type", "message"))
    elif isinstance(error, str):
        hints = error[:4000]
    else:
        hints = ""
    value = ((stderr or "")[:8192] + " " + hints).lower()
    known = next((kind for kind, needles in _EXPLICIT_CODES
                  if any(needle in value for needle in needles)), None)
    if timed_out:
        code = "GEMINI_CLI_TIMEOUT"
    elif exit_code == 41:
        code = "GEMINI_API_AUTH_FAILED"
    elif exit_code == 52:
        code = "GEMINI_CLI_CONFIG_INVALID"
    elif exit_code == 44:
        code = "GEMINI_CLI_SANDBOX_FAILED"
    elif exit_code == 53:
        code = "GEMINI_CLI_TURN_LIMIT"
    elif exit_code == 55:
        code = "GEMINI_CLI_UNTRUSTED_WORKSPACE"
    elif exit_code == 42:
        code = "GEMINI_CLI_INPUT_INVALID"
    # Specific unsupported-model/billing refusals must precede broad
    # "unavailable" signatures used by the upstream-transient detector.
    elif any(s in value for s in ("billing required", "paid tier", "payment required")):
        code = "GEMINI_API_BILLING_REQUIRED"
    elif any(s in value for s in ("model not found", "model is not supported",
                                  "models/flash", "requested model unavailable")):
        code = "GEMINI_MODEL_UNAVAILABLE"
    elif known:
        code = known
    elif exit_code == 0 and isinstance(response, dict) and not error and (
        isinstance(response.get("response"), str) and response["response"].strip()
    ):
        code = "GEMINI_CLI_COMPLETE"
    elif exit_code == 0:
        code = "GEMINI_CLI_RESPONSE_MALFORMED"
    else:
        code = "GEMINI_CLI_FAILED"
    result = {"status": "COMPLETED" if code == "GEMINI_CLI_COMPLETE" else "HELD",
              "code": code, "exit_code": exit_code,
              "stderr_present": bool(stderr), "response_error_present": bool(error),
              "retry_allowed": False, "can_execute": False,
              "terminal_authority": MODEL_AUTHORITY}
    if source_sha:
        if not _SHA.fullmatch(source_sha):
            result.update(status="HELD", code="GEMINI_SOURCE_HEAD_INVALID")
        else:
            result["source_head_sha"] = source_sha
    return result


def validate_workspace(worktree: str | Path, runner_temp: str | Path,
                       main_sha: str) -> dict[str, Any]:
    """Trust only the exact isolated candidate worktree at the verified main HEAD."""
    if not isinstance(main_sha, str) or not _SHA.fullmatch(main_sha):
        return {"status": "HELD", "code": "GEMINI_WORKSPACE_MAIN_HEAD_INVALID",
                "can_execute": False}
    try:
        temp_root = Path(runner_temp).resolve(strict=True)
        candidate = Path(worktree).resolve(strict=True)
        expected = temp_root / "wow-gemini" / "candidate"
        if candidate != expected or not candidate.is_dir():
            return {"status": "HELD", "code": "GEMINI_WORKSPACE_PATH_INVALID",
                    "can_execute": False}
        head = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError):
        return {"status": "HELD", "code": "GEMINI_WORKSPACE_HEAD_UNVERIFIED",
                "can_execute": False}
    if head != main_sha:
        return {"status": "HELD", "code": "GEMINI_WORKSPACE_HEAD_MISMATCH",
                "can_execute": False}
    repository_gemini = candidate / ".gemini"
    if repository_gemini.exists() or repository_gemini.is_symlink():
        return {"status": "HELD", "code": "GEMINI_REPOSITORY_SETTINGS_NOT_TRUSTED",
                "can_execute": False}
    return {"status": "TRUSTED", "code": "GEMINI_WORKSPACE_TRUSTED",
            "can_execute": False, "terminal_authority": MODEL_AUTHORITY}


def admit_one_shot_recovery(runs: Any, comparison: Any, current_sha: str) -> dict[str, Any]:
    """Admit one retry only when the failed worker predates a reviewed runtime fix."""
    def held(code: str) -> dict[str, Any]:
        return {"status": "HELD", "code": code, "can_execute": False,
                "terminal_authority": MODEL_AUTHORITY}

    if not isinstance(current_sha, str) or not _SHA.fullmatch(current_sha):
        return held("GEMINI_RECOVERY_CURRENT_SHA_INVALID")
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        return held("GEMINI_RECOVERY_LATEST_RUN_UNVERIFIED")

    last = runs[0]
    previous_sha = last.get("headSha")
    if not isinstance(previous_sha, str) or not _SHA.fullmatch(previous_sha):
        return held("GEMINI_RECOVERY_PREVIOUS_SHA_INVALID")
    if last.get("status") != "completed" or last.get("conclusion") != "failure":
        return held("GEMINI_RECOVERY_PREVIOUS_RUN_NOT_FAILED")
    if previous_sha == current_sha:
        return held("GEMINI_RECOVERY_ALREADY_ATTEMPTED_SHA")
    if not isinstance(comparison, dict):
        return held("GEMINI_RECOVERY_COMPARE_UNAVAILABLE")

    base = comparison.get("base_commit")
    merge_base = comparison.get("merge_base_commit")
    if (comparison.get("status") != "ahead"
            or not isinstance(base, dict) or base.get("sha") != previous_sha
            or not isinstance(merge_base, dict) or merge_base.get("sha") != previous_sha):
        return held("GEMINI_RECOVERY_SHA_LINEAGE_INVALID")

    files = comparison.get("files")
    commits = comparison.get("total_commits")
    if (not isinstance(files, list) or not files or len(files) >= 300
            or not isinstance(commits, int) or isinstance(commits, bool)
            or commits < 1 or commits >= 250):
        return held("GEMINI_RECOVERY_DIFF_TRUNCATED_OR_INVALID")

    changed_runtime = sorted({
        item["filename"] for item in files
        if isinstance(item, dict)
        and item.get("filename") in _RECOVERY_RUNTIME_PATHS
        and item.get("status") == "modified"
    })
    if not changed_runtime:
        return held("GEMINI_RECOVERY_NO_REVIEWED_RUNTIME_CHANGE")
    return {
        "status": "RECOVERY_ADMITTED",
        "code": "GEMINI_ONE_SHOT_AFTER_REVIEWED_FIX",
        "previous_head_sha": previous_sha,
        "current_head_sha": current_sha,
        "changed_runtime_files": changed_runtime,
        "can_execute": False,
        "terminal_authority": MODEL_AUTHORITY,
    }


def inspect_changed_paths(paths: list[str]) -> dict[str, Any]:
    """Reject trust-root modifications from a generated candidate, not silently apply."""
    if not paths or len(paths) > 100 or any(not isinstance(p, str) for p in paths):
        return {"status": "HELD", "code": "GEMINI_PATCH_EMPTY_OR_UNBOUNDED", "can_execute": False}
    for path in paths:
        name = path.strip().replace("\\", "/")
        if (name != path or not name or name.startswith("/") or ".." in name.split("/")
                or any(name.startswith(prefix) for prefix in _BLOCKED_PATHS)
                or not (name.startswith("artifacts/wow-engine/v17/") and name.endswith(".py")
                        and name != "artifacts/wow-engine/v17/engineering_gemini_headless.py"
                        or name.startswith("artifacts/wow-engine/tests/")
                        and Path(name).name.startswith("test_") and name.endswith(".py"))):
            return {"status": "HELD", "code": "GEMINI_PATCH_PROTECTED_OR_INVALID_PATH",
                    "rejected_path": name[:200], "can_execute": False}
    if not any(p.startswith("artifacts/wow-engine/v17/") and p.endswith(".py")
               for p in paths):
        return {"status": "HELD", "code": "GEMINI_PATCH_SOURCE_CHANGE_REQUIRED",
                "can_execute": False}
    return {"status": "CANDIDATE_ONLY", "code": "GEMINI_ISOLATED_PATCH_UNREVIEWED",
            "files": len(paths), "can_execute": False}


def candidate_test_paths(paths: list[str]) -> list[str]:
    """Require real regression tests changed alongside the code candidate."""
    allowed = sorted(
        p for p in paths
        if p.startswith("artifacts/wow-engine/tests/")
        and p.endswith(".py") and Path(p).name.startswith("test_")
    )
    if not allowed or len(allowed) > 10:
        raise ValueError("GEMINI_CHANGED_REGRESSION_TEST_REQUIRED")
    return allowed


def verify_candidate(worktree: Path, paths: list[str]) -> dict[str, Any]:
    """Execute edited tests in isolated worktree; fail closed on any failure.

    No Gemini API key or GitHub write token is present at this step. Do not
    turn test stdout/stderr into a pass receipt; return process evidence only.
    """
    inspected = inspect_changed_paths(paths)
    if inspected["status"] != "CANDIDATE_ONLY":
        return {"status": "HELD", "code": inspected["code"], "can_execute": False}
    try:
        tests = candidate_test_paths(paths)
    except ValueError:
        return {"status": "HELD", "code": "GEMINI_CHANGED_REGRESSION_TEST_REQUIRED",
                "can_execute": False}
    if not worktree.is_dir() or not all((worktree / p).is_file() for p in tests):
        return {"status": "HELD", "code": "GEMINI_CHANGED_TEST_PATH_MISSING", "can_execute": False}
    try:
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", *tests],
            cwd=worktree, timeout=300, capture_output=True, text=True, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "HELD", "code": "GEMINI_REGRESSION_TIMEOUT", "can_execute": False}
    except OSError:
        return {"status": "HELD", "code": "GEMINI_REGRESSION_RUNNER_UNAVAILABLE", "can_execute": False}
    return {
        "status": "TESTED_CANDIDATE" if run.returncode == 0 else "HELD",
        "code": "GEMINI_REGRESSION_PASS" if run.returncode == 0 else "GEMINI_REGRESSION_FAILED",
        "test_files": tests, "exit_code": run.returncode,
        "can_execute": False, "terminal_authority": MODEL_AUTHORITY,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    admission = sub.add_parser("admit")
    admission.add_argument("--enabled", required=True)
    admission.add_argument("--spend-confirmed", required=True)
    admission.add_argument("--auto-enabled", required=True)
    admission.add_argument("--mode", choices=["manual", "auto"], required=True)
    selection = sub.add_parser("select")
    selection.add_argument("--manifest", required=True)
    selection.add_argument("--issues", required=True)
    selection.add_argument("--target", default="")
    selection.add_argument("--lease-group", default="GLOBAL")
    classify = sub.add_parser("classify")
    classify.add_argument("--exit-code", type=int, required=True)
    classify.add_argument("--stderr-file", required=True)
    classify.add_argument("--response-file", required=True)
    classify.add_argument("--source-sha", default="")
    classify.add_argument("--timed-out", action="store_true")
    recovery = sub.add_parser("recovery-admit")
    recovery.add_argument("--runs-file", required=True)
    recovery.add_argument("--comparison-file", required=True)
    recovery.add_argument("--current-sha", required=True)
    worker_recheck = sub.add_parser("worker-auto-admit")
    worker_recheck.add_argument("--runs-file", required=True)
    worker_recheck.add_argument("--comparison-file", required=True)
    worker_recheck.add_argument("--current-sha", required=True)
    sub.add_parser("worker-auto-admit-github")
    sub.add_parser("provider-history-github")
    changed = sub.add_parser("inspect-patch")
    changed.add_argument("--files-json", required=True)
    verify = sub.add_parser("verify-candidate")
    verify.add_argument("--files-json", required=True)
    verify.add_argument("--worktree", required=True)
    workspace = sub.add_parser("validate-workspace")
    workspace.add_argument("--worktree", required=True)
    workspace.add_argument("--runner-temp", required=True)
    workspace.add_argument("--main-sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "admit":
            result = admit(args.enabled, args.spend_confirmed, args.auto_enabled, args.mode)
        elif args.action == "select":
            result = select_incident(
                json.loads(Path(args.manifest).read_text(encoding="utf-8")),
                json.loads(Path(args.issues).read_text(encoding="utf-8")),
                target_incident=args.target, requested_lease=args.lease_group,
            )
        elif args.action == "classify":
            try:
                response = json.loads(Path(args.response_file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                response = None
            result = classify_cli(args.exit_code, Path(args.stderr_file).read_text(encoding="utf-8"),
                                  timed_out=args.timed_out, response=response,
                                  source_sha=args.source_sha)
        elif args.action == "recovery-admit":
            result = admit_one_shot_recovery(
                json.loads(Path(args.runs_file).read_text(encoding="utf-8")),
                json.loads(Path(args.comparison_file).read_text(encoding="utf-8")),
                args.current_sha,
            )
        elif args.action == "worker-auto-admit":
            result = recheck_auto_worker_provider(
                json.loads(Path(args.runs_file).read_text(encoding="utf-8")),
                json.loads(Path(args.comparison_file).read_text(encoding="utf-8")),
                args.current_sha,
            )
        elif args.action == "worker-auto-admit-github":
            result = recheck_auto_worker_github()
        elif args.action == "provider-history-github":
            result = read_provider_history_github()
        elif args.action == "inspect-patch":
            result = inspect_changed_paths(json.loads(Path(args.files_json).read_text(encoding="utf-8")))
        elif args.action == "validate-workspace":
            result = validate_workspace(args.worktree, args.runner_temp, args.main_sha)
        else:
            result = verify_candidate(Path(args.worktree),
                json.loads(Path(args.files_json).read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Deliberately exclude exception text; upstream API inputs may contain secrets.
        result = {"status": "HELD", "code": "GEMINI_ADMISSION_OR_EVIDENCE_INVALID",
                  "error_type": type(exc).__name__, "can_execute": False,
                  "terminal_authority": MODEL_AUTHORITY}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {
        "SELECTED", "ADMITTED", "NO_ACTION", "CANDIDATE_ONLY", "COMPLETED", "TRUSTED",
        "TESTED_CANDIDATE", "RECOVERY_ADMITTED", "VERIFIED_PROVIDER_HISTORY",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
