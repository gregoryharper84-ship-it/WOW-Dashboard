"""Fail-closed, recoverable dispatch outbox for the WOW V17 resident controller.

Requires a durable atomic shared database store. No process-local lock or
GitHub issue comment is an acceptable production OutboxStore implementation.
A remote worker separately acquires its certified incident writer lease.
No code here authorizes PR mutation, merge, deployment, or wagering.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol
import re

_SHA = re.compile(r"[a-f0-9]{40}\Z")
_ACTIONS = frozenset({"DISPATCH_NEW_REPAIR", "REPAIR_SAME_BRANCH"})
_TERMINAL_OUTBOX_CODES = frozenset({
    "ATTEMPT_ACK_UNCORRELATED", "ATTEMPT_ACK_CORRELATED_UNVERIFIED",
    "ATTEMPT_AMBIGUOUS", "ATTEMPT_NOT_RECORDED",
})


@dataclass(frozen=True)
class Proposal:
    incident: int
    main_sha: str
    action: str
    pr_number: int | None = None
    pr_head_sha: str | None = None


@dataclass(frozen=True)
class Reservation:
    """Opaque receipt from the database atomic-reservation transaction."""
    incident: int
    intent_id: str
    main_sha: str
    action: str
    pr_number: int | None
    pr_head_sha: str | None
    epoch: int
    state: str = "RESERVED"


@dataclass(frozen=True)
class Outcome:
    code: str
    attempted: bool = False
    workflow_run_id: int | None = None


class OutboxStore(Protocol):
    def reserve(self, proposal: Proposal) -> tuple[str, Reservation | None]: ...
    def arm_once(self, reservation: Reservation) -> bool: ...
    def mark_result(self, reservation: Reservation, code: str,
                    workflow_run_id: int | None) -> bool: ...


class WorkflowSender(Protocol):
    def __call__(self, proposal: Proposal, reservation: Reservation) -> int | None: ...


def _valid_proposal(p: Proposal) -> bool:
    if type(p.incident) is not int or p.incident <= 0 or not isinstance(p.main_sha, str):
        return False
    if not _SHA.fullmatch(p.main_sha) or p.action not in _ACTIONS:
        return False
    if p.action == "REPAIR_SAME_BRANCH":
        return (type(p.pr_number) is int and p.pr_number > 0
                and isinstance(p.pr_head_sha, str) and bool(_SHA.fullmatch(p.pr_head_sha)))
    return p.pr_number is None and p.pr_head_sha is None


def _valid_reservation(r: Reservation, p: Proposal) -> bool:
    return (r.incident == p.incident and r.main_sha == p.main_sha
            and r.action == p.action and r.pr_number == p.pr_number
            and r.pr_head_sha == p.pr_head_sha and type(r.epoch) is int
            and r.epoch > 0 and isinstance(r.intent_id, str)
            and bool(re.fullmatch(r"[a-zA-Z0-9_-]{16,96}", r.intent_id))
            and r.state == "RESERVED")


def dispatch_once(
    proposal: Proposal, store: OutboxStore, sender: WorkflowSender,
    *, writer_fence: Callable[[Reservation], bool],
    current_main: Callable[[], str],
) -> Outcome:
    """Reserve -> verify current trusted main and fence -> arm ONCE -> dispatch.

    Failure after arm is ambiguous even if the sender raises before sending.
    An intent is never re-posted without separate verified run reconciliation.
    All store methods must implement their own database atomic CAS checks.
    """
    if not _valid_proposal(proposal):
        return Outcome("PROPOSAL_INVALID")
    try:
        state, reservation = store.reserve(proposal)
    except Exception:
        return Outcome("OUTBOX_STORE_UNAVAILABLE")
    if state == "IN_FLIGHT":
        return Outcome("PRIOR_DISPATCH_REQUIRES_RECONCILIATION")
    if state != "RESERVED" or not isinstance(reservation, Reservation):
        return Outcome("OUTBOX_RESERVATION_UNVERIFIED")
    if not _valid_reservation(reservation, proposal):
        return Outcome("OUTBOX_RESERVATION_CONTRADICTORY")
    try:
        if current_main() != proposal.main_sha:
            return Outcome("MAIN_CHANGED_BEFORE_DISPATCH")
        if writer_fence(reservation) is not True:
            return Outcome("WRITER_FENCE_UNVERIFIED")
        # Database CAS: a losing concurrent controller MUST NOT send a POST.
        if store.arm_once(reservation) is not True:
            return Outcome("OUTBOX_ATTEMPT_ALREADY_ARMED")
    except Exception:
        return Outcome("OUTBOX_FENCE_OR_ARM_UNKNOWN")
    try:
        # A 204/empty workflow_dispatch response does not identify a run.
        # Sender returns None until a run ID is independently correlated.
        run_id = sender(proposal, reservation)
        if run_id is None:
            code = "ATTEMPT_ACK_UNCORRELATED"
        elif type(run_id) is int and run_id > 0:
            code = "ATTEMPT_ACK_CORRELATED_UNVERIFIED"
        else:
            code = "ATTEMPT_AMBIGUOUS"
            run_id = None
    except Exception:
        code, run_id = "ATTEMPT_AMBIGUOUS", None
    if code not in _TERMINAL_OUTBOX_CODES:
        return Outcome("OUTBOX_INTERNAL_INVALID", attempted=True)
    try:
        recorded = store.mark_result(reservation, code, run_id)
    except Exception:
        recorded = False
    if recorded is not True:
        # Preserve hold after possibly accepted provider POST; never retry.
        return Outcome("OUTBOX_RESULT_NOT_DURABLE_RECONCILE", attempted=True)
    return Outcome(code, attempted=True, workflow_run_id=run_id)
