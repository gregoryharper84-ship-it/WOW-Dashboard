"""Inert, deterministic release-admission rule for an August-scale release design.

This function checks *caller-supplied claims*, not their authenticity. It does
not read GitHub or Render, attest privileges, acquire leases, or issue a deploy.
Only an independently authenticated release writer can gather authoritative
evidence, and this rule is never a release authorization.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SHA = re.compile(r"^[a-f0-9]{40}$")
REQUIRED = frozenset({
    "WOW governed probability backend",
    "WOW required-three regression",
    "WOW additional required regression",
})


@dataclass(frozen=True)
class ReleaseFacts:
    target_sha: str
    main_sha: str
    checks: dict[str, tuple[str, str]]  # claimed check name -> (SHA, conclusion)
    qa_approved: bool
    release_approved: bool
    approver_independent: bool
    credential_scope_attested: bool
    service_id_attested: bool
    writer_count: int
    release_lease_owned: bool
    intent_reserved: bool
    provider_ack_uncertain: bool
    can_execute: bool = False


def _valid_sha(value: object) -> bool:
    return type(value) is str and SHA.fullmatch(value) is not None


def _check_claims_match(facts: ReleaseFacts) -> bool:
    if type(facts.checks) is not dict or not _valid_sha(facts.target_sha):
        return False
    for name in REQUIRED:
        receipt = facts.checks.get(name)
        if (
            type(receipt) is not tuple
            or len(receipt) != 2
            or type(receipt[0]) is not str
            or type(receipt[1]) is not str
            or receipt != (facts.target_sha, "success")
        ):
            return False
    return True


def admission(facts: ReleaseFacts) -> dict[str, Any]:
    """Fail closed on malformed claims. Never grant operational deploy authority."""
    if not isinstance(facts, ReleaseFacts):
        return {
            "status": "HOLD",
            "reasons": ["RELEASE_FACTS_INVALID"],
            "deploy": False,
            "can_execute": False,
        }

    reasons: list[str] = []
    if not _valid_sha(facts.target_sha) or (
        not _valid_sha(facts.main_sha) or facts.target_sha != facts.main_sha
    ):
        reasons.append("RELEASE_EXACT_MAIN_SHA_INVALID_OR_STALE")
    if not _check_claims_match(facts):
        reasons.append("RELEASE_AUGUST_CHECKS_NOT_VERIFIED")
    if not (
        facts.qa_approved is True
        and facts.release_approved is True
        and facts.approver_independent is True
    ):
        reasons.append("RELEASE_INDEPENDENT_AUTHORIZATION_MISSING")
    if not (
        facts.credential_scope_attested is True
        and facts.service_id_attested is True
    ):
        reasons.append("RELEASE_PROVIDER_IDENTITY_UNATTESTED")
    if type(facts.writer_count) is not int or facts.writer_count != 1 or (
        facts.release_lease_owned is not True
    ):
        reasons.append("RELEASE_SINGLE_WRITER_UNVERIFIED")
    if facts.intent_reserved is not True:
        reasons.append("RELEASE_DURABLE_INTENT_MISSING")
    if facts.provider_ack_uncertain is not False:
        reasons.append("RELEASE_PROVIDER_ACK_UNCERTAIN_NO_RETRY")
    if facts.can_execute is not False:
        reasons.append("RELEASE_TRADING_EXECUTION_MUST_BE_DISABLED")

    return {
        "status": "HOLD" if reasons else "CLAIMS_CONSISTENT_NOT_AUTHORIZED",
        "reasons": reasons,
        "deploy": False,
        "can_execute": False,
    }
