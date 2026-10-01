from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.wow_engineering_ticket_queue import (
    MAX_WAKES,
    BlockerCheck,
    EngineeringTicketOrchestrator,
    parked_until_for,
    pr_review_blocker,
    ticket_to_issue,
)


NOW = datetime(2026, 10, 1, 22, 45, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self, claimed):
        self.claimed = [dict(row) for row in claimed]
        self.park_calls = []
        self.clear_calls = []
        self.fail_calls = []

    def claim_due(self, *, limit=5):
        return self.claimed[:limit]

    def park_claimed(self, ticket, *, reason, context=None, detail="", now=None):
        self.park_calls.append((dict(ticket), reason, dict(context or {}), detail, now))
        wakes = int(ticket.get("wake_count") or 0)
        row = dict(ticket)
        row.update(
            {
                "status": "BLOCKED",
                "queue_status": "PARKED",
                "parked_reason": reason,
                "parked_context": dict(context or {}),
                "parked_until": parked_until_for(reason, wakes, now=now).isoformat(),
                "wake_count": wakes + 1,
                "claim_owner": None,
                "claimed_at": None,
            }
        )
        return row

    def clear_claimed_blocker(self, ticket):
        self.clear_calls.append(dict(ticket))
        row = dict(ticket)
        row.update(
            {
                "status": "IN_PROGRESS",
                "queue_status": "IN_PROGRESS",
                "parked_reason": None,
                "parked_context": {},
                "parked_until": None,
                "wake_count": 0,
            }
        )
        return row

    def fail_claimed(self, ticket, *, detail, now=None):
        self.fail_calls.append((dict(ticket), detail, now))
        row = dict(ticket)
        row.update(
            {
                "status": "CLOSED",
                "queue_status": "FAILED",
                "terminal_state": "BLOCKED_WITH_EXACT_REASON",
                "failure_detail": detail,
                "claim_owner": None,
                "claimed_at": None,
            }
        )
        return row


def _claimed(*, previous="ACTIONABLE", reason=None, wake_count=0, context=None):
    return {
        "ticket_id": "WOW-ENG-TTL-TEST",
        "title": "TTL test",
        "class": "B",
        "priority": "P1",
        "scope": "engineering-control-plane",
        "status": "IN_PROGRESS",
        "queue_status": "IN_PROGRESS",
        "previous_queue_status": previous,
        "previous_parked_reason": reason,
        "parked_context": dict(context or {}),
        "wake_count": wake_count,
        "claim_owner": "test-worker",
    }


def test_ttl_mapping_and_backoff_cap():
    assert parked_until_for("api_rate_limit", 0, now=NOW) == NOW + timedelta(minutes=15)
    assert parked_until_for("upstream_dependency", 1, now=NOW) == NOW + timedelta(hours=2)
    assert parked_until_for("awaiting_pr_review", 1, now=NOW) == NOW + timedelta(hours=8)
    assert parked_until_for("awaiting_pr_review", 99, now=NOW) == NOW + timedelta(hours=24)
    assert parked_until_for("unknown_reason", 0, now=NOW) == NOW + timedelta(hours=1)


def test_actionable_ticket_is_ready_without_blocker_recheck():
    repo = FakeRepository([_claimed()])
    checks = {"awaiting_pr_review": lambda ticket: (_ for _ in ()).throw(AssertionError("must not run"))}
    batch = EngineeringTicketOrchestrator(repo, blocker_evaluators=checks).poll_due(now=NOW)
    assert [row["ticket_id"] for row in batch.ready] == ["WOW-ENG-TTL-TEST"]
    assert not batch.reparked
    assert not batch.failed


def test_woken_pending_pr_is_rechecked_then_reparked_with_backoff():
    repo = FakeRepository([
        _claimed(
            previous="PARKED",
            reason="awaiting_pr_review",
            wake_count=1,
            context={"pr_id": "1154"},
        )
    ])
    checks = {"awaiting_pr_review": lambda ticket: BlockerCheck("BLOCKED", "still pending")}
    batch = EngineeringTicketOrchestrator(repo, blocker_evaluators=checks).poll_due(now=NOW)
    assert not batch.ready
    assert not batch.failed
    assert len(batch.reparked) == 1
    parked = batch.reparked[0]
    assert parked["queue_status"] == "PARKED"
    assert parked["wake_count"] == 2
    assert parked["parked_until"] == (NOW + timedelta(hours=8)).isoformat()
    assert repo.park_calls[0][3] == "still pending"


