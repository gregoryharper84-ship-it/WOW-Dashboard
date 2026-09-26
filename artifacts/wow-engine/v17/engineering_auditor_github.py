"""Public GitHub reconciliation for the resident WOW Engineering Auditor.

The repository is public, so the auditor does not need a GitHub credential to
observe issues, pull requests, or workflow-run health. This avoids creating a
new secret path for an evidence-only watchdog.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import requests

from v17.engineering_auditor import AuditEvent, EngineeringAuditStore, iso, utcnow

REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
DEFAULT_TIMEOUT_SECONDS = 10
CODE_HEALTH_WORKFLOW_NAMES = frozenset({
    "wow-v17-engineering-auditor-code-health",
    "wow-engine-verify",
    "wow-v17-change-impact-gate",
})


class GitHubAuditUnavailable(RuntimeError):
    pass


def _get(session: Any, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
    response = session.get(
        f"{API_ROOT}{path}",
        params=params,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "wow-v17-engineering-auditor/1.0"},
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise GitHubAuditUnavailable(f"GITHUB_AUDIT_HTTP_{response.status_code}")
    payload = response.json()
    if not isinstance(payload, (dict, list)):
        raise GitHubAuditUnavailable("GITHUB_AUDIT_RESPONSE_INVALID")
    return payload


def issue_to_event(issue: dict[str, Any]) -> AuditEvent:
    labels = tuple(
        str(label.get("name"))
        for label in issue.get("labels") or []
        if isinstance(label, dict) and label.get("name")
    )
    source_kind = "GITHUB_PR" if issue.get("pull_request") else "GITHUB_ISSUE"
    updated_at = issue.get("updated_at")
    return AuditEvent.from_mapping(
        {
            "event_name": "github_public_reconcile",
            "action": "reconcile",
            "repository": REPOSITORY,
            "source_kind": source_kind,
            "source_ref": str(issue.get("number") or ""),
            "title": str(issue.get("title") or ""),
            "state": str(issue.get("state") or "open").upper(),
            "labels": labels,
            "draft": bool(issue.get("draft", False)),
            "actor": ((issue.get("user") or {}).get("login") if isinstance(issue.get("user"), dict) else None),
            "updated_at": updated_at,
            "head_sha": None,
            "details": {"html_url": issue.get("html_url"), "source_updated_at": updated_at},
        }
    )


def workflow_run_to_event(run: dict[str, Any]) -> AuditEvent:
    name = str(run.get("name") or run.get("display_title") or "UNKNOWN_WORKFLOW")
    return AuditEvent.from_mapping(
        {
            "event_name": "github_actions_reconcile",
            "action": "completed",
            "repository": REPOSITORY,
            "source_kind": "CODE_HEALTH_RUN",
            "source_ref": str(run.get("id") or ""),
            "title": name,
            "state": "COMPLETED",
            "labels": [],
            "draft": False,
            "actor": ((run.get("actor") or {}).get("login") if isinstance(run.get("actor"), dict) else None),
            "updated_at": run.get("updated_at"),
            "head_sha": str(run.get("head_sha") or "") or None,
            "conclusion": str(run.get("conclusion") or "").lower() or None,
            "details": {"check_name": name, "html_url": run.get("html_url"), "run_number": run.get("run_number")},
        }
    )


def bootstrap_open_github_work(store: EngineeringAuditStore, *, session: Any = requests) -> int:
    payload = _get(session, "/issues", params={"state": "open", "sort": "updated", "direction": "asc", "per_page": 100})
    if not isinstance(payload, list):
        raise GitHubAuditUnavailable("GITHUB_AUDIT_ISSUES_RESPONSE_INVALID")
    count = 0
    for issue in payload:
        if not isinstance(issue, dict) or not issue.get("number"):
            continue
        store.ingest_event(issue_to_event(issue))
        count += 1
    return count


def reconcile_github_updates(
    store: EngineeringAuditStore,
    *,
    session: Any = requests,
    since: datetime | None = None,
    seen_workflow_runs: set[str] | None = None,
) -> dict[str, int]:
    now = utcnow()
    since = since or (now - timedelta(minutes=10))
    issues = _get(
        session,
        "/issues",
        params={"state": "all", "since": iso(since), "sort": "updated", "direction": "asc", "per_page": 100},
    )
    if not isinstance(issues, list):
        raise GitHubAuditUnavailable("GITHUB_AUDIT_ISSUES_RESPONSE_INVALID")
    work_n = 0
    for issue in issues:
        if not isinstance(issue, dict) or not issue.get("number"):
            continue
        store.ingest_event(issue_to_event(issue), now=now)
        work_n += 1

    actions = _get(session, "/actions/runs", params={"branch": "main", "per_page": 50})
    if not isinstance(actions, dict):
        raise GitHubAuditUnavailable("GITHUB_AUDIT_ACTIONS_RESPONSE_INVALID")
    seen = seen_workflow_runs if seen_workflow_runs is not None else set()
    latest_by_name: dict[str, dict[str, Any]] = {}
    for run in actions.get("workflow_runs") or []:
        if not isinstance(run, dict):
            continue
        if str(run.get("status") or "") != "completed" or str(run.get("head_branch") or "") != "main":
            continue
        name = str(run.get("name") or "")
        if name not in CODE_HEALTH_WORKFLOW_NAMES:
            continue
        latest_by_name.setdefault(name, run)

    code_health_n = 0
    for run in latest_by_name.values():
        run_id = str(run.get("id") or "")
        if not run_id or run_id in seen:
            continue
        store.ingest_event(workflow_run_to_event(run), now=now)
        seen.add(run_id)
        code_health_n += 1
    if len(seen) > 200:
        for run_id in list(seen)[:-100]:
            seen.discard(run_id)
    return {"github_work_events": work_n, "code_health_events": code_health_n}
