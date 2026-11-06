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
INDEPENDENT_QA_CHECK = "WOW Independent QA exact-head"
RELEASE_CHECK = "WOW Release Authority exact-head"
# The active protected ruleset has five authenticated exact-head checks.
# The standalone historical semantic review is manual-only and NOT a sixth
# required check; a signed same-SHA REJECT remains a binding safety veto.
ACTIONS_APP_ID = 15368
NATIVE_REGRESSION_CHECKS = (
    "WOW required-three regression",
    "WOW governed probability backend",
    "WOW additional required regression",
)



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
        elif ((prev.get("app") or {}).get("id") != (check.get("app") or {}).get("id")
              or prev.get("status") != check.get("status")
              or prev.get("conclusion") != check.get("conclusion")):
            raise ValueError("CONFLICTING_CHECK_EVIDENCE")
    return by_name


def _signed_reject(check: dict) -> bool:
    output = check.get("output") or {}
    return ((check.get("app") or {}).get("id") == QA_APP_ID
            and check.get("conclusion") == "failure"
            and isinstance(output, dict)
            and isinstance(output.get("title"), str)
            and output["title"].startswith("REJECT"))


def _failed_qa(check: dict, number: int, head: str) -> dict:
    """Interpret only signed QA App presentation as a diagnostic, never PASS."""
    output = check.get("output") or {}
    title = output.get("title", "") if isinstance(output, dict) else ""
    summary = output.get("summary", "") if isinstance(output, dict) else ""
    # Only the signed check's top-level reason is diagnostic. The actual
    # reviewer records provider-level attempts in a separate retained artifact;
    # never infer an available model, credit, or retry from an owner-fallback
    # summary that has no authenticated per-attempt evidence.
    reason_match = re.search(
        r"\breason(?:\s+|[=:]\s*)(PROVIDER_[A-Z0-9_]+|HTTP_[0-9]{3}|"
        r"OWNER_REVIEW_REQUIRED|OWNER_FALLBACK_REQUIRES_ENGINEERING_APP_SOURCE)\b",
        summary if isinstance(summary, str) else "",
    )
    reason = reason_match.group(1) if reason_match else ""
    if isinstance(title, str) and title.startswith("HOLD"):
        if reason == "OWNER_FALLBACK_REQUIRES_ENGINEERING_APP_SOURCE":
            return _result(
                "INDEPENDENT_QA_NOT_ACCEPTED",
                "VERIFY_INDEPENDENT_SOURCE_AND_RETAINED_REVIEW_ATTEMPTS",
                "NONOWNER_SOURCE_AND_VALID_QA_RECEIPT", pr=number, sha=head,
            )
        if reason == "PROVIDER_MODEL_UNAVAILABLE":
            return _result("REVIEWER_MODEL_REPAIR", "VERIFY_REVIEWER_MODEL_AND_CREDENTIAL",
                           "VALID_PROVIDER_PREFLIGHT_AND_NEW_QA_RECEIPT", pr=number, sha=head)
        # Bare HTTP 404 is *ambiguous*: endpoint contract, model and credential
        # scopes cannot be distinguished without authenticated evidence.
        if reason in ("PROVIDER_HTTP_404", "HTTP_404"):
            return _result("REVIEWER_PROVIDER_DIAGNOSIS", "VERIFY_ENDPOINT_MODEL_AND_SCOPE",
                           "AUTHORIZED_PROVIDER_COMPATIBILITY_PROVEN", pr=number, sha=head)
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

def _five_check_continuation(checks: list, by_name: dict, number: int,
                             expected_sha: str) -> dict | None:
    """Describe the actual protected five-check review stage, never approve.

    If any current pinned context is present, evaluate that cohort; do not
    require the retired manual semantic-review workflow to provide a PASS.
    An incomplete cohort is still a HOLD. All positive results remain
    evidence-only (merge_authorized=False).
    """
    expected_apps = {
        **{name: ACTIONS_APP_ID for name in NATIVE_REGRESSION_CHECKS},
        INDEPENDENT_QA_CHECK: QA_APP_ID,
        RELEASE_CHECK: RELEASE_APP_ID,
    }
    if not any(name in by_name for name in expected_apps):
        return None

    # Retain the entire check inventory's identity and exact-head safety.
    # A newer valid check must not silently erase an earlier foreign publisher.
    for check in checks:
        name = check["name"]
        if name not in expected_apps:
            continue
        if ((check.get("app") or {}).get("id") != expected_apps[name]
                or check.get("head_sha") != expected_sha):
            return _result("PROTECTED_CHECK_IDENTITY_INVALID",
                           "REPAIR_PINNED_CHECK_PRODUCER",
                           "VALID_EXACT_HEAD_PINNED_CHECK", pr=number, sha=expected_sha)

    for name in NATIVE_REGRESSION_CHECKS:
        row = by_name.get(name)
        if row is None or row.get("status") != "completed":
            return _result("NATIVE_REGRESSION_PENDING", "FOLLOW_REQUIRED_REGRESSION",
                           "EXACT_HEAD_NATIVE_REGRESSIONS_COMPLETE", pr=number,
                           sha=expected_sha)
        if row.get("conclusion") != "success":
            return _result("NATIVE_REGRESSION_NOT_ACCEPTED", "REPAIR_REGRESSION_FAILURE",
                           "NEW_EXACT_HEAD_REGRESSION_PASS", pr=number,
                           sha=expected_sha)

    qa = by_name.get(INDEPENDENT_QA_CHECK)
    if qa is None:
        return _result("QA_GATE_PENDING", "FOLLOW_EXISTING_QA_CHECK",
                       "QA_EXACT_HEAD_PASS", pr=number, sha=expected_sha)
    if qa.get("status") != "completed" or qa.get("conclusion") != "success":
        return _result("QA_GATE_NOT_ACCEPTED", "REPAIR_INDEPENDENT_QA_GATE",
                       "QA_EXACT_HEAD_PASS", pr=number, sha=expected_sha)

    release = by_name.get(RELEASE_CHECK)
    if release is None:
        return _result("RELEASE_EVIDENCE_PENDING", "FOLLOW_EXISTING_RELEASE_CHECK",
                       "RELEASE_APP_CHECK_AT_EXACT_SHA", pr=number, sha=expected_sha)
    if release.get("status") != "completed" or release.get("conclusion") != "success":
        return _result("RELEASE_NOT_ACCEPTED", "REPAIR_RELEASE_FAILURE",
                       "VALID_RELEASE_APP_PASS", pr=number, sha=expected_sha)

    # A valid source review / protected merge authorization / production
    # deployment must still be independently established; this is read-only.
    return _result("REVIEW_STAGE_ACCEPTED", "VERIFY_NATIVE_CHECKS_AND_OWNER_APPROVAL",
                   "EXACT_HEAD_PROTECTED_MERGE", pr=number, sha=expected_sha)


