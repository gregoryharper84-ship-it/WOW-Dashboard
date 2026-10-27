"""Resident, fail-closed trigger for the existing protected-main engineering worker.

This is a supervisor, not a second implementation agent. The existing GitHub
Actions engineering workflow owns diagnosis, code changes, tests and PR creation.
The supervisor never merges, deploys, scores sport, or grants verification.
"""
from __future__ import annotations

import json
import io
import logging
import os
import re
import socket
import threading
import uuid
import zipfile
from datetime import datetime, timezone
import urllib.request
from urllib.error import HTTPError
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from v17.engineering_dispatch_queue import _validate_manifest
from v17.amrt_operational_lanes import dispatch_preflight
from v17.engineering_provider_failover import resident_provider_admission
from v17.engineering_remote_fallback import assert_no_remote_writer

LOG = logging.getLogger("wow.v17.engineering_resident_supervisor")
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
# The numeric canonical GitHub API alias for this exact repository, verified
# against GET /repos/{owner}/{repo}. Never accept a different numeric ID.
REPO_ID = 1240256887
PROVIDER_WORKFLOW = "wow-v17-engineering-provider-dispatcher.yml"
# Only the three registered engineering workflows can own the implementation
# writer. Unrelated CI run churn must never corrupt this busy check.
ACTIVE_WORKFLOW_FILES = (
    "wow-v17-chatgpt-engineering-worker.yml",
    "wow-v17-claude-engineering-worker.yml",
    "wow-v17-engineering-provider-dispatcher.yml",
)
MANIFEST = Path(__file__).with_name("engineering_dispatch_manifest.json")
LOCK_KEY = "wow:v17:engineering:resident-dispatch-lock:v1"
COOLDOWN_KEY = "wow:v17:engineering:resident-dispatch-cooldown:v1"
ATTEMPT_KEY_PREFIX = "wow:v17:engineering:resident-dispatch-attempts:v1:"
PENDING_DISPATCH_KEY = LOCK_KEY + ":pending"
DISPATCHER_HEARTBEAT_ID = "WOW_ENGINEERING_RESIDENT_DISPATCHER"
OPEN_PR_PAGE_SIZE = 100
MAX_OPEN_PR_PAGES = 10
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


