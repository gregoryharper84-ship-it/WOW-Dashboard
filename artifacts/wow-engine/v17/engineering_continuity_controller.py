"""WOW V17 continuity controller: fenced, durable, bounded engineering dispatch.

This module does not authorize GitHub writes, PR merging, model publication or
production deployment. The port must be an independently authorized adapter
that verifies its own App identity, database lease epochs and provenance.
It is safe to import in an unconfigured production process: nothing starts.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol
import re

SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Snapshot:
    incident: int
    main_sha: str
    incident_open: bool
    pr_state: str = "NONE"             # NONE, DRAFT, READY, MERGED
    pr_number: int | None = None
    pr_head_sha: str | None = None
    checks: str = "PENDING"             # PENDING, FAILED, PASS
    qa: str = "PENDING"                 # PENDING, FAILED, PASS
    pr_app_authored: bool = False
    protected_change: bool = False
    verified_writer_available: bool = False
    lease_service_ready: bool = False
    worker_busy: bool = False
    pending_dispatch: bool = False
    attempts: int = 0
    deployed_sha: str | None = None
    action_receipt_verified: bool = False


@dataclass(frozen=True)
class Decision:
    code: str
    mutation: bool = False
    terminal: bool = False


@dataclass(frozen=True)
class Lease:
    incident: int
    owner: str
    epoch: int
    fenced_token: str


class ContinuityPort(Protocol):
    """Privileged host ports. Dispatch lease is NOT the remote writer lease.

    The remote worker must acquire/renew/check its own atomic DB writer lease
    for EVERY branch mutation. Claiming dispatch admission grants no write rights.
    The port must implement unique durable intents and reconcile ambiguous ACKs.
    """

    def load(self, incident: int) -> Snapshot: ...
    def checkpoint(self, incident: int, code: str) -> None: ...
    def claim_dispatch_admission(self, incident: int, main_sha: str) -> Lease: ...
    def check_fence(self, lease: Lease) -> bool: ...
    def dispatch(self, incident: int, action: str, main_sha: str,
                 lease: Lease, pr_number: int | None) -> str: ...
    def release_dispatch_admission(self, lease: Lease) -> None: ...


def _sha(value: str | None) -> bool:
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def decide(s: Snapshot) -> Decision:
    """Fail-closed policy; answers describe intent, not authorizations."""
    if not isinstance(s.incident, int) or isinstance(s.incident, bool) or s.incident <= 0:
        return Decision("BAD_INCIDENT_ID")
    if not _sha(s.main_sha):
        return Decision("MAIN_IDENTITY_INVALID")
    if s.pr_state not in {"NONE", "DRAFT", "READY", "MERGED"}:
        return Decision("PR_STATE_UNKNOWN")
    if s.checks not in {"PENDING", "FAILED", "PASS"} or s.qa not in {"PENDING", "FAILED", "PASS"}:
        return Decision("REVIEW_STATE_UNKNOWN")
    if not isinstance(s.attempts, int) or isinstance(s.attempts, bool) or s.attempts < 0:
        return Decision("ATTEMPT_COUNTER_INVALID")
    if s.pr_state != "NONE" and (not isinstance(s.pr_number, int) or s.pr_number <= 0 or not _sha(s.pr_head_sha)):
        return Decision("PR_IDENTITY_INVALID")
    if s.pr_state == "NONE" and (s.pr_number is not None or s.pr_head_sha is not None):
        return Decision("PR_IDENTITY_CONTRADICTION")
    if s.deployed_sha is not None and not _sha(s.deployed_sha):
        return Decision("DEPLOY_IDENTITY_INVALID")
    if s.action_receipt_verified and (s.pr_state != "MERGED" or s.deployed_sha != s.main_sha):
        return Decision("ACTION_RECEIPT_CONTEXT_CONTRADICTION")
    if not s.incident_open:
        return Decision("ISSUE_ALREADY_CLOSED", terminal=True)
    if s.pr_state == "MERGED":
        if s.deployed_sha != s.main_sha:
            return Decision("WAIT_INDEPENDENT_RELEASE")
        if s.action_receipt_verified:
            # Does not close the issue; requires independent closure reducer.
            return Decision("READY_FOR_INDEPENDENT_CLOSURE")
        return Decision("VERIFY_ACTUAL_ACTION_READ_ONLY")
    if s.pending_dispatch:
        return Decision("DISPATCH_PENDING_RECONCILIATION")
    if s.worker_busy:
        return Decision("ANOTHER_WORKER_ACTIVE")
    if s.pr_state in {"DRAFT", "READY"}:
        if s.checks == "PASS" and s.qa == "PASS":
            return Decision("WAIT_INDEPENDENT_RELEASE_REVIEW")
        if s.checks != "FAILED" and s.qa != "FAILED":
            return Decision("WAIT_EXISTING_PR_CHECKS")
        if s.protected_change:
            return Decision("TRUST_ROOT_REPAIR_REQUIRES_APP_REPROPOSAL")
        if not s.pr_app_authored:
            return Decision("PR_WRITER_PROVENANCE_UNVERIFIED")
        action = "REPAIR_SAME_BRANCH"
    else:
        if s.protected_change:
            return Decision("TRUST_ROOT_REQUIRES_INDEPENDENT_PROPOSAL")
        action = "DISPATCH_NEW_REPAIR"
    if s.attempts >= MAX_ATTEMPTS:
        return Decision("REPAIR_ATTEMPT_CAP_REQUIRES_TRIAGE")
    if not s.verified_writer_available:
        return Decision("ENGINEERING_APP_WRITER_UNVERIFIED")
    if not s.lease_service_ready:
        return Decision("ATOMIC_FENCED_LEASE_UNAVAILABLE")
    return Decision(action, mutation=True)


def advance(port: ContinuityPort, incident: int) -> Decision:
    """One bounded cycle. No recursive retries; ambiguous dispatch is a HOLD.

    Durable intent precedes lease acquisition and dispatch; the external port
    must make the checkpoint idempotent and reject unverified writer identity.
    Any checkpoint/storage/lease failure aborts without attempting dispatch.
    """
    snapshot = port.load(incident)
    if snapshot.incident != incident:
        return Decision("INCIDENT_ID_MISMATCH")
    result = decide(snapshot)
    if not result.mutation:
        port.checkpoint(incident, result.code)
        return result
    # Only an independently authenticated host can persist this durable intent.
    # This checkpoint is a durable, unique pending-dispatch row. load() must
    # reflect pending_dispatch=True for another invocation until reconciliation.
    port.checkpoint(incident, "INTENT_" + result.code)
    try:
        lease = port.claim_dispatch_admission(incident, snapshot.main_sha)
    except Exception:
        # No dispatch and no guessed lease. The host should log a typed failure.
        return Decision("LEASE_CLAIM_FAILED")
    try:
        if (lease.incident != incident or lease.epoch <= 0
                or not lease.owner or not lease.fenced_token):
            return Decision("LEASE_RECEIPT_INVALID")
        if not port.check_fence(lease):
            return Decision("LEASE_LOST_BEFORE_DISPATCH")
        current = port.load(incident)
        if current.incident != incident or current.main_sha != snapshot.main_sha:
            return Decision("MAIN_CHANGED_BEFORE_DISPATCH")
        if current.pr_state != snapshot.pr_state or current.pr_head_sha != snapshot.pr_head_sha:
            return Decision("PR_CHANGED_BEFORE_DISPATCH")
        if not current.pending_dispatch:
            return Decision("PENDING_INTENT_NOT_DURABLE")
        if decide(replace(current, pending_dispatch=False)).code != result.code:
            return Decision("ADMISSION_CHANGED_BEFORE_DISPATCH")
        if not port.check_fence(lease):
            return Decision("LEASE_LOST_AT_DISPATCH_BOUNDARY")
        # The remote worker MUST independently claim its own DB writer lease
        # before PR/branch mutation; dispatch admission is not write authority.
        try:
            receipt = port.dispatch(incident, result.code, snapshot.main_sha, lease, snapshot.pr_number)
        except Exception:
            # May have been accepted upstream. NEVER redispatch automatically.
            port.checkpoint(incident, "DISPATCH_ACK_AMBIGUOUS_RECONCILE")
            return Decision("DISPATCH_ACK_AMBIGUOUS_RECONCILE")
        if not isinstance(receipt, str) or not receipt.strip():
            port.checkpoint(incident, "DISPATCH_RECEIPT_MISSING_RECONCILE")
            return Decision("DISPATCH_RECEIPT_MISSING_RECONCILE")
        port.checkpoint(incident, "DISPATCH_SUBMITTED_UNVERIFIED")
        return Decision("DISPATCH_SUBMITTED_UNVERIFIED")
    finally:
        # A failed release is not consent to start another writer: the lease
        # expires under DB TTL and subsequent cycles must observe fencing.
        try:
            port.release_dispatch_admission(lease)
        except Exception:
            pass


__all__ = ["Snapshot", "Decision", "Lease", "ContinuityPort", "decide", "advance"]
