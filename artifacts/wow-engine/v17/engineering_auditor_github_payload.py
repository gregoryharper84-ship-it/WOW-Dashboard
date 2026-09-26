"""Normalize GitHub Actions event JSON into the Engineering Auditor contract."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def _labels(obj: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for label in obj.get("labels") or []:
        if isinstance(label, dict) and label.get("name"):
            result.append(str(label["name"]))
        elif isinstance(label, str):
            result.append(label)
    return result


def normalize_event(event_name: str, event: dict[str, Any], *, repository: str, sha: str) -> dict[str, Any]:
    action = str(event.get("action") or "")
    actor = str((event.get("sender") or {}).get("login") or "") or None
    if event_name in {"issues", "issue_comment"}:
        issue = event.get("issue") or {}
        kind = "GITHUB_PR" if issue.get("pull_request") else "GITHUB_ISSUE"
        return {
            "event_name": event_name,
            "action": action or "commented",
            "repository": repository,
            "source_kind": kind,
            "source_ref": str(issue.get("number") or ""),
            "title": str(issue.get("title") or ""),
            "state": str(issue.get("state") or "open").upper(),
            "labels": _labels(issue),
            "draft": bool(issue.get("draft", False)),
            "actor": actor,
            "updated_at": issue.get("updated_at"),
            "head_sha": sha or None,
            "details": {"html_url": issue.get("html_url")},
        }
    if event_name in {"pull_request", "pull_request_review"}:
        pr = event.get("pull_request") or {}
        merged = bool(pr.get("merged"))
        state = "MERGED" if merged else str(pr.get("state") or "open").upper()
        return {
            "event_name": event_name,
            "action": action,
            "repository": repository,
            "source_kind": "GITHUB_PR",
            "source_ref": str(pr.get("number") or event.get("number") or ""),
            "title": str(pr.get("title") or ""),
            "state": state,
            "labels": _labels(pr),
            "draft": bool(pr.get("draft", False)),
            "actor": actor,
            "updated_at": pr.get("updated_at"),
            "head_sha": str((pr.get("head") or {}).get("sha") or sha or "") or None,
            "details": {"html_url": pr.get("html_url")},
        }
    if event_name in {"push", "workflow_dispatch"}:
        return {
            "event_name": event_name,
            "action": action or "updated",
            "repository": repository,
            "source_kind": "CODE_HEALTH_RUN",
            "source_ref": str(os.getenv("GITHUB_RUN_ID") or sha),
            "title": "WOW V17 engineering code-health trigger",
            "state": "COMPLETED",
            "labels": [],
            "draft": False,
            "actor": actor,
            "updated_at": None,
            "head_sha": sha or None,
            "conclusion": "success",
            "details": {"check_name": "EVENT_BRIDGE_TRIGGER"},
        }
    raise ValueError(f"unsupported GitHub event: {event_name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args()
    event = json.loads(Path(args.event_path).read_text()) if args.event_path else {}
    payload = normalize_event(args.event_name, event, repository=args.repository, sha=args.sha)
    if not payload.get("source_ref"):
        raise SystemExit("source_ref unavailable")
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