class GitHubTransport:
    """Read GitHub issue/workflow state and dispatch an existing trusted workflow."""

    def __init__(self, token: str, repo: str = REPO) -> None:
        if not token:
            raise ValueError("GITHUB_DISPATCH_TOKEN_MISSING")
        self.token, self.repo = token, repo

    def call_with_headers(
        self, path: str, payload: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, str]]:
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
            response_headers = dict(response.headers.items())
        return (json.loads(raw) if raw else {}), response_headers

    def call(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self.call_with_headers(path, payload)[0]

    def get(self, suffix: str) -> Any:
        return self.call(f"/repos/{self.repo}/{suffix}")

    def remote_writer_clear(self) -> bool:
        comments = []
        for page in range(1, 101):
            rows = self.get(f"issues/1138/comments?per_page=100&page={page}")
            if not isinstance(rows, list):
                raise ValueError("REMOTE_RESERVATION_INVALID")
            comments.extend(rows)
            if len(rows) < 100:
                break
        else:
            raise ValueError("REMOTE_RESERVATION_INVALID")
        assert_no_remote_writer(
            comments, get_run=lambda run_id: self.get(f"actions/runs/{run_id}"))
        now = datetime.now(timezone.utc)
        for comment in comments:
            body = comment.get("body") or ""
            if "<!-- WOW_DUAL_STREAM_HEARTBEAT:" in body and "- worker_mode: RUNNING" in body:
                expiry = re.search(r"^- lease_expires_at: (.+)$", body, re.M)
                if not expiry:
                    return False
                stamp = datetime.fromisoformat(expiry[1].replace("Z", "+00:00"))
                if stamp > now:
                    return False
        return True

    def provider_admission(self) -> str:
        """Read existing GitHub receipts only. Never probe a paid provider."""
        result = self.get(
            f"actions/workflows/{PROVIDER_WORKFLOW}/runs?branch=main&status=completed&per_page=1"
        )
        runs = result.get("workflow_runs")
        if not isinstance(runs, list) or not runs:
            return "PROVIDER_STATUS_UNVERIFIED"
        run = runs[0]
        if (run.get("head_branch") != "main" or run.get("status") != "completed"
                or run.get("path") != f".github/workflows/{PROVIDER_WORKFLOW}"
                or type(run.get("id")) is not int or run["id"] <= 0
                or not re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", "")))):
            return "PROVIDER_STATUS_UNVERIFIED"
        artifacts = self.get(f"actions/runs/{run['id']}/artifacts?per_page=100")
        rows = artifacts.get("artifacts")
        if (not isinstance(rows, list) or type(artifacts.get("total_count")) is not int
                or artifacts["total_count"] != len(rows)):
            return "PROVIDER_STATUS_UNVERIFIED"
        name = f"wow-v17-engineering-provider-receipt-{run['id']}-{run.get('run_attempt')}"
        selected = [row for row in rows if row.get("name") == name and row.get("expired") is False]
        if len(selected) != 1 or type(selected[0].get("id")) is not int:
            return "PROVIDER_STATUS_UNVERIFIED"
        route = self.provider_receipt(selected[0]["id"])
        now = datetime.now(timezone.utc)
        decision = resident_provider_admission(route, updated_at=run.get("updated_at"), now=now)
        if decision == "PROVIDER_STATUS_UNVERIFIED":
            return decision
        # A later successful protected worker proves recovery; a later failed
        # or unclassified worker invalidates an older positive route receipt.
        latest_success = False
        for workflow in ("wow-v17-chatgpt-engineering-worker.yml", "wow-v17-claude-engineering-worker.yml"):
            workers = self.get(f"actions/workflows/{workflow}/runs?branch=main&status=completed&per_page=1")
            items = workers.get("workflow_runs")
            if not isinstance(items, list):
                return "PROVIDER_STATUS_UNVERIFIED"
            if not items:
                continue
            worker = items[0]
            if (worker.get("path") != f".github/workflows/{workflow}"
                    or worker.get("head_branch") != "main" or worker.get("status") != "completed"
                    or type(worker.get("id")) is not int or worker["id"] <= 0
                    or not re.fullmatch(r"[0-9a-f]{40}", str(worker.get("head_sha", "")))
                    or worker.get("conclusion") not in {"success", "failure", "cancelled", "timed_out"}):
                return "PROVIDER_STATUS_UNVERIFIED"
            updated = datetime.fromisoformat(worker["updated_at"].replace("Z", "+00:00"))
            route_time = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
            age = (now - updated).total_seconds()
            if age < 0:
                return "PROVIDER_STATUS_UNVERIFIED"
            acknowledged = (
                route.get("action") == "NO_ACTION"
                and str(route.get("parent_run_id")) == str(worker["id"])
                and route.get("reason") == workflow.removesuffix(".yml") + "_SUCCESS"
                and worker["conclusion"] == "success"
            )
            if updated >= route_time or acknowledged:
                if worker["conclusion"] == "success" and age <= 900:
                    latest_success |= worker["head_sha"] == self.get("commits/main").get("sha")
                elif (decision == "PROVIDER_ADMITTED"
                      and route.get("provider") == ("openai" if "chatgpt" in workflow else "anthropic")):
                    decision = "PROVIDER_RECOVERY_UNVERIFIED"
        if latest_success:
            return "PROVIDER_ADMITTED"
        if decision == "PROVIDER_ADMITTED" and self.get("commits/main").get("sha") != run["head_sha"]:
            return "PROVIDER_RECOVERY_UNVERIFIED"
        return decision

    def provider_receipt(self, artifact_id: int) -> dict[str, Any]:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}/actions/artifacts/{artifact_id}/zip",
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json"},
        )
        try:
            opener.open(request, timeout=8)
        except HTTPError as exc:
            if exc.code != 302:
                raise ValueError("PROVIDER_STATUS_UNVERIFIED") from None
            location = exc.headers.get("Location", "")
        else:
            raise ValueError("PROVIDER_STATUS_UNVERIFIED")
        target = urlparse(location)
        if (target.scheme != "https" or target.username or target.password or target.port
                or not (target.netloc.endswith(".blob.core.windows.net")
                        or target.netloc.endswith(".githubusercontent.com"))):
            raise ValueError("PROVIDER_STATUS_UNVERIFIED")
        # Signed artifact URLs get NO repository credential and no redirects.
        with opener.open(urllib.request.Request(location), timeout=8) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("PROVIDER_STATUS_UNVERIFIED")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            matches = [info for info in archive.infolist() if info.filename == "provider-route.json"]
            if len(matches) != 1 or matches[0].file_size > 32_768:
                raise ValueError("PROVIDER_STATUS_UNVERIFIED")
            return json.loads(archive.read(matches[0]))

    def dispatched_runs(self, claim: dict[str, Any]) -> list[dict[str, Any]]:
        result = self.get(
            f"actions/workflows/{PROVIDER_WORKFLOW}/runs?branch=main&event=workflow_dispatch&per_page=100"
        )
        if not isinstance(result, dict):
            raise ValueError("DISPATCH_RECONCILIATION_REQUIRED")
        runs = result.get("workflow_runs")
        total = result.get("total_count")
        if (type(total) is not int or total < 0 or not isinstance(runs, list)
                or len(runs) > 100 or any(not isinstance(run, dict) for run in runs)):
            raise ValueError("DISPATCH_RECONCILIATION_REQUIRED")
        if total > len(runs):
            # A full page is sufficient only if it crosses the reservation boundary.
            if not runs or runs[-1].get("created_at", "") >= claim["created_at"]:
                raise ValueError("DISPATCH_RECONCILIATION_REQUIRED")
        return [run for run in runs
                if run.get("head_branch") == "main" and run.get("event") == "workflow_dispatch"
                and run.get("path") == f".github/workflows/{PROVIDER_WORKFLOW}"
                and run.get("created_at", "") >= claim["created_at"]
                and re.search(rf"\blease={re.escape(claim['lease_group'])} incident={claim['issue_number']}\b",
                              run.get("display_title", ""))
                and re.search(rf"\bresident={claim['dispatch_id']}(?:\s|$)",
                              run.get("display_title", ""))]

    def get_open_pull_request_page(self, page: int) -> tuple[Any, bool, bool]:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("OPEN_PR_PAGE_INVALID")
        result, headers = self.call_with_headers(
            f"/repos/{self.repo}/pulls?state=open&per_page={OPEN_PR_PAGE_SIZE}&page={page}",
        )
        has_next, has_pagination_link = _open_pr_page_pagination(
            headers, page=page, repo=self.repo,
        )
        return result, has_next, has_pagination_link

    def dispatch(self, issue: dict[str, Any], *, dispatch_id: str = "") -> None:
        group = str(issue.get("lease_group") or "GLOBAL")
        incident = str(issue["issue_number"])
        if issue.get("severity") == "P0":
            if group == "GLOBAL":
                raise ValueError("P0_DOMAIN_LEASE_MISSING")
        elif group != "GLOBAL":
            raise ValueError("STANDARD_TARGET_MUST_USE_GLOBAL_LEASE")
        if dispatch_id and not re.fullmatch(r"[0-9a-f]{32}", dispatch_id):
            raise ValueError("DISPATCH_RECONCILIATION_REQUIRED")
        # An exact P1 target must not silently degrade to generic queue selection.
        self.call(
            f"/repos/{self.repo}/actions/workflows/{PROVIDER_WORKFLOW}/dispatches",
            {"ref": "main", "inputs": {
                "force_provider": "auto",
                "reason": "RESIDENT_SUPERVISOR_NEXT_TASK",
                "target_incident": incident,
                "lease_group": group,
                **({"resident_dispatch_id": dispatch_id} if dispatch_id else {}),
            }},
        )


