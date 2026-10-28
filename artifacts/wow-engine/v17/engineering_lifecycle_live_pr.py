"""Validate current GitHub PR metadata for V17 lifecycle attestations.

The workflow obtains its own GitHub REST response using a read-only token.
This pure module is deliberately network-free, approval-free, and cannot alter
a PR, terminal decision, lease, betting market, or model probability.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}\Z")


class LivePRMetadataHold(ValueError):
    """An exact-current PR metadata prerequisite has not been proven."""


def validated_snapshot(
    payload: Any, *, repository: str, number: int, head_sha: str
) -> dict[str, Any]:
    """Fail closed on wrong PR, head, repository, base, state or malformed body."""
    if not isinstance(payload, dict):
        raise LivePRMetadataHold("LIFECYCLE_LIVE_PR_PAYLOAD_INVALID")
    if (
        not isinstance(repository, str) or not repository
        or type(number) is not int or number <= 0
        or not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha)
    ):
        raise LivePRMetadataHold("LIFECYCLE_EXPECTED_IDENTITY_INVALID")

    head = payload.get("head")
    base = payload.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise LivePRMetadataHold("LIFECYCLE_LIVE_PR_IDENTITY_INVALID")
    head_repo = head.get("repo")
    base_repo = base.get("repo")
    if not isinstance(head_repo, dict) or not isinstance(base_repo, dict):
        raise LivePRMetadataHold("LIFECYCLE_LIVE_PR_REPOSITORY_UNVERIFIED")
    if (
        type(payload.get("number")) is not int or payload["number"] != number
        or head.get("sha") != head_sha
        or head_repo.get("full_name") != repository
        or base_repo.get("full_name") != repository
        or base.get("ref") != "main"
    ):
        raise LivePRMetadataHold("LIFECYCLE_LIVE_PR_IDENTITY_MISMATCH")
    if payload.get("state") != "open" or payload.get("draft") is not False:
        raise LivePRMetadataHold("LIFECYCLE_LIVE_PR_NOT_REVIEWABLE")

    body = payload.get("body")
    if body is not None and not isinstance(body, str):
        raise LivePRMetadataHold("LIFECYCLE_LIVE_PR_BODY_INVALID")
    return {
        "number": number,
        "head_sha": head_sha,
        "repository": repository,
        "body": body or "",
        "state": "open",
        "draft": False,
    }


def verify_unchanged(first: dict[str, Any], second: dict[str, Any]) -> None:
    """Do not certify an attestation that changed during the classification."""
    if first != second:
        raise LivePRMetadataHold("LIFECYCLE_LIVE_PR_CHANGED_DURING_AUDIT")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr-json", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--body-output", type=Path)
    parser.add_argument("--recheck-json", type=Path)
    args = parser.parse_args()

    first = validated_snapshot(
        json.loads(args.pr_json.read_text(encoding="utf-8")),
        repository=args.repository, number=args.number, head_sha=args.head_sha,
    )
    if args.recheck_json is not None:
        second = validated_snapshot(
            json.loads(args.recheck_json.read_text(encoding="utf-8")),
            repository=args.repository, number=args.number, head_sha=args.head_sha,
        )
        verify_unchanged(first, second)
    elif args.body_output is not None:
        args.body_output.write_text(first["body"], encoding="utf-8")
    else:
        parser.error("provide --body-output or --recheck-json")
    print("LIFECYCLE_LIVE_PR_METADATA_VERIFIED")


if __name__ == "__main__":
    main()
