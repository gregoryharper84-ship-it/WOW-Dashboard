"""Authenticated-worker boundary for the five existing AMRT specialist adapters.

This is a callable seam for the SINGLE existing protected engineering worker,
not another resident agent, scheduler, lease owner, or authorization source.
The protected worker MUST read and authenticate its current lease epoch/worker
identity independently immediately before invoking this function. No local
flag or fixture in this module proves actual workflow admission.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping

from v17.amrt_operational_lanes import evaluate_amrt_specialist
from v17.amrt_receipt_bridge import record_handoff
from v17.amrt_specialist_agents import AGENT_TO_EXISTING_ROLE
from v17.persistent_worker_safety import Lease, lease_allows_mutation

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_WORKER = re.compile(r"^github-actions:[1-9][0-9]*:[1-9][0-9]*(?::anthropic)?$")


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("AMRT_LEASE_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("AMRT_LEASE_TIMESTAMP_INVALID")
    return parsed.astimezone(timezone.utc)


def persist_authenticated_specialist_handoff(
    *,
    incident: Mapping[str, Any],
    specialist: str,
    lease: Lease,
    # Read by the protected worker from the authoritative, current lease
    # record; never copied from candidate PR source or an agent response.
    verified_latest_epoch: int,
    verified_worker_id: str,
    # From a separate authenticated read of the canonical GitHub issue.
    # It is NOT the temporary GitHub Actions writer principal.
    verified_accountable_owner_id: str,
    verified_owner_evidence_ref: str,
    observed_at: datetime,
    head_sha: str,
    supabase_url: str,
    service_credential: str,
    producer_stages: Mapping[str, Mapping[str, Any]] | None = None,
    replay_results: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate ONE specialist and write/read back its existing ledger receipt.

    No job dispatch, source mutation, permissions, independent review, terminal
    approval, or sporting probability is performed. A retry appends a distinct
    receipt; the caller must deduplicate by its existing canonical run/lease
    lifecycle, never treat this method as an exactly-once transaction.
    """
    if not isinstance(incident, Mapping) or incident.get("amrt_owned") is not True:
        raise ValueError("AMRT_INCIDENT_NOT_ADMITTED")
    if incident.get("can_execute") is not False:
        raise ValueError("AMRT_EXECUTION_INVARIANT_INVALID")
    if specialist not in AGENT_TO_EXISTING_ROLE:
        raise ValueError("AMRT_SPECIALIST_UNKNOWN")
    if not isinstance(lease, Lease):
        raise ValueError("AMRT_LEASE_MISSING")
    issue = str(incident.get("incident_id") or "").strip()
    if not issue or not issue.isdigit() or int(issue) < 1:
        raise ValueError("AMRT_CANONICAL_INCIDENT_INVALID")
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise ValueError("AMRT_OBSERVATION_TIME_UNTRUSTED")
    if not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
        raise ValueError("AMRT_EXACT_HEAD_SHA_MISSING")
    if not isinstance(verified_latest_epoch, int) or isinstance(verified_latest_epoch, bool):
        raise ValueError("AMRT_VERIFIED_LEASE_EPOCH_MISSING")
    if not isinstance(verified_worker_id, str) or not _WORKER.fullmatch(verified_worker_id):
        raise ValueError("AMRT_VERIFIED_WORKER_ID_INVALID")
    # GitHub issue accountability and the ephemeral mutation lease are two
    # distinct, independently checked identities. Do NOT rewrite owner_id to
    # the worker ID merely to pass a single-writer fence.
    issue_ref = "https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/" + issue
    if (not isinstance(verified_accountable_owner_id, str)
            or not verified_accountable_owner_id.strip()
            or verified_accountable_owner_id != incident.get("owner_id")
            or verified_owner_evidence_ref != issue_ref
            or incident.get("canonical_owner_source_ref") != issue_ref):
        raise ValueError("AMRT_ACCOUNTABLE_ISSUE_OWNER_UNVERIFIED")
    if (lease.work_item_id != issue
            or lease.worker_id != verified_worker_id
            or incident.get("lease_owner") != verified_worker_id
            or incident.get("lease_authority") != "EXISTING_AUTHENTICATED_ENGINEERING_WORKER"):
        raise ValueError("AMRT_CANONICAL_OWNER_OR_WRITER_MISMATCH")

    observed = observed_at.astimezone(timezone.utc)
    expiry = _utc(lease.expires_at)
    if observed >= expiry or lease.lease_epoch != verified_latest_epoch:
        raise ValueError("AMRT_STALE_OR_EXPIRED_LEASE")
    normalized = Lease(lease.work_item_id, lease.worker_id, lease.lease_epoch, expiry.isoformat())
    if not lease_allows_mutation(
        normalized, expected_work_item_id=issue,
        latest_epoch=verified_latest_epoch, current_worker_id=verified_worker_id,
        now=observed.isoformat(),
    ):
        raise ValueError("AMRT_WRITER_LEASE_UNVERIFIED")

    evaluated = evaluate_amrt_specialist(
        incident, specialist=specialist, producer_stages=producer_stages,
        replay_results=replay_results, now=observed,
    )
    if (evaluated.incident_id != issue
            or evaluated.can_execute is not False
            or evaluated.terminal_authority != TERMINAL_AUTHORITY
            or evaluated.role != AGENT_TO_EXISTING_ROLE[specialist]
            or evaluated.state == "FIXED_AND_VERIFIED"
            or "specialist:" + specialist not in evaluated.evidence_refs):
        raise ValueError("AMRT_SPECIALIST_RECEIPT_INVALID")

    # Existing canonical receipt writer handles validation, durable append,
    # authenticated readback and rejects a worker's self-approval/closure.
    # No new table or credential is created; do not log these arguments.
    return record_handoff(
        supabase_url, service_credential, evaluated,
        worker_run_id=verified_worker_id,
        priority=str(incident.get("severity") or "P0"),
        change_class=str(incident.get("change_class") or ""),
        head_sha=head_sha,
        lease_id=verified_worker_id,
        lease_epoch=lease.lease_epoch,
    )


__all__ = ["CAN_EXECUTE", "TERMINAL_AUTHORITY", "persist_authenticated_specialist_handoff"]