def active_engineering_workflow(client: GitHubTransport) -> bool:
    """Fail closed on each exact GitHub engineering-workflow active inventory.

    Repository-wide counts can race unrelated CI execution; query only the
    three already-registered writers. Positive evidence is always BUSY,
    including transient total_count/row skew. Malformed or overfull inventory
    still blocks instead of granting another source writer.
    """
    for filename in ACTIVE_WORKFLOW_FILES:
        for state in ("queued", "in_progress", "waiting", "requested"):
            result = client.get(
                f"actions/workflows/{filename}/runs?status={state}&per_page=100"
            )
            if not isinstance(result, dict):
                raise RuntimeError("ACTIVE_WORKFLOW_INVENTORY_INVALID")
            total, runs = result.get("total_count"), result.get("workflow_runs")
            if (
                isinstance(total, bool) or not isinstance(total, int)
                or total < 0 or not isinstance(runs, list)
                or len(runs) > 100 or any(not isinstance(run, dict) for run in runs)
            ):
                raise RuntimeError("ACTIVE_WORKFLOW_INVENTORY_INVALID")
            if total > 100:
                raise RuntimeError("ACTIVE_WORKFLOW_INVENTORY_INCOMPLETE")
            if total > 0 or runs:
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


def _open_pr_page_pagination(
    headers: dict[str, str], *, page: int, repo: str,
) -> tuple[bool, bool]:
    link_header = next(
        (value for key, value in headers.items() if key.lower() == "link"),
        None,
    )
    if link_header is None:
        return False, False
    if not isinstance(link_header, str):
        raise RuntimeError("OPEN_PR_INVENTORY_INVALID")

    next_links = []
    for link in link_header.split(","):
        match = re.fullmatch(
            r'\s*<([^<>]+)>\s*;\s*rel\s*=\s*"?([a-z]+)"?\s*', link,
        )
        if match is None:
            raise RuntimeError("OPEN_PR_INVENTORY_INVALID")
        if match.group(2) == "next":
            next_links.append(match.group(1))
    if len(next_links) > 1:
        raise RuntimeError("OPEN_PR_INVENTORY_INVALID")
    if not next_links:
        return False, True

    next_url = urlparse(next_links[0])
    query = parse_qs(next_url.query, keep_blank_values=True)
    permitted_paths = {f"/repos/{repo}/pulls"}
    if repo == REPO:
        # GitHub's own Link header may prefer its canonical /repositories/id
        # form. This is the SAME pre-verified repository, not a wildcard.
        permitted_paths.add(f"/repositories/{REPO_ID}/pulls")
    if (
        next_url.scheme != "https"
        or next_url.netloc != "api.github.com"
        or next_url.path not in permitted_paths
        or next_url.fragment
        or set(query) != {"state", "per_page", "page"}
        or query.get("state") != ["open"]
        or query.get("per_page") != [str(OPEN_PR_PAGE_SIZE)]
        or query.get("page") != [str(page + 1)]
    ):
        raise RuntimeError("OPEN_PR_INVENTORY_INVALID")
    return True, True


