"""Resident, fail-closed trigger for the existing protected-main engineering worker.

This is a supervisor, not a second implementation agent. The existing GitHub
Actions engineering workflow owns diagnosis, code changes, tests and PR creation.
The supervisor never merges, deploys, scores sport, or grants verification.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import urllib.request
from pathlib import Path
from typing import Any

from v17.engineering_dispatch_queue import _validate_manifest

LOG = logging.getLogger("wow.v17.engineering_resident_supervisor")
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
PROVIDER_WORKFLOW = "wow-v17-engineering-provider-dispatcher.yml"
ACTIVE_WORKFLOWS = frozenset({
    "wow-v17-chatgpt-engineering-worker",
    "wow-v17-claude-engineering-worker",
    "wow-v17-engineering-provider-dispatcher",
})
MANIFEST = Path(__file__).with_name("engineering_dispatch_manifest.json")
LOCK_KEY = "wow:v17:engineering:resident-dispatch-lock:v1"
COOLDOWN_KEY = "wow:v17:engineering:resident-dispatch-cooldown:v1"
ATTEMPT_KEY_PREFIX = "wow:v17:engineering:resident-dispatch-attempts:v1:"
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


class GitHubTransport:
    """Read GitHub issue/workflow state and dispatch an existing trusted workflow."""

    def __init__(self, token: str, repo: str = REPO) -> None:
        if not token:
            raise ValueError("GITHUB_DISPATCH_TOKEN_MISSING")
        self.token, self.repo = token, repo

    def call(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/") or "//" in path or not path.startswith(f"/repos/{self.repo}/"):
            raise ValueError("UNAPPROVED_GITHUB_API_PATH")
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "WOW-V17-Resident-Engineering-Supervisor",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            "https://api.github.com" + path, data=data, headers=headers,
            method="POST" if data is not None else "GET",
        )
        # Do not log URLs with query strings or secret-bearing transport errors.
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}

    def get(self, suffix: str) -> Any:
        return self.call(f"/repos/{self.repo}/{suffix}")

    def dispatch(self, issue: dict[str, Any]) -> None:
        group = str(issue.get("lease_group") or "GLOBAL")
        incident = str(issue["issue_number"])
        if issue.get("severity") == "P0":
            if group == "GLOBAL":
                raise ValueError("P0_DOMAIN_LEASE_MISSING")
        elif group != "GLOBAL":
            raise ValueError("STANDARD_TARGET_MUST_USE_GLOBAL_LEASE")
        # An exact P1 target must not silently degrade to generic queue selection.
        self.call(
            f"/repos/{self.repo}/actions/workflows/{PROVIDER_WORKFLOW}/dispatches",
            {"ref": "main", "inputs": {
                "force_provider": "auto",
                "reason": "RESIDENT_SUPERVISOR_NEXT_TASK",
                "target_incident": incident,
                "lease_group": group,
            }},
        )


def active_engineering_workflow(client: GitHubTransport) -> bool:
    """Fail closed when GitHub could not enumerate the complete active run set."""
    for state in ("queued", "in_progress", "waiting", "requested"):
        result = client.get(f"actions/runs?status={state}&per_page=100")
        runs = result.get("workflow_runs")
        if not isinstance(runs, list) or int(result.get("total_count", -1)) > len(runs):
            raise RuntimeError("ACTIVE_WORKFLOW_INVENTORY_INCOMPLETE")
        if any(run.get("name") in ACTIVE_WORKFLOWS for run in runs):
            return True
    return False


def next_approved_issue(
    client: GitHubTransport,
    manifest: dict[str, Any],
    *,
    exclude_issue_numbers: frozenset[int] = frozenset(),
) -> dict[str, Any] | None:
    _validate_manifest(manifest)
    entries = sorted(
        manifest["restoration"] + manifest["acceleration"],
        key=lambda item: (
            {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}[str(item["severity"]).upper()],
            int(item["priority_rank"]),
        ),
    )
    for item in entries:
        issue_number = int(item["issue_number"])
        if issue_number in exclude_issue_numbers:
            continue
        result = client.get(f"issues/{issue_number}")
        if result.get("state") == "open" and "pull_request" not in result:
            return item
    return None


def pending_pr_for_issue(client: GitHubTransport, issue_number: int) -> bool:
    result = client.get("pulls?state=open&per_page=100")
    # GitHub returns a list for this endpoint; our transport also supports lists.
    if not isinstance(result, list):
        raise RuntimeError("OPEN_PR_INVENTORY_INVALID")
    if len(result) == 100:
        raise RuntimeError("OPEN_PR_INVENTORY_NOT_EXHAUSTIVE")
    import re
    pattern = re.compile(r"(?im)^Incident:\s*(?:\x60)?#?" + re.escape(str(issue_number)) + r"(?:\x60)?\s*$")
    return any(pattern.search(str(pr.get("body") or "")) for pr in result)


def dispatch_once(client: GitHubTransport, redis_client: Any, manifest: dict[str, Any]) -> str:
    """Atomic admission across resident replicas; GitHub worker remains the writer."""
    if not redis_client.set(LOCK_KEY, socket.gethostname(), nx=True, ex=180):
        return "ANOTHER_RESIDENT_SUPERVISOR_OWNS_LEASE"
    if redis_client.exists(COOLDOWN_KEY):
        return "RECENT_DISPATCH_COOLDOWN"
    if active_engineering_workflow(client):
        return "EXISTING_ENGINEERING_WORKFLOW_ACTIVE"
    # An incident already at PR/review or retry cap must not starve every
    # other approved incident. Keep one shared writer and do not duplicate PRs.
    skipped: set[int] = set()
    has_pending_pr = False
    has_attempt_cap = False
    while True:
        issue = next_approved_issue(
            client, manifest, exclude_issue_numbers=frozenset(skipped),
        )
        if issue is None:
            if has_attempt_cap:
                return "DISPATCH_ATTEMPT_CAP_REQUIRES_TRIAGE"
            if has_pending_pr:
                return "AWAITING_EXISTING_PR_REVIEW_OR_REPAIR"
            return "NO_APPROVED_OPEN_ENGINEERING_TASK"
        issue_number = int(issue["issue_number"])
        if pending_pr_for_issue(client, issue_number):
            has_pending_pr = True
            skipped.add(issue_number)
            continue
        # Prevent repeated failed agent invocations from burning quota all day.
        # The existing worker owns bounded repair attempts inside each run.
        attempts_key = ATTEMPT_KEY_PREFIX + str(issue_number)
        if int(redis_client.get(attempts_key) or 0) >= 3:
            has_attempt_cap = True
            skipped.add(issue_number)
            continue
        client.dispatch(issue)
        redis_client.incr(attempts_key)
        redis_client.expire(attempts_key, 86400)
        redis_client.set(COOLDOWN_KEY, str(issue_number), ex=900)
        return "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"


def _enabled() -> bool:
    return os.getenv("WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED", "0") == "1"


def supervisor_runtime_status() -> dict[str, Any]:
    """Expose activation truth without exposing a token or credential."""
    enabled = _enabled()
    token_present = bool(os.getenv("WOW_ENGINEERING_GITHUB_TOKEN", "").strip())
    redis_present = bool(os.getenv("REDIS_URL", "").strip())
    safe = (
        os.getenv("WOW_CAN_EXECUTE", "false").strip().lower() == "false"
        and os.getenv("WOW_DRY_RUN_ONLY", "true").strip().lower() == "true"
    )
    status = "DISABLED" if not enabled else ("CONFIGURED_UNVERIFIED" if token_present and redis_present and safe else "BLOCKED")
    return {
        "status": status,
        "enabled": enabled,
        "token_configured": token_present,
        "redis_configured": redis_present,
        "governance_pass": safe,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "merge_authority": False,
        "deploy_authority": False,
    }


def run_resident_supervisor(stop: threading.Event = _STOP) -> None:
    status = supervisor_runtime_status()
    if status["status"] != "CONFIGURED_UNVERIFIED":
        LOG.warning("WOW_RESIDENT_SUPERVISOR status=%s can_execute=false", status["status"])
        return
    from redis import Redis

    client = GitHubTransport(os.getenv("WOW_ENGINEERING_GITHUB_TOKEN", ""))
    interval = 300
    while not stop.is_set():
        try:
            manifest = json.loads(MANIFEST.read_text())
            with Redis.from_url(os.environ["REDIS_URL"]) as redis_client:
                outcome = dispatch_once(client, redis_client, manifest)
            LOG.warning("WOW_RESIDENT_SUPERVISOR outcome=%s can_execute=false", outcome)
        except Exception as exc:
            LOG.warning(
                "WOW_RESIDENT_SUPERVISOR outcome=BLOCKED error_type=%s can_execute=false",
                type(exc).__name__,
            )
        if stop.wait(interval):
            break


def install_celery_worker_hooks() -> None:
    from celery.signals import worker_ready, worker_shutdown

    @worker_ready.connect(weak=False)
    def _start(**_: object) -> None:
        global _THREAD
        if _enabled() and (_THREAD is None or not _THREAD.is_alive()):
            _STOP.clear()
            _THREAD = threading.Thread(
                target=run_resident_supervisor, name="wow-engineering-supervisor",
                daemon=True,
            )
            _THREAD.start()

    @worker_shutdown.connect(weak=False)
    def _stop(**_: object) -> None:
        _STOP.set()
