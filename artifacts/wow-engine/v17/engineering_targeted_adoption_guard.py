"""Fail-closed read-only admission for the EXISTING Engineering App adopter.

P0 #2136: a manual exact PR/SHA rescue must share the WIP and duplicate
invariants of automatic discovery, both before and inside the writer lock.
No App-token access, source authorship, approvals, merges or order execution.
"""
from __future__ import annotations

import argparse
import json

from v17 import engineering_authorized_adoption as adoption


def guard(pr_number: int, source_sha: str, repository: str) -> dict:
    if (repository != adoption.REPO or type(pr_number) is not int
            or pr_number <= 0 or not isinstance(source_sha, str)
            or not adoption.SHA.fullmatch(source_sha)):
        raise adoption.AdoptionHold("TARGET_ADOPTION_REQUEST_INVALID_HOLD")

    pending = 0
    for page in range(1, adoption.MAX_SOURCE_PAGES + 1):
        pulls = adoption.api(
            f"pulls?state=open&base=main&per_page=100&page={page}"
        )
        if not isinstance(pulls, list):
            raise adoption.AdoptionHold("TARGET_APP_WIP_INVENTORY_INVALID_HOLD")
        for pr in pulls:
            if not isinstance(pr, dict):
                raise adoption.AdoptionHold("TARGET_APP_WIP_ROW_INVALID_HOLD")
            author = pr.get("user") or {}
            head = pr.get("head") or {}
            if not isinstance(author, dict) or not isinstance(head, dict):
                raise adoption.AdoptionHold("TARGET_APP_WIP_ROW_INVALID_HOLD")
            if (author.get("login") == adoption.APP_AUTHOR
                    and str(head.get("ref") or "").startswith(
                        "wow-engineering/exact-replay-"
                    )):
                pending += 1
        if len(pulls) < 100:
            break
    else:
        raise adoption.AdoptionHold("TARGET_APP_WIP_INVENTORY_PAGE_CAP_HOLD")
    if pending >= 3:
        raise adoption.AdoptionHold("TARGET_APP_WIP_CAP_HOLD")

    branch = adoption.branch_for(pr_number, source_sha)
    existing = adoption.api(
        f"pulls?state=all&head={adoption.REPO.split('/')[0]}:{branch}&per_page=10"
    )
    if not isinstance(existing, list):
        raise adoption.AdoptionHold("TARGET_REPLAY_INVENTORY_INVALID_HOLD")
    if existing:
        raise adoption.AdoptionHold("TARGET_REPLAY_ALREADY_EXISTS_HOLD")
    return {"status": "TARGET_ADMISSION_PASS", "pr_number": pr_number,
            "source_sha": source_sha, "can_execute": False,
            "review_wip": pending}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-pr", required=True, type=int)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    print(json.dumps(guard(args.source_pr, args.source_sha, args.repo),
                     sort_keys=True))


if __name__ == "__main__":
    main()
