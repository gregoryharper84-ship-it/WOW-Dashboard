"""Simple V1 deterministic delivery reducer; evidence only, never a release authority.

Reads trusted, exact-head GitHub snapshots. It cannot review, authorize a
protected merge, deploy, place orders or invent fitted model probabilities.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = re.compile(r"[0-9a-f]{40}\Z")
QA_APP_ID = 5245832
RELEASE_APP_ID = 5245853
QA_CHECK = "WOW Independent Security Review"


def _result(code: str, next_action: str, trigger: str, *, pr: int | None = None,
            sha: str | None = None) -> dict:
    return {"decision": code, "next_action": next_action,
            "reentry_trigger": trigger, "pr": pr, "head_sha": sha,
            "can_execute": False, "review_approved": False,
            "merge_authorized": False, "probability_fitted": False}


def _valid_pr(pr: dict, expected_sha: str) -> bool:
    head, base = pr.get("head") or {}, pr.get("base") or {}
    if (type(pr.get("number")) is not int or pr["number"] <= 0
            or pr.get("state") != "open" or pr.get("draft") is True
            or not isinstance(expected_sha, str) or not SHA.fullmatch(expected_sha)):
        return False
    return (head.get("sha") == expected_sha
            and (head.get("repo") or {}).get("full_name") == REPO
            and base.get("ref") == "main"
            and (base.get("repo") or {}).get("full_name") == REPO)


def _latest(checks: list[dict]) -> dict[str, dict]:
    """Conservative duplicate handling: any conflicting result is a hold."""
    by_name: dict[str, dict] = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            raise ValueError("CHECK_INVENTORY_INVALID")
        name = check["name"]
        prev = by_name.get(name)
        if prev is None:
            by_name[name] = check
        elif (prev.get("app", {}).get("id") != check.get("app", {}).get("id")
              or prev.get("status") != check.get("status")
              or prev.get("conclusion") != check.get("conclusion")):
            raise ValueError("CONFLICTING_CHECK_EVIDENCE")
    return by_name


def reduce_pr(pr: dict, checks: list, expected_sha: str) -> dict:
    """Classify a pre-merge PR without turning a classification into authority."""
    if not isinstance(pr, dict) or not isinstance(checks, list) or len(checks) > 1000:
        raise ValueError("REVIEW_EVIDENCE_INVALID")
    number = pr.get("number") if type(pr.get("number")) is int else None
    if not _valid_pr(pr, expected_sha):
        return _result("SOURCE_HEAD_UNSTABLE", "RECHECK_EXACT_HEAD", "PR_HEAD_CHANGED",
                       pr=number, sha=expected_sha)
    by_name = _latest(checks)
    qa = by_name.get(QA_CHECK)
    if not qa:
        return _result("EVIDENCE_PENDING", "VERIFY_INDEPENDENT_REVIEW_DISPATCH",
                       "QA_APP_CHECK_PRESENT_AT_EXACT_SHA", pr=number, sha=expected_sha)
    if qa.get("app", {}).get("id") != QA_APP_ID:
        return _result("QA_IDENTITY_INVALID", "REPAIR_QA_APP_IDENTITY",
                       "QA_APP_IDENTITY_RESTORED", pr=number, sha=expected_sha)
    if qa.get("status") != "completed":
        return _result("EVIDENCE_PENDING", "FOLLOW_EXISTING_QA_RUN",
                       "QA_APP_CHECK_TERMINAL_AT_EXACT_SHA", pr=number, sha=expected_sha)
    if qa.get("conclusion") != "success":
        # A QA HOLD is not a semantic REJECT. Both preserve the binding gate.
        return _result("INDEPENDENT_QA_NOT_ACCEPTED", "DIAGNOSE_SIGNED_REVIEW_RECEIPT",
                       "NEW_VALID_QA_APP_EVIDENCE", pr=number, sha=expected_sha)
    # The Release App is an independent gate, not a consequence of QA PASS.
    release = [c for c in checks if (c.get("app") or {}).get("id") == RELEASE_APP_ID]
    if not release:
        return _result("RELEASE_EVIDENCE_PENDING", "FOLLOW_EXISTING_RELEASE_CHECK",
                       "RELEASE_APP_CHECK_AT_EXACT_SHA", pr=number, sha=expected_sha)
    if any(c.get("status") != "completed" or c.get("conclusion") != "success"
           for c in release):
        return _result("RELEASE_NOT_ACCEPTED", "REPAIR_RELEASE_FAILURE",
                       "VALID_RELEASE_APP_PASS", pr=number, sha=expected_sha)
    # Still no merge approval: protected native checks and CODEOWNER are separate.
    return _result("REVIEW_STAGE_ACCEPTED", "VERIFY_NATIVE_CHECKS_AND_OWNER_APPROVAL",
                   "EXACT_HEAD_PROTECTED_MERGE", pr=number, sha=expected_sha)


def reduce_completion(*, merge_sha: str | None, live_sha: str | None,
                      verified_sha: str | None, healthy_route: bool,
                      persisted_result: bool) -> dict:
    if not (merge_sha and SHA.fullmatch(merge_sha)):
        return _result("MERGE_NOT_VERIFIED", "OBTAIN_AUTHORIZED_MERGE", "MERGE_SHA_RECORDED")
    if live_sha != merge_sha:
        return _result("DEPLOY_MISMATCH", "REPAIR_CONTROLLED_DEPLOY", "LIVE_SHA_EQUALS_MERGED_SHA")
    if verified_sha != merge_sha or healthy_route is not True or persisted_result is not True:
        return _result("PRODUCTION_EVIDENCE_PENDING", "VERIFY_ROUTE_AND_PERSISTED_RESULT",
                       "REAL_PRODUCTION_SMOKE_PERSISTED")
    return _result("COMPLETE", "SELECT_NEXT_INCIDENT", "NEW_INCIDENT_AVAILABLE")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=Path, required=True)
    parser.add_argument("--checks", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    decision = reduce_pr(json.loads(args.pr.read_text()),
                         json.loads(args.checks.read_text()), args.head_sha)
    args.output.write_text(json.dumps(decision, sort_keys=True) + "\n")
    print(json.dumps(decision, sort_keys=True))


if __name__ == "__main__":
    main()