def _validate_open_pr(pr: Any, *, repo: str) -> dict[str, Any]:
    if not isinstance(pr, dict):
        raise RuntimeError("OPEN_PR_INVENTORY_INVALID")
    number = pr.get("number")
    issue_url = pr.get("issue_url")
    if (
        isinstance(number, bool)
        or not isinstance(number, int)
        or number < 1
        or pr.get("state") != "open"
        or pr.get("html_url") != f"https://github.com/{repo}/pull/{number}"
        or not isinstance(pr.get("title"), str)
        or (pr.get("body") is not None and not isinstance(pr.get("body"), str))
        or issue_url != f"https://api.github.com/repos/{repo}/issues/{number}"
        or not isinstance(pr.get("draft"), bool)
    ):
        raise RuntimeError("OPEN_PR_INVENTORY_INVALID")
    return pr


def pending_pr_for_issue(client: GitHubTransport, issue_number: int) -> bool:
    repo = getattr(client, "repo", REPO)
    issue_ref = re.escape(str(issue_number))
    body_pattern = re.compile(
        r"(?im)^Incident:\s*(?:\x60)?#?" + issue_ref + r"(?:\x60)?\s*$",
    )
    seen_numbers: set[int] = set()
    matches_issue = False

    for page in range(1, MAX_OPEN_PR_PAGES + 1):
        result, has_next, has_pagination_link = client.get_open_pull_request_page(page)
        if not isinstance(result, list) or len(result) > OPEN_PR_PAGE_SIZE:
            raise RuntimeError("OPEN_PR_INVENTORY_INVALID")
        if (
            not isinstance(has_next, bool)
            or not isinstance(has_pagination_link, bool)
            or (has_next and len(result) != OPEN_PR_PAGE_SIZE)
        ):
            raise RuntimeError("OPEN_PR_INVENTORY_INVALID")

        for raw_pr in result:
            pr = _validate_open_pr(raw_pr, repo=repo)
            pr_number = pr["number"]
            if pr_number in seen_numbers:
                raise RuntimeError("OPEN_PR_INVENTORY_INCOMPLETE")
            seen_numbers.add(pr_number)

            # PR titles are untrusted descriptions, never incident-ownership proof.
            if body_pattern.search(pr.get("body") or ""):
                matches_issue = True

        if len(result) < OPEN_PR_PAGE_SIZE:
            return matches_issue
        if has_pagination_link and not has_next:
            return matches_issue
        if page == MAX_OPEN_PR_PAGES:
            raise RuntimeError("OPEN_PR_INVENTORY_NOT_EXHAUSTIVE")

    raise RuntimeError("OPEN_PR_INVENTORY_NOT_EXHAUSTIVE")


