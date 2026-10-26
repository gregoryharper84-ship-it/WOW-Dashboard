"""Fail-closed exact-PR-head HOLD target discovery for protected WOW Engineering.

Read-only. Only the independent QA/Release Apps publish actionable findings.
The triggering workflow_run HEAD is *main*, never used as a PR head SHA.
No changes to models, bet execution, or release authority.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
import re
from urllib import request, error

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
APP_AUTHOR = "wow-engineering[bot]"
QA_CHECK = "WOW Independent QA exact-head"
RELEASE_CHECK = "WOW Release Authority exact-head"
WORKFLOWS = {
    "wow-v17-independent-qa-check.yml": "qa",
    "wow-v17-release-authority-check.yml": "release",
}
SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_PAGES = 5
MAX_TARGETS = 16
MAX_SUMMARY = 8000
# Scheduled sweeps retain unresolved latest-check HOLDs even after 24h.
# The durable per-PR/head App marker suppresses already-dispatched retries.


class Blocked(ValueError):
    pass


def stamp(value):
    if not isinstance(value, str) or not value:
        raise Blocked("EVENT_TIMESTAMP_INVALID")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Blocked("EVENT_TIMESTAMP_INVALID") from exc
    if result.utcoffset() is None:
        raise Blocked("EVENT_TIMESTAMP_UNZONED")
    return result.astimezone(timezone.utc)


def paginate(fetch, path, *, key=None):
    """Bounded pagination; partial inventories are an error, never a green no-op."""
    rows = []
    for page in range(1, MAX_PAGES + 1):
        payload = fetch(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        values = payload.get(key) if key else payload
        if not isinstance(values, list):
            raise Blocked("INVENTORY_UNREADABLE:" + path)
        rows.extend(values)
        if len(values) < 100:
            if key and isinstance(payload, dict) and payload.get("total_count", len(rows)) > len(rows):
                raise Blocked("INVENTORY_TRUNCATED:" + path)
            return rows
    raise Blocked("INVENTORY_PAGE_CAP:" + path)


def source_role(event, event_name, repo):
    if event_name in ("schedule", "workflow_dispatch"):
        return None
    if event_name != "workflow_run":
        raise Blocked("UNEXPECTED_EVENT_TYPE")
    run = event.get("workflow_run")
    if not isinstance(run, dict):
        raise Blocked("WORKFLOW_RUN_MISSING")
    path = str(run.get("path") or "")
    filename = path.rsplit("/", 1)[-1]
    role = WORKFLOWS.get(filename)
    expected = filename[:-4] if filename.endswith(".yml") else ""
    name = str(run.get("name") or "")
    if not role or not (name == expected or name.startswith(expected + " ")):
        raise Blocked("UNTRUSTED_UPSTREAM_WORKFLOW")
    if (run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("head_branch") != "main"
            or (run.get("head_repository") or {}).get("full_name") != repo
            or run.get("event") not in ("workflow_run", "workflow_dispatch", "schedule")):
        raise Blocked("UNTRUSTED_UPSTREAM_RUN_IDENTITY")
    return role


def latest_authored(checks, sha, expected_id, check_name):
    matches = [c for c in checks if isinstance(c, dict)
               and c.get("head_sha") == sha
               and c.get("name") == check_name
               and str((c.get("app") or {}).get("id")) == str(expected_id)]
    return max(matches, key=lambda v: int(v.get("id") or 0), default=None)


def discover(fetch, event, event_name, repo, qa_id, release_id, now=None):
    if repo != REPO:
        raise Blocked("REPOSITORY_NOT_ALLOWLISTED")
    if not (str(qa_id).isdigit() and str(release_id).isdigit()) or str(qa_id) == str(release_id):
        raise Blocked("INDEPENDENT_APP_IDENTITIES_INVALID")
    role = source_role(event, event_name, repo)
    run = event.get("workflow_run") or {}
    if role:
        # A rerun can have a different actual started_at; allow only the upstream
        # run's narrow signed time window, not a historic HOLD on the same head.
        lower = stamp(run.get("run_started_at") or run.get("created_at")) - timedelta(seconds=30)
        upper = stamp(run.get("updated_at")) + timedelta(seconds=60)
        if lower > upper or upper - lower > timedelta(hours=1):
            raise Blocked("UPSTREAM_RUN_WINDOW_INVALID")
    else:
        now = now or datetime.now(timezone.utc)
        lower, upper = None, now + timedelta(minutes=2)

    prs = paginate(fetch, f"/repos/{repo}/pulls?state=open&base=main")
    targets, deferred = [], []
    for pr in prs:
        if not isinstance(pr, dict) or pr.get("state") != "open" or pr.get("draft"):
            continue
        if (pr.get("user") or {}).get("login") != APP_AUTHOR:
            continue
        head = pr.get("head") or {}
        sha = head.get("sha")
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise Blocked("APP_PR_HEAD_INVALID")
        if (head.get("repo") or {}).get("full_name") != repo or (pr.get("base") or {}).get("ref") != "main":
            raise Blocked("APP_PR_REPOSITORY_IDENTITY_INVALID")
        num = pr.get("number")
        if not isinstance(num, int) or num <= 0:
            raise Blocked("APP_PR_NUMBER_INVALID")
        checks = paginate(fetch, f"/repos/{repo}/commits/{sha}/check-runs", key="check_runs")
        candidates = []
        for cur_role, (app_id, check_name) in {
            "qa": (qa_id, QA_CHECK), "release": (release_id, RELEASE_CHECK),
        }.items():
            if role and cur_role != role:
                continue
            current = latest_authored(checks, sha, app_id, check_name)
            if current is None or current.get("status") != "completed" or current.get("conclusion") != "failure":
                continue
            completed = stamp(current.get("completed_at"))
            if completed > upper or (lower is not None and completed < lower):
                deferred.append({"pr": num, "finding": "HOLD_OUTSIDE_EVENT_WINDOW", "role": cur_role})
                continue
            summary = (current.get("output") or {}).get("summary")
            if (not isinstance(summary, str) or len(summary) > MAX_SUMMARY
                    or "Typed findings:" not in summary or "can_execute=false" not in summary):
                raise Blocked("APP_HOLD_SUMMARY_MALFORMED")
            check_id = current.get("id")
            if not isinstance(check_id, int) or check_id <= 0:
                raise Blocked("APP_HOLD_CHECK_ID_INVALID")
            candidates.append((check_id, cur_role, summary))
        if not candidates:
            continue
        check_id, cur_role, summary = max(candidates, key=lambda x: x[0])
        targets.append({"pr_number": num, "head_sha": sha, "source_check_id": check_id,
                        "source_app_id": int(qa_id if cur_role == "qa" else release_id),
                        "source_role": cur_role})
    targets.sort(key=lambda x: x["pr_number"])
    if len(targets) > MAX_TARGETS:
        # Bounded and fair: never globally fail a healthy inventory merely
        # because more than 16 eligible repairs are awaiting attention.
        # Workflow events take a bounded initial slice; the hourly reentry
        # rotates across the entire authentic latest-check population.
        if role:
            offset = 0
        else:
            hour_slot = int(now.timestamp() // 3600)
            offset = (hour_slot * MAX_TARGETS) % len(targets)
        chosen = (targets[offset:] + targets[:offset])[:MAX_TARGETS]
        selected_ids = {x["pr_number"] for x in chosen}
        for item in targets:
            if item["pr_number"] not in selected_ids and len(deferred) < 32:
                deferred.append({"pr": item["pr_number"],
                                 "finding": "BOUNDED_HOLD_BATCH_DEFERRED"})
        targets = chosen
    return {"targets": targets, "deferred": deferred[:32], "can_execute": False}


def github_fetch(path):
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise Blocked("GITHUB_READ_TOKEN_MISSING")
    req = request.Request("https://api.github.com" + path,
                          headers={"Authorization": "Bearer " + token,
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28",
                                   "User-Agent": "WOW-engineering-HOLD-resolver"})
    try:
        with request.urlopen(req, timeout=25) as resp:
            return json.load(resp)
    except (error.HTTPError, error.URLError, TimeoutError, ValueError) as exc:
        raise Blocked("GITHUB_READ_FAILED:" + type(exc).__name__) from None


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--event-file", required=True)
    p.add_argument("--event-name", required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--qa-app-id", required=True)
    p.add_argument("--release-app-id", required=True)
    args = p.parse_args(argv)
    try:
        with open(args.event_file, encoding="utf-8") as f:
            event = json.load(f)
        result = discover(github_fetch, event, args.event_name, args.repository,
                          args.qa_app_id, args.release_app_id)
    except (OSError, json.JSONDecodeError, Blocked) as exc:
        print(json.dumps({"error": "HOLD_DISCOVERY_BLOCKED:" + str(exc), "can_execute": False}))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
