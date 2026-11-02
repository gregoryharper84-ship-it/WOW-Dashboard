"""Deterministic, no-model engineering work selection for 24x7 GitHub intake.

Inputs are read-only GitHub API snapshots from a trusted main-branch workflow.
This module NEVER executes code, approves changes, merges or authorizes wagers.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

INCIDENT_LINE = re.compile(
    r"(?im)^\s*(?:Incident|Fixes|Closes|Resolves)\s*:\s*#([1-9][0-9]*)\b"
)
EXPLICIT_LINK = re.compile(r"(?im)^\s*(?:Fixes|Closes|Resolves)\s+#([1-9][0-9]*)\b")
SHA = re.compile(r"^[0-9a-f]{40}$")
STOP_LABELS = {
    "needs-owner", "blocked", "security-hold", "do-not-automate",
    "no-autobuild", "no-auto-build", "duplicate", "wontfix",
}
PRIORITY = {
    "production-down": 0,
    "incident-p0": 0,
    "p0": 0,
    "production-restore": 1,
    "p1": 1,
    "incident-p1": 1,
    "p2": 2,
    "approved-improvement": 3,
    "engineering-improvement": 3,
}
MAX_ISSUES = 5000
MAX_PRS = 2000


def labels(item: dict) -> set[str]:
    result = set()
    for value in item.get("labels", []):
        name = value.get("name") if isinstance(value, dict) else value
        if isinstance(name, str):
            result.add(name.strip().lower())
    return result


def eligible(issue: dict) -> tuple[int, int] | None:
    if not isinstance(issue, dict) or "pull_request" in issue:
        return None
    if issue.get("state") != "open":
        return None
    number = issue.get("number")
    if type(number) is not int or number <= 0:
        return None
    tags = labels(issue)
    if tags & STOP_LABELS:
        return None
    priorities = [PRIORITY[tag] for tag in tags if tag in PRIORITY]
    if not priorities:
        return None
    # Sensitively scoped changes get an existing independent review before merge;
    # selection itself never grants that approval.
    return min(priorities), number


def related_incidents(pr: dict) -> set[int]:
    body = pr.get("body") or ""
    if not isinstance(body, str):
        return set()
    return {int(n) for n in INCIDENT_LINE.findall(body) + EXPLICIT_LINK.findall(body)}


def valid_pr(pr: dict) -> bool:
    if not isinstance(pr, dict) or pr.get("state") != "open":
        return False
    number = pr.get("number")
    if type(number) is not int or number <= 0:
        return False
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    return (
        isinstance(head, dict) and isinstance(base, dict)
        and isinstance(head.get("sha"), str)
        and SHA.fullmatch(head["sha"]) is not None
        and base.get("ref") == "main"
        and isinstance(head.get("repo"), dict)
        and isinstance(base.get("repo"), dict)
        and head["repo"].get("full_name") ==
        base["repo"].get("full_name")
    )


def plan(issues: list, prs: list, *, target: int | None = None) -> dict:
    """Prefer P0/P1 restoration; resume existing PR, never duplicate its writer."""
    if not isinstance(issues, list) or not isinstance(prs, list):
        raise ValueError("inventory is not a pair of arrays")
    if len(issues) > MAX_ISSUES or len(prs) > MAX_PRS:
        raise ValueError("inventory exceeded bounded scan; refuse truncated data")
    if target is not None and (type(target) is not int or target <= 0):
        raise ValueError("target must be a positive numeric incident ID")

    ranked = []
    for issue in issues:
        priority = eligible(issue)
        if priority and (target is None or priority[1] == target):
            ranked.append((priority, issue))
    ranked.sort(key=lambda row: row[0])
    for (tier, number), _issue in ranked:
        related = [pr for pr in prs if valid_pr(pr) and number in related_incidents(pr)]
        if related:
            pr = min(related, key=lambda row: row["number"])
            return {
                "action": "RESUME_PR", "incident": number,
                "pr": pr["number"], "head_sha": pr["head"]["sha"],
                "priority": tier, "reason": "EXISTING_PR_REQUIRES_EXACT_HEAD_GATES",
                "can_execute": False,
            }
        return {
            "action": "BUILD", "incident": number, "pr": None,
            "head_sha": None, "priority": tier,
            "reason": "NEXT_ELIGIBLE_PRIORITY_ISSUE", "can_execute": False,
        }
    return {
        "action": "IDLE", "incident": None, "pr": None,
        "head_sha": None, "priority": None,
        "reason": "NO_ELIGIBLE_ISSUE_OR_EXPLICIT_TARGET", "can_execute": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issues", type=Path, required=True)
    parser.add_argument("--prs", type=Path, required=True)
    parser.add_argument("--target", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = plan(
        json.loads(args.issues.read_text(encoding="utf-8")),
        json.loads(args.prs.read_text(encoding="utf-8")),
        target=args.target,
    )
    args.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