def dispatch_once(client: GitHubTransport, redis_client: Any, manifest: dict[str, Any]) -> str:
    """Atomic admission across resident replicas; GitHub worker remains the writer."""
    owner = uuid.uuid4().hex
    if not redis_client.set(LOCK_KEY, owner, nx=True, ex=180):
        return "ANOTHER_RESIDENT_SUPERVISOR_OWNS_LEASE"
    try:
        if not client.remote_writer_clear():
            return "REMOTE_WRITER_RECONCILIATION_REQUIRED"
    except Exception:
        return "REMOTE_WRITER_RECONCILIATION_REQUIRED"
    pending = redis_client.get(PENDING_DISPATCH_KEY)
    if pending:
        return reconcile_resident_dispatch(client, redis_client, pending)
    if redis_client.exists(COOLDOWN_KEY):
        return "RECENT_DISPATCH_COOLDOWN"
    if active_engineering_workflow(client):
        return "EXISTING_ENGINEERING_WORKFLOW_ACTIVE"
    try:
        provider_state = client.provider_admission()
    except Exception:
        return "PROVIDER_STATUS_UNVERIFIED"
    if not isinstance(provider_state, str) or provider_state not in {
        "PROVIDER_ADMITTED", "PROVIDER_QUOTA_BLOCKED", "PROVIDER_THROTTLED",
        "PROVIDER_RECOVERY_UNVERIFIED",
    }:
        return "PROVIDER_STATUS_UNVERIFIED"
    if provider_state != "PROVIDER_ADMITTED":
        return provider_state
    # An incident already at PR/review or retry cap must not starve every
    # other approved incident. Keep one shared writer and do not duplicate PRs.
    skipped: set[int] = set()
    has_pending_pr = False
    has_attempt_cap = False
    needs_p1_bootstrap = False
    while True:
        issue = next_approved_issue(
            client, manifest, exclude_issue_numbers=frozenset(skipped),
        )
        if issue is None:
            if has_attempt_cap:
                return "DISPATCH_ATTEMPT_CAP_REQUIRES_TRIAGE"
            if has_pending_pr:
                return "AWAITING_EXISTING_PR_REVIEW_OR_REPAIR"
            if needs_p1_bootstrap:
                return "P1_EXACT_WORKER_BOOTSTRAP_REQUIRED"
            return "NO_APPROVED_OPEN_ENGINEERING_TASK"
        issue_number = int(issue["issue_number"])
        if str(issue.get("severity")).upper() == "P1" and os.getenv(
            "WOW_ENGINEERING_EXACT_P1_BOOTSTRAP_CERTIFIED", "0"
        ) != "1":
            # Protected main currently rejects exact P1 targets. Without a
            # separately approved worker bootstrap, admitting P1 would loop
            # back to the generic top-of-queue candidate.
            needs_p1_bootstrap = True
            skipped.add(issue_number)
            continue
        # AMRT incidents share the same worker and lease; no alternative
        # dispatcher, background provider, or synthetic approval authority.
        if issue.get("amrt_owned") is True:
            admission = dispatch_preflight(issue)
            if admission.state != "READY":
                LOG.warning("AMRT admission held for issue %d reason=%s",
                            issue_number, ",".join(admission.reasons))
                skipped.add(issue_number)
                continue
        if pending_pr_for_issue(client, issue_number):
            has_pending_pr = True
            skipped.add(issue_number)
            continue
        # Prevent repeated failed agent invocations from burning quota all day.
        # The existing worker owns bounded repair attempts inside each run.
        attempts_key = ATTEMPT_KEY_PREFIX + str(issue_number)
        attempts = int(redis_client.get(attempts_key) or 0)
        if attempts >= 3:
            has_attempt_cap = True
            skipped.add(issue_number)
            continue
        claim = {
            "issue_number": issue_number,
            "lease_group": str(issue.get("lease_group") or "GLOBAL"),
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attempts_before": attempts, "counted": False,
            "dispatch_id": owner,
            "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
        }
        encoded = json.dumps(claim, sort_keys=True)
        # This persistent handoff extends the existing resident lease across
        # ambiguous POST/accounting responses; it has no automatic expiry/reset.
        reserved = redis_client.eval(
            """-- reserve_resident_dispatch
            if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
            return redis.call('SET', KEYS[2], ARGV[2], 'NX') and 1 or 0
            """, 2, LOCK_KEY, PENDING_DISPATCH_KEY, owner, encoded,
        )
        if not reserved:
            return "DISPATCH_RECONCILIATION_REQUIRED"
        try:
            client.dispatch(issue, dispatch_id=owner)
        except Exception:
            return "DISPATCH_RECONCILIATION_REQUIRED"
        if not count_accepted_dispatch(redis_client, encoded, claim):
            return "DISPATCH_RECONCILIATION_REQUIRED"
        redis_client.set(COOLDOWN_KEY, str(issue_number), ex=900)
        return "DISPATCHED_TO_PROTECTED_ENGINEERING_WORKFLOW"


