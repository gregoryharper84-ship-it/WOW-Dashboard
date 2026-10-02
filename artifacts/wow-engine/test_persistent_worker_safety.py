from __future__ import annotations

from v17.persistent_worker_safety import (
    CircuitState,
    Lease,
    MigrationDecision,
    RetryAction,
    RetryState,
    RollbackDecision,
    WorkerMode,
    build_dlq_receipt,
    build_ticket_context,
    circuit_mode,
    lease_allows_mutation,
    migration_decision,
    retry_action,
    rollback_decision,
    safe_hold_receipt,
)


def test_ticket_context_drops_previous_ticket_and_unknown_state() -> None:
    context = build_ticket_context(
        {
            "incident_id": "PM-823",
            "typed_failure": "ACTION_TRANSPORT_FAILURE",
            "acceptance_criteria": ["preserve typed failure"],
            "prior_ticket_chat": "PM-502 database fix",
            "freeform_agent_memory": "carry this forward",
        }
    )
    assert context["incident_id"] == "PM-823"
    assert "prior_ticket_chat" not in context
    assert "freeform_agent_memory" not in context
    assert context["can_execute"] is False
    assert context["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_stale_lease_epoch_cannot_mutate_after_reassignment() -> None:
    stale = Lease("PM-823", "worker-a", 17, "2026-10-02T16:00:00Z")
    current = Lease("PM-823", "worker-b", 18, "2026-10-02T17:00:00Z")
    assert lease_allows_mutation(stale, latest_epoch=current.lease_epoch, current_worker_id="worker-a") is False
    assert lease_allows_mutation(current, latest_epoch=current.lease_epoch, current_worker_id="worker-b") is True


def test_foreign_worker_cannot_mutate_even_with_current_epoch() -> None:
    claim = Lease("PM-823", "worker-b", 18, "2026-10-02T17:00:00Z")
    assert lease_allows_mutation(claim, latest_epoch=18, current_worker_id="worker-a") is False


def test_repeated_no_progress_failure_goes_to_dlq() -> None:
    state = RetryState(attempts=3, repeated_fingerprint_count=3, progress_events=0)
    assert retry_action(state) == RetryAction.DLQ


def test_progressing_ticket_can_continue_within_attempt_bound() -> None:
    state = RetryState(attempts=4, repeated_fingerprint_count=2, progress_events=2)
    assert retry_action(state) == RetryAction.RETRY


def test_attempt_cap_ejects_ticket_even_without_same_fingerprint() -> None:
    state = RetryState(attempts=5, repeated_fingerprint_count=1, progress_events=0)
    assert retry_action(state) == RetryAction.DLQ


def test_systemic_failure_rate_enters_safe_hold() -> None:
    state = CircuitState(total_recent_items=10, systemic_failures=6, rollback_failures=0, provider_failures=0)
    assert circuit_mode(state) == WorkerMode.SAFE_HOLD


def test_provider_failure_burst_enters_safe_hold() -> None:
    state = CircuitState(total_recent_items=2, systemic_failures=0, rollback_failures=0, provider_failures=4)
    assert circuit_mode(state) == WorkerMode.SAFE_HOLD


def test_healthy_system_remains_running() -> None:
    state = CircuitState(total_recent_items=10, systemic_failures=1, rollback_failures=0, provider_failures=0)
    assert circuit_mode(state) == WorkerMode.RUNNING


def test_auto_rollback_requires_attribution_known_good_artifact_and_db_compatibility() -> None:
    assert rollback_decision(
        production_verification_failed=True,
        attributable_to_release=True,
        rollback_artifact_known_good=True,
        database_state_compatible=True,
    ) == RollbackDecision.AUTO_ROLLBACK

    assert rollback_decision(
        production_verification_failed=True,
        attributable_to_release=False,
        rollback_artifact_known_good=True,
        database_state_compatible=True,
    ) == RollbackDecision.HOLD_FOR_REVIEW

    assert rollback_decision(
        production_verification_failed=True,
        attributable_to_release=True,
        rollback_artifact_known_good=True,
        database_state_compatible=False,
    ) == RollbackDecision.HOLD_FOR_REVIEW


def test_destructive_production_down_migration_requires_human_intervention() -> None:
    assert migration_decision(
        production=True,
        destructive=True,
        direction="DOWN",
    ) == MigrationDecision.HUMAN_INTERVENTION_REQUIRED


def test_non_destructive_expand_migration_is_allowed() -> None:
    assert migration_decision(
        production=True,
        destructive=False,
        direction="UP",
    ) == MigrationDecision.ALLOW_NON_DESTRUCTIVE


def test_dlq_receipt_preserves_governance() -> None:
    receipt = build_dlq_receipt(
        incident_id="PM-823",
        failure_fingerprint="syntax:error:foo.py",
        attempts=3,
        last_good_state="TRIAGED",
        recommended_recovery_route="human-review",
    )
    assert receipt["terminal_bucket"] == "DLQ"
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_safe_hold_stops_mutation_but_keeps_observability() -> None:
    receipt = safe_hold_receipt(["provider failure burst"])
    assert receipt["worker_mode"] == "SAFE_HOLD"
    assert receipt["mutations_allowed"] is False
    assert receipt["observability_allowed"] is True
    assert receipt["can_execute"] is False
