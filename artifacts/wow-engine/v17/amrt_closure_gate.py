"""AMRT closure and retry decisions for the existing WOW Ecosystem Conductor.

Pure decision logic only. No network, write, dispatch, merge, deploy or
sporting-probability authority. The authorized Engineering worker must integrate
it under existing incident leases and governance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

TEAM = (
    "BLOCKER_SENTINEL",
    "ROOT_CAUSE_INVESTIGATOR",
    "MODEL_RECOVERY_ENGINEER",
    "DATA_INTEGRATION_ENGINEER",
    "CODE_CI_ENGINEER",
    "INDEPENDENT_QA",
    "RELEASE_VERIFICATION",
)
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
MAX_PROVIDER_ATTEMPTS = 3


@dataclass(frozen=True)
class Decision:
    status: str
    blockers: tuple[str, ...]
    next_owner: str


def specialist_for(failure: str) -> str:
    code = str(failure).upper()
    if any(part in code for part in ("EVENT_ID", "IDENTITY", "SOURCE", "FEED", "HYDRATION", "PERSISTENCE")):
        return "DATA_INTEGRATION_ENGINEER"
    if any(part in code for part in ("CI_", "TEST_", "BUILD_", "PATCH_")):
        return "CODE_CI_ENGINEER"
    if any(part in code for part in ("DEPLOY", "SHA_", "ACTION_", "RELEASE")):
        return "RELEASE_VERIFICATION"
    if any(part in code for part in ("SCORER", "MODEL_", "CALIBRATION", "STAGE_PROOF", "PUBLICATION")):
        return "MODEL_RECOVERY_ENGINEER"
    return "ROOT_CAUSE_INVESTIGATOR"


def lease_reentry(case: Mapping[str, Any], *, now: datetime) -> Decision:
    """Lease expiry is a stall for *existing* ownership, never authority to steal it."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
    if not case.get("incident_id") or not case.get("owner_id"):
        return Decision("BLOCKED", ("INCIDENT_OR_OWNER_MISSING",), "ECOSYSTEM_CONDUCTOR")
    epoch = case.get("lease_epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        return Decision("BLOCKED", ("LEASE_EPOCH_INVALID",), "ECOSYSTEM_CONDUCTOR")
    value = case.get("lease_expires_at")
    if not isinstance(value, str) or not value:
        return Decision("BLOCKED", ("LEASE_EXPIRY_MISSING",), "ECOSYSTEM_CONDUCTOR")
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return Decision("BLOCKED", ("LEASE_EXPIRY_MALFORMED",), "ECOSYSTEM_CONDUCTOR")
    if expiry.tzinfo is None or expiry.utcoffset() is None:
        return Decision("BLOCKED", ("LEASE_EXPIRY_NOT_OFFSET_AWARE",), "ECOSYSTEM_CONDUCTOR")
    if expiry <= now:
        return Decision("REENTRY_REQUIRED", ("LEASE_EXPIRED_ATOMIC_RECLAIM_REQUIRED",), "ECOSYSTEM_CONDUCTOR")
    return Decision("OWNED", (), str(case["owner_id"]))


def provider_attempt(attempts: int, repeated_failure: bool, fallback_available: bool) -> Decision:
    """Recommend a governed provider choice; never bypass the shared writer lease."""
    if attempts < 0:
        return Decision("BLOCKED", ("INVALID_ATTEMPT_COUNT",), "ECOSYSTEM_CONDUCTOR")
    if attempts >= MAX_PROVIDER_ATTEMPTS:
        return Decision("BLOCKED", ("ATTEMPT_CAP_REQUIRES_TRIAGE",), "CODE_CI_ENGINEER")
    if repeated_failure and fallback_available:
        return Decision("FALLBACK_CANDIDATE", (), "CODE_CI_ENGINEER")
    if repeated_failure:
        return Decision("BLOCKED", ("PROVIDER_STAGNANT_NO_AUTHORIZED_FALLBACK",), "CODE_CI_ENGINEER")
    return Decision("RETRY_CANDIDATE", (), "CODE_CI_ENGINEER")


def closure_gate(receipt: Mapping[str, Any]) -> Decision:
    """Proof-conjunction for FIXED_AND_VERIFIED. Unknown or absent evidence fails closed."""
    blockers: list[str] = []
    if receipt.get("can_execute") is not False or receipt.get("terminal_authority") != TERMINAL_AUTHORITY:
        blockers.append("V17_GOVERNANCE_INVALID")
    if not receipt.get("incident_id") or not receipt.get("implementation_actor"):
        blockers.append("INCIDENT_OR_IMPLEMENTER_MISSING")
    if receipt.get("change_class") not in ("A", "B", "C"):
        blockers.append("CHANGE_CLASS_UNKNOWN")
    if not receipt.get("root_cause_evidence"):
        blockers.append("ROOT_CAUSE_UNPROVEN")

    for key in ("focused_tests_pass", "regression_tests_pass", "ci_green",
                "security_authorized", "independent_review_approved",
                "independent_qa_approved", "release_authorized",
                "production_db_identity_verified", "action_acceptance_pass",
                "persistence_readback_pass", "reconciliation_pass",
                "terminal_states_concordant"):
        if receipt.get(key) is not True:
            blockers.append(key.upper() + "_MISSING_OR_FAILED")

    head = receipt.get("reviewed_head_sha")
    ci = receipt.get("ci_sha")
    review = receipt.get("review_sha")
    merge = receipt.get("authorized_merge_sha")
    deployed = receipt.get("deployed_sha")
    action = receipt.get("action_sha")
    if not all(isinstance(s, str) and len(s) == 40 and all(c in "0123456789abcdef" for c in s.lower())
               for s in (head, ci, review, merge, deployed, action)):
        blockers.append("COMPLETE_EXACT_SHA_CHAIN_MISSING")
    elif ci != head or review != head or deployed != merge or action != merge:
        blockers.append("EXACT_SHA_MISMATCH")

    implementer = receipt.get("implementation_actor")
    qa = receipt.get("qa_actor")
    reviewer = receipt.get("review_actor")
    if not qa or not reviewer or len({str(implementer), str(qa), str(reviewer)}) != 3:
        blockers.append("INDEPENDENT_ACTOR_SEPARATION_UNPROVEN")

    run_ids = [receipt.get(key) for key in ("action_run_id", "persistence_run_id", "reconciliation_run_id")]
    if not all(isinstance(value, str) and bool(value) for value in run_ids) or len(set(run_ids)) != 1:
        blockers.append("PRODUCTION_RUN_RECONCILIATION_UNPROVEN")

    if receipt.get("change_class") == "C" and (
        receipt.get("model_owner_certified") is not True
        or not receipt.get("class_c_governance_approved") is True
    ):
        blockers.append("CLASS_C_CERTIFICATION_MISSING")

    if blockers:
        return Decision("BLOCKED_WITH_EXACT_REASON", tuple(blockers),
                        "RELEASE_VERIFICATION" if "ROOT_CAUSE_UNPROVEN" not in blockers
                        else "ROOT_CAUSE_INVESTIGATOR")
    return Decision("FIXED_AND_VERIFIED", (), "INDEPENDENT_QA")
