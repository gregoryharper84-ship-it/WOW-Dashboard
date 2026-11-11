#!/usr/bin/env python3
"""Read-only, fail-closed Greptile review inventory using GitHub's free API.

This is a diagnostic tool, NOT a required gate, reviewer, approval, or merge
scheduler. It cannot start a Greptile review, buy credits, or certify code.

Usage:
  GITHUB_TOKEN=... python scripts/greptile_review_audit.py \
      --repository owner/repo --output /tmp/greptile-audit.json

The token needs read-only pull-request and issue-comment access. A bounded
--max-prs is for targeted diagnostics and *always* reports partial coverage.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
CREDIT_NOTICE = re.compile(r"(?:run out of|out of|exhausted)\s+(?:your\s+)?review credits", re.I)
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_PAGES = 100
THREAD_QUERY = """
query($owner:String!, $name:String!, $number:Int!, $after:String) {
  repository(owner:$owner,name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100,after:$after) {
        nodes {
          id isResolved isOutdated
          comments(first:100) {
            nodes { body url author { login } }
            pageInfo { hasNextPage }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
      reviews(first:100) {
        nodes { author { login } commit { oid } state }
        pageInfo { hasNextPage }
      }
    }
  }
}
"""


class AuditUnavailable(RuntimeError):
    """An incomplete response must never be reported as clean."""


def is_greptile(login: Any) -> bool:
    """Do not mistake human mentions of Greptile for Greptile-authored reviews."""
    return isinstance(login, str) and "greptile" in login.lower()


def credit_pause(comment: dict[str, Any]) -> bool:
    body = comment.get("body")
    if not isinstance(body, str):
        return False
    login = ((comment.get("user") or {}).get("login") or
             (comment.get("author") or {}).get("login"))
    return bool(
        (is_greptile(login) and CREDIT_NOTICE.search(body)) or
        "<!-- greptile-credit-pause:" in body.lower()
    )


def classify_pr(pr: dict[str, Any], threads: list[dict[str, Any]],
                reviews: list[dict[str, Any]], comments: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify only evidence actually supplied; never grant release approval."""
    number = pr["number"]
    head = pr["head_sha"]
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise AuditUnavailable(f"PR_{number}_HEAD_INVALID")
    unresolved: list[dict[str, Any]] = []
    resolved_count = 0
    for thread in threads:
        if not isinstance(thread.get("isResolved"), bool) or not isinstance(thread.get("isOutdated"), bool):
            raise AuditUnavailable(f"PR_{number}_THREAD_STATE_INCOMPLETE")
        replies = (thread.get("comments") or {}).get("nodes") or []
        if any(is_greptile((c.get("author") or {}).get("login")) for c in replies):
            if thread["isResolved"]:
                resolved_count += 1
            else:
                first = next((c for c in replies if is_greptile((c.get("author") or {}).get("login"))), {})
                unresolved.append({
                    "thread_id": thread.get("id"),
                    "outdated": thread["isOutdated"],
                    "url": first.get("url"),
                    "summary": str(first.get("body") or "")[:600],
                })
    greptile_reviews = [r for r in reviews if is_greptile((r.get("author") or {}).get("login"))]
    exact_head_reviews = [r for r in greptile_reviews if (r.get("commit") or {}).get("oid") == head]
    paused = any(credit_pause(c) for c in comments)
    # Exact-head Greptile review evidence is advisory only, NEVER QA/SIRT/Release.
    if unresolved:
        status = "UNRESOLVED_GREPTILE_FINDING"
    elif paused:
        status = "GREPTILE_CREDITS_EXHAUSTED"
    elif exact_head_reviews:
        status = "GREPTILE_REVIEW_AT_HEAD_NO_OPEN_THREADS"
    elif greptile_reviews:
        status = "GREPTILE_REVIEW_STALE_HEAD"
    else:
        status = "NO_EXACT_HEAD_GREPTILE_REVIEW_EVIDENCE"
    return {
        "pr": number, "head_sha": head, "status": status,
        "unresolved_threads": unresolved, "resolved_greptile_threads": resolved_count,
        "historical_greptile_reviews": len(greptile_reviews),
        "exact_head_greptile_reviews": len(exact_head_reviews),
        "credit_pause_observed": paused,
        "independent_qa_release_or_sirt_accepted": None,  # Not within this audit's authority.
    }


class GitHubReadOnly:
    def __init__(self, token: str):
        if not token:
            raise AuditUnavailable("GITHUB_TOKEN_MISSING")
        self.token = token

    def request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/") or "//" in path:
            raise AuditUnavailable("GITHUB_PATH_INVALID")
        raw = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "wow-greptile-readonly-audit/1.0",
        }
        if raw is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(API_ROOT + path, data=raw, headers=headers), timeout=20) as response:
                value = json.load(response)
        except HTTPError as exc:
            raise AuditUnavailable(f"GITHUB_HTTP_{exc.code}") from exc
        except (URLError, TimeoutError, ValueError) as exc:
            raise AuditUnavailable("GITHUB_TRANSPORT_OR_JSON_INVALID") from exc
        return value

    def pulls(self, repository: str) -> list[dict[str, Any]]:
        pulls: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            chunk = self.request(f"/repos/{repository}/pulls?state=open&per_page=100&page={page}")
            if not isinstance(chunk, list):
                raise AuditUnavailable("GITHUB_PULLS_RESPONSE_INVALID")
            for p in chunk:
                pulls.append({"number": p["number"], "head_sha": p["head"]["sha"]})
            if len(chunk) < 100:
                return pulls
        raise AuditUnavailable("GITHUB_PULLS_PAGINATION_INCOMPLETE")

    def threads_and_reviews(self, repository: str, number: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        owner, name = repository.split("/", 1)
        threads: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        cursor = None
        for page in range(MAX_PAGES):
            response = self.request("/graphql", {"query": THREAD_QUERY, "variables": {
                "owner": owner, "name": name, "number": number, "after": cursor,
            }})
            if response.get("errors"):
                raise AuditUnavailable(f"PR_{number}_GRAPHQL_ERRORS")
            pull = ((response.get("data") or {}).get("repository") or {}).get("pullRequest")
            if not isinstance(pull, dict):
                raise AuditUnavailable(f"PR_{number}_GRAPHQL_INCOMPLETE")
            pages = pull.get("reviewThreads") or {}
            if not isinstance(pages.get("nodes"), list):
                raise AuditUnavailable(f"PR_{number}_THREAD_NODES_MISSING")
            for thread in pages["nodes"]:
                if (thread.get("comments") or {}).get("pageInfo", {}).get("hasNextPage"):
                    raise AuditUnavailable(f"PR_{number}_THREAD_COMMENTS_TRUNCATED")
            threads.extend(pages["nodes"])
            review_page = pull.get("reviews") or {}
            if not isinstance(review_page.get("nodes"), list):
                raise AuditUnavailable(f"PR_{number}_REVIEWS_MISSING")
            if (review_page.get("pageInfo") or {}).get("hasNextPage"):
                raise AuditUnavailable(f"PR_{number}_REVIEWS_TRUNCATED")
            if page == 0:
                reviews.extend(review_page["nodes"])
            info = pages.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return threads, reviews
            next_cursor = info.get("endCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise AuditUnavailable(f"PR_{number}_THREAD_CURSOR_INVALID")
            cursor = next_cursor
        raise AuditUnavailable(f"PR_{number}_THREAD_PAGINATION_INCOMPLETE")

    def issue_comments(self, repository: str, number: int) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            chunk = self.request(f"/repos/{repository}/issues/{number}/comments?per_page=100&page={page}")
            if not isinstance(chunk, list):
                raise AuditUnavailable(f"PR_{number}_ISSUE_COMMENTS_INVALID")
            collected.extend(chunk)
            if len(chunk) < 100:
                return collected
        raise AuditUnavailable(f"PR_{number}_ISSUE_COMMENTS_TRUNCATED")


def audit(client: GitHubReadOnly, repository: str, max_prs: int = 0) -> dict[str, Any]:
    if not REPO_PATTERN.fullmatch(repository) or max_prs < 0:
        raise AuditUnavailable("AUDIT_ARGUMENTS_INVALID")
    source = client.pulls(repository)
    partial = max_prs > 0 and len(source) > max_prs
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for pr in source[:max_prs or None]:
        try:
            threads, reviews = client.threads_and_reviews(repository, pr["number"])
            comments = client.issue_comments(repository, pr["number"])
            rows.append(classify_pr(pr, threads, reviews, comments))
        except (AuditUnavailable, KeyError, TypeError) as exc:
            errors.append({"pr": pr["number"], "error": str(exc)[:120]})
            # Stop on rate-limit/auth error; no free-provider retry storms.
            if "GITHUB_HTTP_403" in str(exc) or "GITHUB_HTTP_429" in str(exc):
                break
    outstanding = [p for p in rows if p["unresolved_threads"]]
    not_exact = [p for p in rows if p["exact_head_greptile_reviews"] == 0]
    if errors or partial or len(rows) != len(source):
        outcome = "AUDIT_INCOMPLETE"
    elif outstanding:
        outcome = "UNRESOLVED_GREPTILE_FINDINGS"
    elif not_exact:
        outcome = "GREPTILE_REVIEW_COVERAGE_UNPROVEN"
    else:
        outcome = "NO_OPEN_GREPTILE_FINDINGS_IN_EXACT_HEAD_REVIEWS"
    return {
        "schema": "WOW_GREPTILE_READONLY_AUDIT_V1",
        "repository": repository,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "open_pr_inventory": len(source), "prs_inspected": len(rows),
        "audit_errors": errors, "partial": partial,
        "unresolved_greptile_threads": sum(len(p["unresolved_threads"]) for p in rows),
        "greptile_credit_pauses": sum(p["credit_pause_observed"] for p in rows),
        "without_exact_head_greptile_review": len(not_exact),
        "pull_requests": rows,
        "release_approval": "NOT_ASSESSED_BY_THIS_AUDIT",
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="gregoryharper84-ship-it/WOW-Dashboard")
    parser.add_argument("--max-prs", type=int, default=0,
                        help="Diagnostic subset; always flagged partial if additional PRs exist")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = audit(GitHubReadOnly(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""),
                       args.repository, args.max_prs)
    except AuditUnavailable as exc:
        result = {"schema": "WOW_GREPTILE_READONLY_AUDIT_V1", "outcome": "AUDIT_INCOMPLETE",
                  "error": str(exc), "can_execute": False}
    content = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    return 0 if result["outcome"] == "NO_OPEN_GREPTILE_FINDINGS_IN_EXACT_HEAD_REVIEWS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
