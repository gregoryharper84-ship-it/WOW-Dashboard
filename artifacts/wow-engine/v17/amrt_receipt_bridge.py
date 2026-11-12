"""Persist AMRT role decisions to the existing append-only engineering ledger.

Only the authenticated existing worker may invoke record_handoff with its
service credential. No new table, permission, release attestation, or actor.
The recorder is forbidden to synthesize independent QA or successful closure.
"""
from __future__ import annotations

from typing import Any

from v17.amrt_operational_lanes import RoleReceipt
from v17.engineering_receipts import build_receipt, record_receipt

REENTRY = {
    "BLOCKER_SENTINEL": "TRUSTED_SOURCE_EVIDENCE_AVAILABLE",
    "ROOT_CAUSE_INVESTIGATOR": "REPRODUCTION_AND_ROOT_CAUSE_AVAILABLE",
    "MODEL_RECOVERY_ENGINEER": "MODEL_OWNER_EVIDENCE_AVAILABLE",
    "DATA_INTEGRATION_ENGINEER": "SOURCE_RECONCILIATION_AVAILABLE",
    "CODE_CI_ENGINEER": "AUTHORIZED_WRITER_OR_PR_REVIEW_AVAILABLE",
    "INDEPENDENT_QA": "INDEPENDENT_EXACT_SHA_QA_RECEIPT_AVAILABLE",
    "RELEASE_VERIFICATION": "AUTHORIZED_LIVE_RELEASE_EVIDENCE_AVAILABLE",
}


def handoff_to_existing_receipt(
    handoff: RoleReceipt, *, worker_run_id: str,
    priority: str, change_class: str,
    head_sha: str | None = None,
    lease_id: str | None = None,
    lease_epoch: int | None = None,
) -> dict[str, Any]:
    """Build truth-preserving evidence, never an independent approval."""
    if handoff.can_execute is not False or handoff.terminal_authority != "V17_TERMINAL_REDUCER":
        raise ValueError("AMRT_GOVERNANCE_INVALID")
    if handoff.role not in REENTRY:
        raise ValueError("AMRT_UNKNOWN_ROLE")
    if handoff.state == "FIXED_AND_VERIFIED":
        # Closure must be authored by the separate independent verifier, not
        # encoded by this resident Engineering worker.
        raise ValueError("AMRT_SELF_CLOSURE_FORBIDDEN")
    if handoff.state not in ("READY", "ROUTED", "HELD", "PR_REVIEW", "REENTRY_REQUIRED"):
        raise ValueError("AMRT_STATE_INVALID")
    if not handoff.incident_id.strip():
        raise ValueError("AMRT_INCIDENT_ID_MISSING")
    blocked = handoff.state in ("HELD", "REENTRY_REQUIRED")
    reason = handoff.reasons[0] if handoff.reasons else "AMRT_EXTERNAL_EVIDENCE_PENDING"
    # A routing decision is only IN_PROGRESS; it is NEVER equivalent to an
    # implemented fix or external QA/release authority.
    record = {
        "incident_id": handoff.incident_id,
        "provider": "automation",
        "role": "conductor",
        "worker_run_id": worker_run_id,
        "priority": priority,
        "change_class": change_class,
        "disposition": "BLOCKED_WITH_EXACT_REASON" if blocked else "IN_PROGRESS",
        "retry_count": 0,
        "tests": [{
            "amrt_role": handoff.role,
            "amrt_state": handoff.state,
            "next_owner": handoff.next_owner,
            "reason_codes": list(handoff.reasons),
            "evidence_refs": list(handoff.evidence_refs),
            "can_execute": False,
        }],
    }
    if blocked:
        record.update({
            "typed_blocker": reason,
            "blocker_owner": handoff.next_owner,
            "revisit_trigger": REENTRY[handoff.role],
        })
    if head_sha is not None:
        record["head_sha"] = head_sha
    if lease_id is not None:
        record["lease_id"] = lease_id
    if lease_epoch is not None:
        record["lease_epoch"] = lease_epoch
    return build_receipt(**record)


def record_handoff(url: str, credential: str, handoff: RoleReceipt, **kwargs: Any) -> dict[str, Any]:
    """Append with existing service-role credential and verify exact readback."""
    row = handoff_to_existing_receipt(handoff, **kwargs)
    return record_receipt(url, credential, row)
