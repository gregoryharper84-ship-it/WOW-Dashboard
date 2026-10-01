from services.wow_engineering_ticket_telemetry import InstrumentedEngineeringTicketRepository


class FakeRepository:
    def __init__(self):
        self.claim_owner = "worker-1"

    def claim_due(self, *, limit=5):
        return [
            {
                "ticket_id": "T-1",
                "previous_queue_status": "PARKED",
                "previous_parked_reason": "awaiting_pr_review",
                "wake_count": 2,
            },
            {
                "ticket_id": "T-2",
                "previous_queue_status": "ACTIONABLE",
                "wake_count": 0,
            },
        ][:limit]

    def park_claimed(self, ticket, **kwargs):
        return {
            **ticket,
            "queue_status": "PARKED",
            "parked_reason": kwargs["reason"],
            "wake_count": int(ticket.get("wake_count") or 0) + 1,
        }

    def clear_claimed_blocker(self, ticket):
        return {**ticket, "queue_status": "IN_PROGRESS", "wake_count": 0}

    def fail_claimed(self, ticket, **kwargs):
        return {**ticket, "queue_status": "FAILED", "terminal_state": "BLOCKED_WITH_EXACT_REASON"}


def test_claim_due_emits_claim_and_wake_events_without_changing_rows():
    events = []
    repo = InstrumentedEngineeringTicketRepository(FakeRepository(), queue_sink=events.append)
    rows = repo.claim_due(limit=5)

    assert [row["ticket_id"] for row in rows] == ["T-1", "T-2"]
    assert [event["event_name"] for event in events] == [
        "QUEUE_CLAIMED",
        "QUEUE_WAKE_RECHECK",
        "QUEUE_CLAIMED",
    ]
    assert all(event["can_execute"] is False for event in events)


def test_repark_and_unblock_emit_lifecycle_events_after_delegate_mutation():
    events = []
    repo = InstrumentedEngineeringTicketRepository(FakeRepository(), queue_sink=events.append)
    ticket = {
        "ticket_id": "T-3",
        "previous_parked_reason": "api_rate_limit",
        "wake_count": 1,
    }

    parked = repo.park_claimed(ticket, reason="api_rate_limit")
    unblocked = repo.clear_claimed_blocker(ticket)

    assert parked["queue_status"] == "PARKED"
    assert unblocked["queue_status"] == "IN_PROGRESS"
    assert [event["event_name"] for event in events] == ["QUEUE_REPARKED", "QUEUE_UNBLOCKED"]


def test_max_wake_failure_emits_escalation_counter_event():
    events = []
    repo = InstrumentedEngineeringTicketRepository(FakeRepository(), queue_sink=events.append)
    ticket = {
        "ticket_id": "T-4",
        "previous_parked_reason": "upstream_dependency",
        "wake_count": 6,
    }

    row = repo.fail_claimed(ticket, detail="max wakes")

    assert row["queue_status"] == "FAILED"
    assert events[-1]["event_name"] == "QUEUE_MAX_WAKE_ESCALATED"


def test_non_max_wake_failure_is_not_mislabeled_as_ceiling_escalation():
    events = []
    repo = InstrumentedEngineeringTicketRepository(FakeRepository(), queue_sink=events.append)
    ticket = {
        "ticket_id": "T-5",
        "previous_parked_reason": "unknown_dependency",
        "wake_count": 1,
    }

    repo.fail_claimed(ticket, detail="no evaluator")

    assert events[-1]["event_name"] == "QUEUE_FAILED"