def test_woken_cleared_blocker_resets_wake_count_but_retains_claim():
    repo = FakeRepository([
        _claimed(
            previous="PARKED",
            reason="awaiting_pr_review",
            wake_count=3,
            context={"pr_id": "1154"},
        )
    ])
    checks = {"awaiting_pr_review": lambda ticket: BlockerCheck("CLEARED", "merged")}
    batch = EngineeringTicketOrchestrator(repo, blocker_evaluators=checks).poll_due(now=NOW)
    assert len(batch.ready) == 1
    ready = batch.ready[0]
    assert ready["queue_status"] == "IN_PROGRESS"
    assert ready["wake_count"] == 0
    assert ready["claim_owner"] == "test-worker"
    assert not batch.reparked
    assert not batch.failed


def test_max_wakes_escalates_without_another_provider_check():
    calls = {"n": 0}

    def evaluator(ticket):
        calls["n"] += 1
        return BlockerCheck("BLOCKED")

    repo = FakeRepository([
        _claimed(
            previous="PARKED",
            reason="awaiting_pr_review",
            wake_count=MAX_WAKES,
            context={"pr_id": "1154"},
        )
    ])
    batch = EngineeringTicketOrchestrator(
        repo, blocker_evaluators={"awaiting_pr_review": evaluator}
    ).poll_due(now=NOW)
    assert calls["n"] == 0
    assert len(batch.failed) == 1
    assert batch.failed[0]["terminal_state"] == "BLOCKED_WITH_EXACT_REASON"
    assert "V17 governance intervention required" in batch.failed[0]["failure_detail"]


def test_missing_blocker_evaluator_fails_closed_with_exact_reason():
    repo = FakeRepository([
        _claimed(previous="PARKED", reason="unknown_dependency", wake_count=1)
    ])
    batch = EngineeringTicketOrchestrator(repo, blocker_evaluators={}).poll_due(now=NOW)
    assert len(batch.failed) == 1
    assert "no blocker evaluator registered" in batch.failed[0]["failure_detail"]


def test_blocker_check_transport_error_reparks_instead_of_stranding_claim():
    def broken(ticket):
        raise TimeoutError("provider timed out")

    repo = FakeRepository([
        _claimed(
            previous="PARKED",
            reason="upstream_dependency",
            wake_count=2,
            context={"dependency": "build-7"},
        )
    ])
    batch = EngineeringTicketOrchestrator(
        repo, blocker_evaluators={"upstream_dependency": broken}
    ).poll_due(now=NOW)
    assert len(batch.reparked) == 1
    _, _, context, detail, _ = repo.park_calls[0]
    assert context["last_blocker_check_error_type"] == "TimeoutError"
    assert "TimeoutError" in detail


def test_pr_review_evaluator_requires_merge_and_handles_requested_changes():
    assert pr_review_blocker(lambda pr_id: "PENDING")(
        _claimed(context={"pr_id": "42"})
    ).disposition == "BLOCKED"
    assert pr_review_blocker(lambda pr_id: "APPROVED")(
        _claimed(context={"pr_id": "42"})
    ).disposition == "BLOCKED"
    assert pr_review_blocker(lambda pr_id: "MERGED")(
        _claimed(context={"pr_id": "42"})
    ).disposition == "CLEARED"
    assert pr_review_blocker(lambda pr_id: "CHANGES_REQUESTED")(
        _claimed(context={"pr_id": "42"})
    ).disposition == "CLEARED"
    failed = pr_review_blocker(lambda pr_id: "CLOSED")(
        _claimed(context={"pr_id": "42"})
    )
    assert failed.disposition == "FAILED"
    assert "without merge" in failed.detail


def test_ticket_to_issue_keeps_queue_control_out_of_langgraph_state():
    issue = ticket_to_issue(_claimed())
    assert issue == {
        "issue_id": "WOW-ENG-TTL-TEST",
        "severity": "P1",
        "change_class": "B",
        "component": "engineering-control-plane",
        "sport": None,
        "promotion_authorized": False,
    }
    assert "queue_status" not in issue
    assert "claim_owner" not in issue
    assert "parked_until" not in issue


def test_migration_uses_atomic_skip_locked_claim_and_service_role_only_rpc():
    root = Path(__file__).resolve().parents[3]
    migration = (
        root
        / "artifacts"
        / "wow-engine"
        / "migrations"
        / "20261001224500_engineering_ticket_ttl_parking.sql"
    ).read_text()
    normalized = " ".join(migration.lower().split())
    assert "for update skip locked" in normalized
    assert "queue_status = 'actionable'" in normalized
    assert "queue_status = 'parked' and b.parked_until <= current_timestamp" in normalized
    assert "b.terminal_state is null" in normalized
    assert "security invoker" in normalized
    assert "revoke all on function public.wow_claim_engineering_tickets(text, integer) from public" in normalized
    assert "grant execute on function public.wow_claim_engineering_tickets(text, integer) to service_role" in normalized
    assert "previous_parked_reason" in normalized
    assert "model_unavailable" not in normalized