def count_accepted_dispatch(redis_client: Any, encoded: str | bytes, claim: dict[str, Any]) -> bool:
    counted = json.dumps({**claim, "counted": True}, sort_keys=True)
    return bool(redis_client.eval(
        """-- count_accepted_resident_dispatch
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
        local before = tonumber(redis.call('GET', KEYS[2]) or '0')
        if before ~= tonumber(ARGV[2]) or before >= 3 then return 0 end
        redis.call('INCR', KEYS[2])
        redis.call('EXPIRE', KEYS[2], 86400)
        redis.call('SET', KEYS[1], ARGV[3])
        return 1
        """, 2, PENDING_DISPATCH_KEY,
        ATTEMPT_KEY_PREFIX + str(claim["issue_number"]),
        encoded, claim["attempts_before"], counted,
    ))


def reconcile_resident_dispatch(client: GitHubTransport, redis_client: Any, encoded: str | bytes) -> str:
    try:
        claim = json.loads(encoded)
        if (claim.get("can_execute") is not False
                or claim.get("terminal_authority") != "V17_TERMINAL_REDUCER"
                or type(claim.get("issue_number")) is not int or claim["issue_number"] < 1
                or type(claim.get("counted")) is not bool
                or type(claim.get("attempts_before")) is not int
                or not 0 <= claim["attempts_before"] < 3
                or not re.fullmatch(r"[0-9a-f]{32}", claim.get("dispatch_id", ""))
                or not re.fullmatch(r"[A-Za-z0-9_-]+", claim.get("lease_group", ""))
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", claim.get("created_at", ""))):
            return "DISPATCH_RECONCILIATION_REQUIRED"
        runs = client.dispatched_runs(claim)
        if len(runs) != 1 or runs[0].get("status") not in {
            "queued", "in_progress", "waiting", "requested", "completed", "pending",
        }:
            return "DISPATCH_RECONCILIATION_REQUIRED"
        if not claim["counted"]:
            if not count_accepted_dispatch(redis_client, encoded, claim):
                return "DISPATCH_RECONCILIATION_REQUIRED"
            encoded = json.dumps({**claim, "counted": True}, sort_keys=True)
        if runs[0]["status"] != "completed":
            return "EXISTING_ENGINEERING_WORKFLOW_ACTIVE"
        # Preserve the existing bounded counter; only the pending handoff clears.
        cleared = redis_client.eval(
            """-- clear_reconciled_resident_dispatch
            if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
            return redis.call('DEL', KEYS[1])
            """, 1, PENDING_DISPATCH_KEY, encoded,
        )
        return "DISPATCH_RECONCILED" if cleared else "DISPATCH_RECONCILIATION_REQUIRED"
    except Exception:
        return "DISPATCH_RECONCILIATION_REQUIRED"


def _enabled() -> bool:
    return os.getenv("WOW_ENGINEERING_RESIDENT_DISPATCH_ENABLED", "0") == "1"


def supervisor_runtime_status() -> dict[str, Any]:
    """Expose activation truth without exposing a token or credential."""
    enabled = _enabled()
    token_present = bool(os.getenv("WOW_ENGINEERING_GITHUB_TOKEN", "").strip())
    redis_present = bool(os.getenv("REDIS_URL", "").strip())
    supabase_present = bool(os.getenv("SUPABASE_URL", "").strip()) and bool(
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    )
    safe = (
        os.getenv("WOW_CAN_EXECUTE", "false").strip().lower() == "false"
        and os.getenv("WOW_DRY_RUN_ONLY", "true").strip().lower() == "true"
    )
    status = "DISABLED" if not enabled else (
        "CONFIGURED_UNVERIFIED" if token_present and redis_present and supabase_present and safe else "BLOCKED"
    )
    return {
        "status": status,
        "enabled": enabled,
        "token_configured": token_present,
        "redis_configured": redis_present,
        "durable_heartbeat_configured": supabase_present,
        "governance_pass": safe,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "merge_authority": False,
        "deploy_authority": False,
    }



def resident_activation_blockers(status: dict[str, Any]) -> tuple[str, ...]:
    """Expose missing *categories* only, never env values or token lengths.

    Startup diagnostics are source-truth, not an authorization override.
    """
    checks = (
        ("enabled", "RESIDENT_FLAG_DISABLED"),
        ("token_configured", "GITHUB_DISPATCH_TOKEN_MISSING"),
        ("redis_configured", "REDIS_LEASE_CONFIG_MISSING"),
        ("durable_heartbeat_configured", "DURABLE_HEARTBEAT_CONFIG_MISSING"),
        ("governance_pass", "DRY_RUN_GOVERNANCE_HOLD"),
    )
    return tuple(code for field, code in checks if status.get(field) is not True)


def persist_resident_heartbeat(
    db_client: Any, *, status: str, outcome: str, instance_id: str,
) -> None:
    """Persist independent, source-attributed liveness; never a repair PASS.

    This creates a distinct row in the existing protected runtime table and
    cannot overwrite the engineering auditor's own heartbeat. Failure to
    persist the PRE-DISPATCH receipt blocks that dispatch cycle.
    """
    if status not in {"STARTING", "RUNNING", "DEGRADED", "STOPPED"}:
        raise ValueError("RESIDENT_HEARTBEAT_STATUS_INVALID")
    now = datetime.now(timezone.utc).isoformat()
    safe_outcome = str(outcome).upper()
    if len(safe_outcome) > 120 or not all(
        ch.isalnum() or ch == "_" for ch in safe_outcome
    ):
        raise ValueError("RESIDENT_HEARTBEAT_OUTCOME_INVALID")
    payload = {
        "auditor_id": DISPATCHER_HEARTBEAT_ID,
        "instance_id": instance_id,
        "status": status,
        "last_heartbeat_at": now,
        "last_error_code": safe_outcome,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "updated_at": now,
    }
    if status == "STOPPED":
        # Render may start a replacement before the retiring Celery instance
        # has emitted its last heartbeat. Fence shutdown to that exact instance;
        # an old STOPPED cannot clobber a newer RUNNING supervisor heartbeat.
        db_client.table("wow_engineering_auditor_runtime").update(payload).eq(
            "auditor_id", DISPATCHER_HEARTBEAT_ID
        ).eq("instance_id", instance_id).execute()
    else:
        db_client.table("wow_engineering_auditor_runtime").upsert(
            payload, on_conflict="auditor_id"
        ).execute()


# Only fixed, locally-authored failure literals can reach operational logs or
# durable heartbeat receipts. Never print raw exception messages returned from
# GitHub/Redis/Supabase transports: those could contain credentials or URLs.
_SAFE_RESIDENT_CYCLE_REASONS = frozenset({
    "ACTIVE_WORKFLOW_INVENTORY_INCOMPLETE",
    "OPEN_PR_INVENTORY_INVALID",
    "OPEN_PR_INVENTORY_INCOMPLETE",
    "OPEN_PR_INVENTORY_NOT_EXHAUSTIVE",
    "OPEN_PR_PAGE_INVALID",
    "P0_DOMAIN_LEASE_MISSING",
    "STANDARD_TARGET_MUST_USE_GLOBAL_LEASE",
    "UNAPPROVED_GITHUB_API_PATH",
})


def resident_cycle_failure_code(exc: Exception) -> str:
    """Return an allowlisted diagnosis; redact arbitrary upstream error text."""
    reason = str(exc)
    if isinstance(exc, (RuntimeError, ValueError)) and reason in _SAFE_RESIDENT_CYCLE_REASONS:
        return reason
    return "CYCLE_" + type(exc).__name__.upper()


def run_resident_supervisor(stop: threading.Event = _STOP) -> None:
    status = supervisor_runtime_status()
    if status["status"] != "CONFIGURED_UNVERIFIED":
        LOG.warning(
            "WOW_RESIDENT_SUPERVISOR status=%s missing_categories=%s can_execute=false",
            status["status"],
            ",".join(resident_activation_blockers(status)) or "NONE",
        )
        return
    from redis import Redis
    from v17.engineering_auditor_worker import _db_client

    instance_id = f"{socket.gethostname()}:{os.getpid()}"
    try:
        db_client = _db_client()
        persist_resident_heartbeat(
            db_client, status="STARTING", outcome="INITIALIZING", instance_id=instance_id,
        )
    except Exception as exc:
        LOG.warning(
            "WOW_RESIDENT_SUPERVISOR status=BLOCKED reason=HEARTBEAT_STORAGE_UNAVAILABLE "
            "error_type=%s can_execute=false", type(exc).__name__,
        )
        return

    interval = 300
    while not stop.is_set():
        try:
            # Pre-admission persistence: failed durable evidence cannot dispatch.
            persist_resident_heartbeat(
                db_client, status="RUNNING", outcome="CYCLE_STARTED", instance_id=instance_id,
            )
            manifest = json.loads(MANIFEST.read_text())
            with Redis.from_url(os.environ["REDIS_URL"]) as redis_client:
                outcome = dispatch_once(client=GitHubTransport(
                    os.environ["WOW_ENGINEERING_GITHUB_TOKEN"],
                ), redis_client=redis_client, manifest=manifest)
            is_hold = outcome in {
                "DISPATCH_ATTEMPT_CAP_REQUIRES_TRIAGE",
                "AWAITING_EXISTING_PR_REVIEW_OR_REPAIR",
                "P1_EXACT_WORKER_BOOTSTRAP_REQUIRED",
                "REMOTE_WRITER_RECONCILIATION_REQUIRED",
                "DISPATCH_RECONCILIATION_REQUIRED",
            } or outcome.startswith("PROVIDER_")
            persist_resident_heartbeat(
                db_client, status="DEGRADED" if is_hold else "RUNNING",
                outcome=outcome, instance_id=instance_id,
            )
            LOG.warning("WOW_RESIDENT_SUPERVISOR outcome=%s can_execute=false", outcome)
        except Exception as exc:
            failure_code = resident_cycle_failure_code(exc)
            LOG.warning(
                "WOW_RESIDENT_SUPERVISOR outcome=BLOCKED reason=%s error_type=%s can_execute=false",
                failure_code, type(exc).__name__,
            )
            try:
                persist_resident_heartbeat(
                    db_client, status="DEGRADED",
                    outcome=failure_code,
                    instance_id=instance_id,
                )
            except Exception:
                LOG.warning(
                    "WOW_RESIDENT_SUPERVISOR outcome=HEARTBEAT_WRITE_FAILED can_execute=false",
                )
        if stop.wait(interval):
            break
    try:
        persist_resident_heartbeat(
            db_client, status="STOPPED", outcome="WORKER_SHUTDOWN", instance_id=instance_id,
        )
    except Exception:
        LOG.warning("WOW_RESIDENT_SUPERVISOR outcome=STOP_HEARTBEAT_FAILED can_execute=false")


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
