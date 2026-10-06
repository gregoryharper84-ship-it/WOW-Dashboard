"""Deterministic rollback authorization for production acceptance failures.

This module decides whether rollback is authorized. It does not call Render.
Execution must use the governed deployment/rollback mechanism only after this
decision is ROLLBACK_AUTHORIZED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Decision = Literal[
    "NO_ROLLBACK",
    "RETRY_REQUIRED",
    "ROLLBACK_AUTHORIZED",
    "BLOCKED_WITH_EXACT_REASON",
]

QUALIFYING_FAILURES = {
    "APPLICATION_HTTP_5XX",
    "RESPONSE_SCHEMA_MISMATCH",
    "SAFETY_HEADER_MISSING",
}


@dataclass(frozen=True)
class RollbackEvidence:
    change_class: str
    failure_class: str
    current_deploy_sha: str
    previous_sha: str
    deployment_attributed: bool
    confirmations: int
    previous_production_accepted: bool
    schema_compatible: bool
    persistence_compatible: bool
    can_execute: bool = False


def evaluate_rollback(evidence: RollbackEvidence) -> tuple[Decision, str]:
    if evidence.can_execute is not False:
        return "BLOCKED_WITH_EXACT_REASON", "ROLLBACK_EVIDENCE_CAN_EXECUTE_MUST_BE_FALSE"
    if evidence.failure_class not in QUALIFYING_FAILURES:
        return "NO_ROLLBACK", "FAILURE_NOT_ROLLBACK_QUALIFYING"
    if evidence.change_class.upper() == "C":
        return "BLOCKED_WITH_EXACT_REASON", "CLASS_C_ROLLBACK_REQUIRES_GOVERNED_REVIEW"
    if not evidence.deployment_attributed:
        return "RETRY_REQUIRED", "FAILURE_NOT_YET_ATTRIBUTED_TO_DEPLOYMENT"
    if evidence.failure_class == "APPLICATION_HTTP_5XX" and evidence.confirmations < 2:
        return "RETRY_REQUIRED", "HTTP_5XX_REQUIRES_BOUNDED_CONFIRMATION"
    if evidence.failure_class in {"RESPONSE_SCHEMA_MISMATCH", "SAFETY_HEADER_MISSING"} and evidence.confirmations < 1:
        return "RETRY_REQUIRED", "DETERMINISTIC_FAILURE_CONFIRMATION_REQUIRED"
    if not evidence.previous_production_accepted:
        return "BLOCKED_WITH_EXACT_REASON", "PREVIOUS_SHA_NOT_PROVEN_PRODUCTION_ACCEPTED"
    if not evidence.schema_compatible:
        return "BLOCKED_WITH_EXACT_REASON", "ROLLBACK_SCHEMA_COMPATIBILITY_UNPROVEN"
    if not evidence.persistence_compatible:
        return "BLOCKED_WITH_EXACT_REASON", "ROLLBACK_PERSISTENCE_COMPATIBILITY_UNPROVEN"
    if evidence.current_deploy_sha == evidence.previous_sha:
        return "NO_ROLLBACK", "CURRENT_AND_PREVIOUS_SHA_IDENTICAL"
    return "ROLLBACK_AUTHORIZED", "EVIDENCE_GATED_ROLLBACK_AUTHORIZED"


__all__ = ["QUALIFYING_FAILURES", "RollbackEvidence", "evaluate_rollback"]
