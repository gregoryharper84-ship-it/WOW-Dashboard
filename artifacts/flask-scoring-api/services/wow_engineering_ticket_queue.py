"""TTL-aware queue orchestration for WOW V17 engineering tickets.

This module is engineering control-plane infrastructure only. It does not
originate, alter, publish, or execute sporting probabilities or wagers.

Wake-ups are pull based: the orchestrator claims ACTIONABLE tickets and PARKED
tickets whose TTL has expired through one atomic database RPC. A woken ticket
must re-evaluate its blocker before any engineering execution resumes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping, TypedDict


CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
PROBABILITY_AUTHORITY = "NONE"
QUEUE_TABLE = "wow_engineering_backlog"
CLAIM_RPC = "wow_claim_engineering_tickets"
MAX_WAKES = 6
DEFAULT_TTL = timedelta(hours=1)

TTL_CONFIG: dict[str, timedelta] = {
    "api_rate_limit": timedelta(minutes=15),
    "upstream_dependency": timedelta(hours=1),
    "awaiting_pr_review": timedelta(hours=4),
}

QueueStatus = Literal["ACTIONABLE", "IN_PROGRESS", "PARKED", "COMPLETED", "FAILED"]
BlockerDisposition = Literal["BLOCKED", "CLEARED", "FAILED"]


class EngineeringTicket(TypedDict, total=False):
    id: int
    ticket_id: str
    title: str
    class_: str
    status: str
    terminal_state: str | None
    source: str
    priority: str | None
    scope: str | None
    description: str
    evidence_notes: str | None
    queue_status: QueueStatus
    previous_queue_status: QueueStatus
    previous_parked_reason: str | None
    previous_parked_until: str | None
    parked_reason: str | None
    parked_context: dict[str, Any]
    parked_until: str | None
    wake_count: int
    claim_owner: str | None
    claimed_at: str | None


@dataclass(frozen=True)
class BlockerCheck:
    disposition: BlockerDisposition
    detail: str = ""


@dataclass(frozen=True)
class PollBatch:
    """One polling-cycle receipt.

    `ready` rows remain atomically claimed by this orchestrator and are safe for
    the caller to hand to the engineering graph. `reparked` and `failed` rows
    have already been committed back to the database and must not execute.
    """

    ready: tuple[dict[str, Any], ...]
    reparked: tuple[dict[str, Any], ...]
    failed: tuple[dict[str, Any], ...]


class TicketClaimLost(RuntimeError):
    """The ticket is no longer owned by the expected orchestrator claim."""


BlockerEvaluator = Callable[[Mapping[str, Any]], BlockerCheck]
PrStatusFetcher = Callable[[str], str]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parked_until_for(
    reason: str,
    current_wake_count: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """Return the next UTC wake time using bounded linear/exponential-style backoff.

    The multiplier follows the ticket contract: wake_count + 1, capped at six.
    Thus an awaiting-review ticket sleeps 4h, 8h, 12h, 16h, 20h, then 24h.
    """

    if current_wake_count < 0:
        raise ValueError("wake_count may not be negative")
    base_ttl = TTL_CONFIG.get(str(reason), DEFAULT_TTL)
    multiplier = min(current_wake_count + 1, MAX_WAKES)
    return (now or utcnow()) + (base_ttl * multiplier)


def ticket_to_issue(ticket: Mapping[str, Any]) -> dict[str, Any]:
    """Map a claimed backlog row onto the public LangGraph issue boundary.

    Queue-control fields stay outside graph state. Production promotion is never
    inferred from a queue claim.
    """

    ticket_id = str(ticket.get("ticket_id") or "").strip()
    if not ticket_id:
        raise ValueError("ticket_id is required")
    severity = str(ticket.get("priority") or "P3").upper()
    if severity not in {"P0", "P1", "P2", "P3"}:
        severity = "P3"
    change_class = str(ticket.get("class") or ticket.get("class_") or "A").upper()
    if change_class not in {"A", "B", "C"}:
        raise ValueError(f"invalid engineering change class: {change_class!r}")
    component = str(ticket.get("scope") or ticket.get("title") or ticket_id).strip()
    return {
        "issue_id": ticket_id,
        "severity": severity,
        "change_class": change_class,
        "component": component,
        "sport": None,
        "promotion_authorized": False,
    }


class SupabaseEngineeringTicketRepository:
    """Service-role persistence adapter for the engineering execution queue."""

    def __init__(self, client: Any, *, claim_owner: str):
        owner = str(claim_owner).strip()
        if not owner:
            raise ValueError("claim_owner is required")
        self.client = client
        self.claim_owner = owner

    def claim_due(self, *, limit: int = 5) -> list[dict[str, Any]]:
        bounded_limit = min(max(int(limit), 1), 25)
        response = self.client.rpc(
            CLAIM_RPC,
            {"p_claim_owner": self.claim_owner, "p_limit": bounded_limit},
        ).execute()
        return [dict(row) for row in (response.data or [])]

    def _update_claimed(
        self,
        ticket: Mapping[str, Any],
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        ticket_id = str(ticket.get("ticket_id") or "").strip()
        if not ticket_id:
            raise ValueError("ticket_id is required")
        response = (
            self.client.table(QUEUE_TABLE)
            .update(dict(fields))
            .eq("ticket_id", ticket_id)
            .eq("queue_status", "IN_PROGRESS")
            .eq("claim_owner", self.claim_owner)
            .execute()
        )
        rows = response.data or []
        if len(rows) != 1:
            raise TicketClaimLost(f"engineering ticket claim lost: {ticket_id}")
        return dict(rows[0])

    def park_claimed(
        self,
        ticket: Mapping[str, Any],
        *,
        reason: str,
        context: Mapping[str, Any] | None = None,
        detail: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        reason = str(reason).strip()
        if not reason:
            raise ValueError("parked reason is required")
        current_wakes = int(ticket.get("wake_count") or 0)
        wake_at = parked_until_for(reason, current_wakes, now=now)
        parked_context = dict(context or ticket.get("parked_context") or {})
        if detail:
            parked_context["last_blocker_check_detail"] = detail
        return self._update_claimed(
            ticket,
            {
                "status": "BLOCKED",
                "queue_status": "PARKED",
                "parked_reason": reason,
                "parked_context": parked_context,
                "parked_until": iso(wake_at),
                "wake_count": current_wakes + 1,
                "claim_owner": None,
                "claimed_at": None,
            },
        )

    def clear_claimed_blocker(self, ticket: Mapping[str, Any]) -> dict[str, Any]:
        """Clear parking metadata while retaining the atomic IN_PROGRESS claim.

        Releasing the row to ACTIONABLE and then immediately executing would
        create a claim race. The caller already owns the row, so it remains
        IN_PROGRESS and is returned in PollBatch.ready for execution.
        """

        return self._update_claimed(
            ticket,
            {
                "status": "IN_PROGRESS",
                "queue_status": "IN_PROGRESS",
                "parked_reason": None,
                "parked_context": {},
                "parked_until": None,
                "wake_count": 0,
            },
        )

    def fail_claimed(
        self,
        ticket: Mapping[str, Any],
        *,
        detail: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        at = now or utcnow()
        existing_notes = str(ticket.get("evidence_notes") or "").rstrip()
        note = f"[engineering-ticket-queue escalation] {detail}"
        evidence_notes = f"{existing_notes}\n{note}" if existing_notes else note
        context = dict(ticket.get("parked_context") or {})
        context.update(
            {
                "queue_failure_detail": detail,
                "escalation_required": True,
                "failed_at": iso(at),
            }
        )
        return self._update_claimed(
            ticket,
            {
                "status": "CLOSED",
                "queue_status": "FAILED",
                "terminal_state": "BLOCKED_WITH_EXACT_REASON",
                "parked_reason": None,
                "parked_context": context,
                "parked_until": None,
                "claim_owner": None,
                "claimed_at": None,
                "closed_at": iso(at),
                "evidence_notes": evidence_notes,
            },
        )


class EngineeringTicketOrchestrator:
    """State-aware polling/wake-up mechanism for engineering tickets."""

    def __init__(
        self,
        repository: SupabaseEngineeringTicketRepository,
        *,
        blocker_evaluators: Mapping[str, BlockerEvaluator],
        max_wakes: int = MAX_WAKES,
    ):
        if max_wakes < 1:
            raise ValueError("max_wakes must be positive")
        self.repository = repository
        self.blocker_evaluators = dict(blocker_evaluators)
        self.max_wakes = max_wakes

    def poll_due(self, *, limit: int = 5, now: datetime | None = None) -> PollBatch:
        at = now or utcnow()
        ready: list[dict[str, Any]] = []
        reparked: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for ticket in self.repository.claim_due(limit=limit):
            previous_status = str(ticket.get("previous_queue_status") or "")
            if previous_status != "PARKED":
                ready.append(ticket)
                continue

            reason = str(ticket.get("previous_parked_reason") or "").strip()
            wake_count = int(ticket.get("wake_count") or 0)
            if wake_count >= self.max_wakes:
                failed.append(
                    self.repository.fail_claimed(
                        ticket,
                        detail=(
                            f"parked blocker {reason or 'UNKNOWN'} remained unresolved after "
                            f"{wake_count} wakes; V17 governance intervention required"
                        ),
                        now=at,
                    )
                )
                continue

            evaluator = self.blocker_evaluators.get(reason)
            if evaluator is None:
                failed.append(
                    self.repository.fail_claimed(
                        ticket,
                        detail=f"no blocker evaluator registered for parked_reason={reason!r}",
                        now=at,
                    )
                )
                continue

            try:
                check = evaluator(ticket)
            except Exception as exc:  # transport/provider errors remain typed in context
                context = dict(ticket.get("parked_context") or {})
                context["last_blocker_check_error_type"] = type(exc).__name__
                reparked.append(
                    self.repository.park_claimed(
                        ticket,
                        reason=reason,
                        context=context,
                        detail=f"blocker re-evaluation raised {type(exc).__name__}",
                        now=at,
                    )
                )
                continue

            if check.disposition == "BLOCKED":
                reparked.append(
                    self.repository.park_claimed(
                        ticket,
                        reason=reason,
                        context=ticket.get("parked_context") or {},
                        detail=check.detail,
                        now=at,
                    )
                )
            elif check.disposition == "CLEARED":
                ready.append(self.repository.clear_claimed_blocker(ticket))
            elif check.disposition == "FAILED":
                failed.append(
                    self.repository.fail_claimed(ticket, detail=check.detail or reason, now=at)
                )
            else:
                failed.append(
                    self.repository.fail_claimed(
                        ticket,
                        detail=f"invalid blocker disposition={check.disposition!r}",
                        now=at,
                    )
                )

        return PollBatch(tuple(ready), tuple(reparked), tuple(failed))


def pr_review_blocker(fetch_pr_status: PrStatusFetcher) -> BlockerEvaluator:
    """Build an awaiting_pr_review evaluator around a Git provider adapter.

    The provider adapter is intentionally external so GitHub/GitLab credentials
    and API details do not enter queue state. APPROVED alone remains blocked;
    the next phase may proceed only after merge. CHANGES_REQUESTED clears the
    waiting state so the engineering graph can resume remediation.
    """

    def evaluate(ticket: Mapping[str, Any]) -> BlockerCheck:
        context = ticket.get("parked_context") or {}
        pr_id = str(context.get("pr_id") or "").strip()
        if not pr_id:
            return BlockerCheck("FAILED", "awaiting_pr_review ticket is missing parked_context.pr_id")
        status = str(fetch_pr_status(pr_id) or "").strip().upper()
        if status in {"MERGED", "APPROVED_AND_MERGED"}:
            return BlockerCheck("CLEARED", f"pull request {pr_id} merged")
        if status == "CHANGES_REQUESTED":
            return BlockerCheck("CLEARED", f"pull request {pr_id} has requested changes")
        if status in {"PENDING", "OPEN", "APPROVED"}:
            return BlockerCheck("BLOCKED", f"pull request {pr_id} status={status}")
        if status in {"CLOSED", "DECLINED"}:
            return BlockerCheck("FAILED", f"pull request {pr_id} closed without merge")
        return BlockerCheck("BLOCKED", f"pull request {pr_id} returned unrecognized status={status!r}")

    return evaluate
