"""Telemetry adapter for the TTL-aware WOW engineering ticket repository."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from services.wow_engineering_ticket_queue import MAX_WAKES


EventSink = Callable[[Mapping[str, Any]], None]


class InstrumentedEngineeringTicketRepository:
    """Decorate a ticket repository with queue lifecycle telemetry.

    The wrapper preserves the repository interface and delegates every state
    mutation to the underlying implementation before emitting its receipt.
    Metric consumers should avoid ticket_id as a metric label; it is included
    only so trace/log sinks can correlate a single engineering ticket.
    """

    def __init__(self, repository: Any, *, queue_sink: EventSink):
        self.repository = repository
        self.queue_sink = queue_sink

    @property
    def claim_owner(self) -> str | None:
        return getattr(self.repository, "claim_owner", None)

    def _emit(self, event_name: str, ticket: Mapping[str, Any], **extra: Any) -> None:
        event = {
            "event_name": event_name,
            "ticket_id": str(ticket.get("ticket_id") or "UNKNOWN"),
            "parked_reason": (
                ticket.get("previous_parked_reason")
                or ticket.get("parked_reason")
                or "NONE"
            ),
            "wake_count": int(ticket.get("wake_count") or 0),
            "can_execute": False,
            **extra,
        }
        self.queue_sink(event)

    def claim_due(self, *, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.repository.claim_due(limit=limit)
        for row in rows:
            self._emit("QUEUE_CLAIMED", row)
            if str(row.get("previous_queue_status") or "") == "PARKED":
                self._emit("QUEUE_WAKE_RECHECK", row)
        return rows

    def park_claimed(self, ticket: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        row = self.repository.park_claimed(ticket, **kwargs)
        self._emit("QUEUE_REPARKED", row)
        return row

    def clear_claimed_blocker(self, ticket: Mapping[str, Any]) -> dict[str, Any]:
        row = self.repository.clear_claimed_blocker(ticket)
        self._emit("QUEUE_UNBLOCKED", row)
        return row

    def fail_claimed(self, ticket: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        row = self.repository.fail_claimed(ticket, **kwargs)
        if int(ticket.get("wake_count") or 0) >= MAX_WAKES:
            self._emit("QUEUE_MAX_WAKE_ESCALATED", ticket)
        else:
            self._emit("QUEUE_FAILED", ticket)
        return row