def reduce_pr(pr: dict, checks: list, expected_sha: str) -> dict:
    """Classify a pre-merge PR without turning a classification into authority."""
    if not isinstance(pr, dict) or not isinstance(checks, list) or len(checks) > 1000:
        raise ValueError("REVIEW_EVIDENCE_INVALID")
    # Validate before .get/filter/_latest; malformed evidence yields a typed
    # fail-closed decision rather than crashing without an output receipt.
    for row in checks:
        if (not isinstance(row, dict) or not isinstance(row.get("name"), str)
                or (row.get("app") is not None and not isinstance(row["app"], dict))
                or (row.get("output") is not None and not isinstance(row["output"], dict))):
            raise ValueError("CHECK_INVENTORY_INVALID")
    number = pr.get("number") if type(pr.get("number")) is int else None
    if not _valid_pr(pr, expected_sha):
        return _result("SOURCE_HEAD_UNSTABLE", "RECHECK_EXACT_HEAD", "PR_HEAD_CHANGED",
                       pr=number, sha=expected_sha)
    # All historical QA checks are considered before taking the newest
    # check-run; a signed REJECT at this SHA is permanent until source changes.
    qa_checks = [c for c in checks if c.get("name") == QA_CHECK]
    if any((c.get("app") or {}).get("id") != QA_APP_ID for c in qa_checks):
        return _result("QA_IDENTITY_INVALID", "REPAIR_QA_APP_IDENTITY",
                       "QA_APP_IDENTITY_RESTORED", pr=number, sha=expected_sha)
    if any(_signed_reject(c) for c in qa_checks):
        return _result("SEMANTIC_REJECT_SAME_SHA", "REPAIR_REJECTED_SOURCE_NEW_SHA",
                       "NEW_SOURCE_SHA", pr=number, sha=expected_sha)
    by_name = _latest(checks)
    current = _five_check_continuation(checks, by_name, number, expected_sha)
    if current is not None:
        return current
    # Legacy-only evidence is diagnostic, not a substitute for the five
    # authenticated ruleset checks. Preserve its signed HOLD/REJECT reasons.
    qa = by_name.get(QA_CHECK)
    if not qa:
        return _result("EVIDENCE_PENDING", "VERIFY_INDEPENDENT_REVIEW_DISPATCH",
                       "QA_APP_CHECK_PRESENT_AT_EXACT_SHA", pr=number, sha=expected_sha)
    if (qa.get("app") or {}).get("id") != QA_APP_ID:
        return _result("QA_IDENTITY_INVALID", "REPAIR_QA_APP_IDENTITY",
                       "QA_APP_IDENTITY_RESTORED", pr=number, sha=expected_sha)
    if qa.get("status") != "completed":
        return _result("EVIDENCE_PENDING", "FOLLOW_EXISTING_QA_RUN",
                       "QA_APP_CHECK_TERMINAL_AT_EXACT_SHA", pr=number, sha=expected_sha)
    if qa.get("conclusion") != "success":
        return _failed_qa(qa, number, expected_sha)
    # The QA App must actually publish a PASS, not merely a success conclusion
    # paired with an incompatible HOLD/REJECT title.
    qa_title = (qa.get("output") or {}).get("title")
    if not isinstance(qa_title, str) or not qa_title.startswith("PASS"):
        return _result("INDEPENDENT_QA_NOT_ACCEPTED", "DIAGNOSE_SIGNED_REVIEW_RECEIPT",
                       "NEW_VALID_QA_APP_EVIDENCE", pr=number, sha=expected_sha)

    # No pinned contexts were present, so legacy-only evidence can diagnose
    # the old review but cannot satisfy the current exact-head release policy.
    return _result("QA_GATE_PENDING", "FOLLOW_EXISTING_QA_CHECK",
                   "QA_EXACT_HEAD_PASS", pr=number, sha=expected_sha)

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
    try:
        decision = reduce_pr(json.loads(args.pr.read_text()),
                             json.loads(args.checks.read_text()), args.head_sha)
    except (ValueError, TypeError):
        decision = _result("REVIEW_EVIDENCE_INVALID",
                           "RECHECK_REVIEW_EVIDENCE", "VALID_REVIEW_EVIDENCE")
    args.output.write_text(json.dumps(decision, sort_keys=True) + "\n")
    print(json.dumps(decision, sort_keys=True))


if __name__ == "__main__":
    main()
