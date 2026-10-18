"""Fail-closed, read-only fallback for independent existing-PR review.

This module cannot approve a pull request or emit a trusted GitHub status check.
Only a protected-main, independently certified workflow may consume the decision.
Its inputs must be collected live from authenticated GitHub API calls, not PR files.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[a-f0-9]{40}$")
CANONICAL_REPO = "gregoryharper84-ship-it/WOW-Dashboard"
ACTIONS_APP = 15368
REQUIRED_REGRESSION = (
    "WOW required-three regression",
    "WOW governed probability backend",
    "WOW additional required regression",
)
# This is supplemental defense-in-depth. The trusted workflow's canonical
# agent_identity_policy.governance_trust_roots inventory must also reject any
# other protected read/executed files in the changed-file set.
DENIED_PREFIXES = (".github/", ".agents/")
PROTECTED_NAMED = frozenset({
    "artifacts/wow-engine/v17/agent_identity_policy.py",
    "artifacts/wow-engine/v17/engineering_agent_team.py",
    "artifacts/wow-engine/v17/engineering_dispatch_manifest.json",
    "artifacts/wow-engine/v17/engineering_dispatch_queue.py",
    "artifacts/wow-engine/v17/engineering_incident_identity.py",
    "artifacts/wow-engine/v17/engineering_effectiveness.py",
    "artifacts/wow-engine/v17/engineering_provider_failover.py",
    "artifacts/wow-engine/v17/independent_release_checks.py",
    "artifacts/wow-engine/v17/persistent_worker_safety.py",
    "artifacts/wow-engine/requirements.txt",
    "artifacts/flask-scoring-api/requirements.txt",
    "render.yaml",
})
DISPOSITION = "BLOCKED_WITH_EXACT_REASON"
# Time-box the temporary provider substitution; renewal requires protected review.
FALLBACK_EXPIRES_UTC = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


def _hold(reason: str, *, head: str = "") -> dict[str, Any]:
    return {
        "state": "HOLD", "code": reason, "head_sha": head,
        "review_evidence_accepted": False,
        "independent_review_complete": False,
        "qa_release_complete": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "disposition": DISPOSITION,
    }


def _dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _approval(reviews: Sequence[Mapping[str, Any]], *, author: str, sha: str,
              trusted_reviewers: frozenset[str]) -> str | None:
    """Require latest state of one explicitly trusted *independent* principal.

    A prior APPROVED followed by CHANGES_REQUESTED is not accepted, nor is an
    APPROVED on a stale commit, nor a self-review, nor a review from an unknown
    login. GitHub must return a COMPLETE, nontruncated set of review records.
    """
    latest: dict[str, Mapping[str, Any]] = {}
    for item in reviews:
        reviewer = (item.get("user") or {}).get("login")
        submitted = _dt(item.get("submitted_at"))
        if not isinstance(reviewer, str) or not reviewer or submitted is None:
            raise ValueError("REVIEW_INVENTORY_INVALID")
        current = latest.get(reviewer)
        try:
            review_id = int(item.get("id"))
            current_id = int(current.get("id")) if current else -1
        except (TypeError, ValueError) as exc:
            raise ValueError("REVIEW_ID_INVALID") from exc
        if review_id < 1:
            raise ValueError("REVIEW_ID_INVALID")
        if (not current or (submitted, review_id) >=
            (_dt(current["submitted_at"]), current_id)):
            latest[reviewer] = item
    for reviewer in sorted(trusted_reviewers):
        if reviewer == author or not reviewer:
            continue
        item = latest.get(reviewer)
        if not item or item.get("state") != "APPROVED":
            continue
        if item.get("commit_id") == sha:
            return reviewer
    return None


def evaluate(*, repository: str, expected_sha: str, pr: Mapping[str, Any],
             changed_files: Sequence[Mapping[str, Any]],
             reviews: Sequence[Mapping[str, Any]], checks: Sequence[Mapping[str, Any]],
             trusted_reviewers: frozenset[str], complete_inventory: bool,
             now: datetime | None = None) -> dict[str, Any]:
    """Advisory result only; NEVER synthesizes a required QA/Release check.

    Independent CODEOWNER/Security review of trust-root workflow changes
    remains mandatory before this module is even considered for installation.
    """
    clock = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        return _hold("CLOCK_NOT_TRUSTED", head=expected_sha)
    if clock >= FALLBACK_EXPIRES_UTC:
        return _hold("TEMPORARY_FALLBACK_EXPIRED", head=expected_sha)
    if not complete_inventory:
        return _hold("REVIEW_OR_CI_INVENTORY_INCOMPLETE", head=expected_sha)
    if repository != CANONICAL_REPO or ((pr.get("base") or {}).get("repo") or {}).get("full_name") != repository:
        return _hold("REPOSITORY_IDENTITY_MISMATCH", head=expected_sha)
    if not SHA.fullmatch(expected_sha):
        return _hold("EXPECTED_SHA_INVALID")
    head = pr.get("head") or {}
    if (head.get("sha") != expected_sha or (head.get("repo") or {}).get("full_name") != repository
            or (pr.get("base") or {}).get("ref") != "main"):
        return _hold("PR_SHA_REPO_OR_BASE_MISMATCH", head=expected_sha)
    if pr.get("state") != "open" or pr.get("draft") or pr.get("merged_at"):
        return _hold("PR_NOT_OPEN_READY", head=expected_sha)
    author = (pr.get("user") or {}).get("login")
    if not isinstance(author, str) or not author:
        return _hold("PR_AUTHOR_UNKNOWN", head=expected_sha)
    if not changed_files or len(changed_files) != int(pr.get("changed_files", -1)):
        return _hold("DIFF_INVENTORY_MISMATCH", head=expected_sha)
    for item in changed_files:
        for path in (item.get("filename"), item.get("previous_filename")):
            if path is None:
                continue
            if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/"):
                return _hold("DIFF_PATH_INVALID", head=expected_sha)
            if path.startswith(DENIED_PREFIXES) or path in PROTECTED_NAMED:
                return _hold("TRUST_ROOT_REQUIRES_PROTECTED_AUTHORIZATION", head=expected_sha)
    if not trusted_reviewers or author in trusted_reviewers and len(trusted_reviewers) == 1:
        return _hold("INDEPENDENT_REVIEWER_NOT_CONFIGURED", head=expected_sha)
    if not checks:
        return _hold("REQUIRED_CI_MISSING", head=expected_sha)
    for name in REQUIRED_REGRESSION:
        matches = [c for c in checks if c.get("name") == name and
                   c.get("head_sha") == expected_sha and
                   (c.get("app") or {}).get("id") == ACTIONS_APP]
        if not matches:
            return _hold("REQUIRED_CI_MISSING:" + name, head=expected_sha)
        try:
            latest = max(matches, key=lambda c: (str(c.get("started_at") or ""), int(c.get("id"))))
        except (TypeError, ValueError):
            return _hold("CI_EVIDENCE_MALFORMED", head=expected_sha)
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            return _hold("REQUIRED_CI_NOT_GREEN:" + name, head=expected_sha)
    try:
        reviewer = _approval(reviews, author=author, sha=expected_sha,
                             trusted_reviewers=trusted_reviewers)
    except ValueError as exc:
        return _hold(str(exc), head=expected_sha)
    if reviewer is None:
        return _hold("EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING", head=expected_sha)
    return {
        "state": "REVIEW_EVIDENCE_VERIFIED",
        "code": "INDEPENDENT_PR_APPROVAL_AT_EXACT_HEAD",
        "repository": repository,
        "head_sha": expected_sha,
        "reviewer": reviewer,
        "review_evidence_accepted": True,
        "independent_review_complete": True,
        "qa_release_complete": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "disposition": DISPOSITION,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--trusted-reviewer", action="append", default=[])
    parser.add_argument("--repository", default=CANONICAL_REPO)
    args = parser.parse_args()
    data = json.loads(args.evidence.read_text(encoding="utf-8"))
    result = evaluate(
        repository=args.repository, expected_sha=args.expected_sha,
        pr=data.get("pr") or {}, changed_files=data.get("changed_files") or [],
        reviews=data.get("reviews") or [], checks=data.get("check_runs") or [],
        trusted_reviewers=frozenset(args.trusted_reviewer),
        complete_inventory=data.get("complete_inventory") is True,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["state"] == "REVIEW_EVIDENCE_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
