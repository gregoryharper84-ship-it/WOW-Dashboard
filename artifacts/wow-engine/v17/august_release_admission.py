"""Pure, non-privileged admission contract for an August-scale Render release.

This module cannot deploy, approve a release, or attest credentials.
All facts must be independently supplied by the single authorized release writer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

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
    checks: dict[str, tuple[str, str]]  # check name -> (SHA, conclusion)
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


def admission(facts: ReleaseFacts) -> dict[str, object]:
    """Return typed HOLD or an *inert* eligibility signal, never deployment authority."""
    reasons = []
    if not SHA.fullmatch(facts.target_sha) or facts.target_sha != facts.main_sha:
        reasons.append("RELEASE_EXACT_MAIN_SHA_INVALID_OR_STALE")
    if any(facts.checks.get(n) != (facts.target_sha, "success") for n in REQUIRED):
        reasons.append("RELEASE_AUGUST_CHECKS_NOT_VERIFIED")
    if not (facts.qa_approved and facts.release_approved and facts.approver_independent):
        reasons.append("RELEASE_INDEPENDENT_AUTHORIZATION_MISSING")
    if not (facts.credential_scope_attested and facts.service_id_attested):
        reasons.append("RELEASE_PROVIDER_IDENTITY_UNATTESTED")
    if facts.writer_count != 1 or not facts.release_lease_owned:
        reasons.append("RELEASE_SINGLE_WRITER_UNVERIFIED")
    if not facts.intent_reserved:
        reasons.append("RELEASE_DURABLE_INTENT_MISSING")
    if facts.provider_ack_uncertain:
        reasons.append("RELEASE_PROVIDER_ACK_UNCERTAIN_NO_RETRY")
    if facts.can_execute:
        reasons.append("RELEASE_TRADING_EXECUTION_MUST_BE_DISABLED")
    return {
        "status": "HOLD" if reasons else "ELIGIBLE_FOR_INDEPENDENT_RELEASE_HANDOFF",
        "reasons": reasons,
        "deploy": False,
        "can_execute": False,
    }
