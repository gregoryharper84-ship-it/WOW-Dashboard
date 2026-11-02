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
    """Choose only explicit newer check-run IDs; ambiguity is a HOLD.

    The caller fetches all check-runs, not only GitHub's latest filter, so
    a previously signed same-SHA security REJECT cannot be hidden.
    """
    by_name: dict[str, dict] = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            raise ValueError("CHECK_INVENTORY_INVALID")
        name = check["name"]
        prev = by_name.get(name)
        if prev is None:
            by_name[name] = check
            continue
        before, after = prev.get("id"), check.get("id")
        if type(before) is int and type(after) is int and before != after:
            if after > before:
                by_name[name] = check
        elif (prev.get("app", {}).get("id") != check.get("app", {}).get("id")
              or prev.get("status") != check.get("status")
              or prev.get("conclusion") != check.get("conclusion")):
            raise ValueError("CONFLICTING_CHECK_EVIDENCE")
    return by_name


def _signed_reject(check: dict) -> bool:
    output = check.get("output") or {}
    return (check.get("app", {}).get("id") == QA_APP_ID
            and check.get("conclusion") == "failure"
            and isinstance(output, dict)
            and isinstance(output.get("title"), str)
            and output["title"].startswith("REJECT"))


def _failed_qa(check: dict, number: int, head: str) -> dict:
    """Interpret only signed QA App presentation as a diagnostic, never PASS."""
    output = check.get("output") or {}
    title = output.get("title", "") if isinstance(output, dict) else ""
    summary = output.get("summary", "") if isinstance(output, dict) else ""
    # A REVIEWER MODEL or CREDIT error is not a source defect.
    reason_match = re.search(r"\breason (PROVIDER_[A-Z0-9_]+|HTTP_[0-9]{3}|OWNER_REVIEW_REQUIRED)\b",
                             summary if isinstance(summary, str) else "")
    reason = reason_match.group(1) if reason_match else ""
    if isinstance(title, str) and title.startswith("HOLD"):
        if reason in ("PROVIDER_MODEL_UNAVAILABLE", "PROVIDER_HTTP_404", "HTTP_404"):
            return _result("REVIEWER_MODEL_REPAIR", "VERIFY_REVIEWER_MODEL_AND_CREDENTIAL",
                           "VALID_PROVIDER_PREFLIGHT_AND_NEW_QA_RECEIPT", pr=number, sha=head)
        if reason in ("PROVIDER_CREDIT_EXHAUSTED", "PROVIDER_HTTP_401", "HTTP_401"):
            return _result("REVIEWER_AUTH_OR_QUOTA_REPAIR", "VERIFY_EXISTING_REVIEWER_ENTITLEMENT",
                           "AUTHORIZED_PROVIDER_AVAILABLE", pr=number, sha=head)
        if reason in ("PROVIDER_HTTP_429", "PROVIDER_HTTP_500", "PROVIDER_HTTP_502",
                      "PROVIDER_HTTP_503", "PROVIDER_HTTP_504", "PROVIDER_HTTP_529",
                      "PROVIDER_TRANSPORT"):
            return _result("REVIEWER_TRANSIENT_OUTAGE", "BOUNDED_RETRY_AFTER_RECOVERY",
                           "REVIEWER_PROVIDER_RECOVERED", pr=number, sha=head)
        if reason == "OWNER_REVIEW_REQUIRED":
            return _result("OWNER_REVIEW_REQUIRED", "EXACT_HEAD_AUTHENTICATED_OWNER_REVIEW",
                           "VALID_INDEPENDENT_OWNER_REVIEW", pr=number, sha=head)
    return _result("INDEPENDENT_QA_NOT_ACCEPTED", "DIAGNOSE_SIGNED_REVIEW_RECEIPT",
                   "NEW_VALID_QA_APP_EVIDENCE", pr=number, sha=head)

def reduce_pr(pr: dict, checks: list, expected_sha: str) -> dict:
    """Classify a pre-merge PR without turning a classification into authority."""
    if not isinstance(pr, dict) or not isinstance(checks, list) or len(checks) > 1000:
        raise ValueError("REVIEW_EVIDENCE_INVALID")
    number = pr.get("number") if type(pr.get("number")) is int else None
    if not _valid_pr(pr, expected_sha):
        return _result("SOURCE_HEAD_UNSTABLE", "RECHECK_EXACT_HEAD", "PR_HEAD_CHANGED",
                       pr=number, sha=expected_sha)
    # All historical QA checks are considered before taking the newest
    # check-run; a signed REJECT at this SHA is permanent until source changes.
    qa_checks = [c for c in checks if c.get("name") == QA_CHECK]
    if any(c.get("app", {}).get("id") != QA_APP_ID for c in qa_checks):
        return _result("QA_IDENTITY_INVALID", "REPAIR_QA_APP_IDENTITY",
                       "QA_APP_IDENTITY_RESTORED", pr=number, sha=expected_sha)
    if any(_signed_reject(c) for c in qa_checks):
        return _result("SEMANTIC_REJECT_SAME_SHA", "REPAIR_REJECTED_SOURCE_NEW_SHA",
                       "NEW_SOURCE_SHA", pr=number, sha=expected_sha)
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
        return _failed_qa(qa, number, expected_sha)
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
