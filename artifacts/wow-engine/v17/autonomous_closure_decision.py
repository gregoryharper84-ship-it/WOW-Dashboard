"""Fail-closed transition decisions for the existing WOW engineering lifecycle.

Pure policy: GitHub/Render/Supabase adapters must authenticate source facts.
This module never grants reviews, merges, deploys, or closes incidents.
"""
from __future__ import annotations
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
TERMINAL = {"FIXED_AND_VERIFIED"}
ALLOWED = {"CI_PENDING", "CERTIFY", "REVIEW_PENDING", "RELEASE_PENDING",
           "QA_PENDING", "CLOSE", "BLOCKED_WITH_EXACT_REASON"}

def decide(facts: dict) -> dict:
    def blocked(reason: str) -> dict:
        return {"action": "BLOCKED_WITH_EXACT_REASON", "reason": reason}
    if not isinstance(facts, dict):
        return blocked("UNAUTHENTICATED_STATE")
    if facts.get("authenticated") is not True:
        return blocked("UNAUTHENTICATED_STATE")
    head = facts.get("head_sha")
    if not isinstance(head, str) or not SHA.fullmatch(head):
        return blocked("INVALID_HEAD_SHA")
    if facts.get("base") != "main" or facts.get("head_repo") != facts.get("repo"):
        return blocked("UNTRUSTED_PR_PROVENANCE")
    if facts.get("state") != "open":
        if facts.get("merged") is not True:
            return blocked("PR_NOT_MERGED")
        merged = facts.get("merged_sha")
        if not isinstance(merged, str) or not SHA.fullmatch(merged):
            return blocked("MERGE_SHA_UNVERIFIED")
        if facts.get("deployment_authenticated") is not True or facts.get("deployed_sha") != merged:
            return {"action": "RELEASE_PENDING", "reason": "DEPLOYED_SHA_UNVERIFIED"}
        if facts.get("qa_authenticated") is not True or facts.get("qa_sha") != merged:
            return {"action": "QA_PENDING", "reason": "INDEPENDENT_QA_UNVERIFIED"}
        if facts.get("reconciliation_authenticated") is not True:
            return blocked("RECONCILIATION_UNVERIFIED")
        return {"action": "CLOSE", "reason": "AUTHENTICATED_EVIDENCE_REQUIRED_UPSTREAM"}
    if facts.get("draft") is True:
        return blocked("DRAFT_PR")
    if facts.get("ci_head_sha") != head or facts.get("ci_state") not in {"success", "pending", "failure"}:
        return blocked("CI_EVIDENCE_MISSING_OR_STALE")
    if facts["ci_state"] == "pending":
        return {"action": "CI_PENDING", "reason": "CHECKS_IN_PROGRESS"}
    if facts["ci_state"] == "failure":
        return blocked("CI_FAILED_REPAIR_REQUIRED")
    if facts.get("certified_sha") != head:
        return {"action": "CERTIFY", "reason": "EXACT_HEAD_CERTIFICATION_REQUIRED"}
    if facts.get("reviewer_authorized") is not True or facts.get("reviewer_independent") is not True:
        return blocked("REVIEWER_NOT_CONFIGURED")
    if facts.get("approved_sha") != head:
        return {"action": "REVIEW_PENDING", "reason": "INDEPENDENT_APPROVAL_REQUIRED"}
    return {"action": "RELEASE_PENDING", "reason": "PROTECTED_RELEASE_GATES_APPLY"}

if __name__ == "__main__":
    try:
        result = decide(json.load(sys.stdin))
    except (ValueError, TypeError):
        result = {"action": "BLOCKED_WITH_EXACT_REASON", "reason": "INVALID_STATE_INPUT"}
    print(json.dumps(result, sort_keys=True))
