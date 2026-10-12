#!/usr/bin/env python3
"""Keep wow-engineering[bot] PRs current against protected main (#1550/#1663 item 2).

Pure decision logic: no network I/O. The caller supplies the current list of open PRs
(as returned by `GET /repos/{repo}/pulls?state=open`) and this module decides which
ones need `PUT /pulls/{n}/update-branch`, using `expected_head_sha` so GitHub rejects
the call if the PR moved underneath it. Never touches PRs from any other author, never
merges, never enables bypass. can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

ENGINEERING_BOT_LOGIN = "wow-engineering[bot]"
PR_UPDATE_CONFLICT = "PR_UPDATE_CONFLICT"


def _author_login(pr: dict[str, Any]) -> str | None:
    user = pr.get("user") or {}
    return user.get("login")


def select_prs_to_update(
    prs: list[dict[str, Any]], *, bot_login: str = ENGINEERING_BOT_LOGIN
) -> list[dict[str, Any]]:
    """Return [{number, expected_head_sha}] for every eligible behind-main PR.

    Eligible means: open, non-draft, authored by `bot_login`, and
    `mergeable_state == "behind"`. Every other PR (including every PR from any other
    author, regardless of its mergeable_state) is left untouched.
    """
    plan: list[dict[str, Any]] = []
    for pr in prs:
        if pr.get("state") != "open":
            continue
        if pr.get("draft"):
            continue
        if _author_login(pr) != bot_login:
            continue
        if pr.get("mergeable_state") != "behind":
            continue
        head_sha = (pr.get("head") or {}).get("sha")
        number = pr.get("number")
        if not head_sha or number is None:
            continue
        plan.append({"number": number, "expected_head_sha": head_sha})
    return plan


def conflict_comment(pr_number: int) -> dict[str, Any]:
    body = (
        f"{PR_UPDATE_CONFLICT}: automatic branch-update skipped for PR #{pr_number} "
        "because GitHub reports a real merge conflict against protected main. "
        "Preserve this PR; do not close, supersede, or recreate it. A human or the "
        "recovery planner must resolve the conflict. can_execute=false."
    )
    return {"pr_number": pr_number, "finding": PR_UPDATE_CONFLICT, "body": body}


def classify_update_result(pr_number: int, *, status_code: int) -> dict[str, Any]:
    """Classify the HTTP status of `PUT /pulls/{n}/update-branch`.

    202 is GitHub's documented success response (update accepted, asynchronous).
    422/409 mean a real conflict: never retried here, routed to a typed comment instead.
    Anything else is an unresolved failure; the caller should not treat it as handled.
    """
    if status_code == 202:
        return {"pr_number": pr_number, "outcome": "UPDATED", "comment": None}
    if status_code in (409, 422):
        return {
            "pr_number": pr_number,
            "outcome": "CONFLICT",
            "comment": conflict_comment(pr_number),
        }
    return {"pr_number": pr_number, "outcome": "UNKNOWN_FAILURE", "comment": None}


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _cmd_plan(args: argparse.Namespace) -> int:
    prs = json.loads(_read_file(args.prs_json))
    plan = select_prs_to_update(prs, bot_login=args.bot_login)
    print(json.dumps(plan))
    return 0


def _cmd_conflict_comment(args: argparse.Namespace) -> int:
    print(json.dumps(conflict_comment(args.pr_number)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="Emit the update-branch plan as JSON.")
    plan_parser.add_argument("--prs-json", required=True)
    plan_parser.add_argument("--bot-login", default=ENGINEERING_BOT_LOGIN)
    plan_parser.set_defaults(func=_cmd_plan)

    comment_parser = sub.add_parser(
        "conflict-comment", help="Emit the typed PR_UPDATE_CONFLICT comment as JSON."
    )
    comment_parser.add_argument("--pr-number", required=True, type=int)
    comment_parser.set_defaults(func=_cmd_conflict_comment)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
