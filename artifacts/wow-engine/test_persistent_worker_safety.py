from __future__ import annotations

from v17.persistent_worker_safety import (
    AttemptReceipt,
    CircuitState,
    Lease,
    MigrationDecision,
    ProgressDecision,
    RetryAction,
    RetryState,
    RollbackDecision,
    WorkerMode,
    assess_material_progress,
    build_dlq_receipt,
    build_ticket_context,
    circuit_mode,
    consecutive_no_progress_count,
    lease_allows_mutation,
    migration_decision,
    progress_aware_retry_action,
    retry_action,
    rollback_decision,
    safe_hold_receipt,
    stable_content_hash,
    stable_failure_fingerprint,
)


def test_ticket_context_drops_previous_ticket_and_unknown_state() -> None:
    context = build_ticket_context(
        {
            "incident_id": "PM-823",
            "typed_failure": "ACTION_TRANSPORT_FAILURE",
            "acceptance_criteria": ["preserve typed failure"],
            "prior_ticket_chat": "PM-502 database fix",
            "freeform_agent_memory": "carry this forward",
        },
        execution_id="exec-pm823-001",
    )
    assert context["incident_id"] == "PM-823"
    assert context["execution_id"] == "exec-pm823-001"
    assert "prior_ticket_chat" not in context
    assert "freeform_agent_memory" not in context
    assert context["can_execute"] is False
    assert context["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_stale_lease_epoch_cannot_mutate_after_reassignment() -> None:
    stale = Lease("PM-823", "worker-a", 17, "2026-10-02T16:00:00Z")
    current = Lease("PM-823", "worker-b", 18, "2026-10-02T17:00:00Z")
    assert lease_allows_mutation(
        stale,
        expected_work_item_id="PM-823",
        latest_epoch=current.lease_epoch,
        current_worker_id="worker-a",
        now="2026-10-02T15:30:00Z",
    ) is False
    assert lease_allows_mutation(
        current,
        expected_work_item_id="PM-823",
        latest_epoch=current.lease_epoch,
        current_worker_id="worker-b",
        now="2026-10-02T16:30:00Z",
    ) is True


def test_foreign_worker_cannot_mutate_even_with_current_epoch() -> None:
    claim = Lease("PM-823", "worker-b", 18, "2026-10-02T17:00:00Z")
    assert lease_allows_mutation(
        claim,
        expected_work_item_id="PM-823",
        latest_epoch=18,
        current_worker_id="worker-a",
        now="2026-10-02T16:30:00Z",
    ) is False


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


def test_expired_lease_cannot_mutate() -> None:
    claim = Lease("PM-823", "worker-b", 18, "2026-10-02T17:00:00Z")
    assert lease_allows_mutation(
        claim,
        expected_work_item_id="PM-823",
        latest_epoch=18,
        current_worker_id="worker-b",
        now="2026-10-02T17:00:00Z",
    ) is False


def test_wrong_ticket_lease_cannot_mutate() -> None:
    claim = Lease("PM-823", "worker-b", 18, "2026-10-02T18:00:00Z")
    assert lease_allows_mutation(
        claim,
        expected_work_item_id="PM-502",
        latest_epoch=18,
        current_worker_id="worker-b",
        now="2026-10-02T17:00:00Z",
    ) is False


def test_context_requires_fresh_execution_identity() -> None:
    try:
        build_ticket_context({"incident_id": "PM-823"}, execution_id="")
    except ValueError as exc:
        assert "execution_id" in str(exc)
    else:
        raise AssertionError("blank execution identity must fail closed")


def _attempt(
    attempt_id: str,
    *,
    fingerprint: str = "fp-1",
    stage: str = "PATCH",
    boundary: str = "parser",
    patch_sha: str = "",
    failing_tests_hash: str = "tests-a",
    acceptance_pass_count: int = 0,
    evidence_hash: str = "evidence-a",
) -> AttemptReceipt:
    return AttemptReceipt(
        attempt_id=attempt_id,
        ticket_id="PM-823",
        failure_fingerprint=fingerprint,
        workflow_stage=stage,
        first_failing_boundary=boundary,
        typed_failure="ACTION_TRANSPORT_FAILURE",
        patch_sha=patch_sha,
        failing_tests_hash=failing_tests_hash,
        acceptance_pass_count=acceptance_pass_count,
        evidence_hash=evidence_hash,
        context_token_count=1200,
    )


def test_failure_fingerprint_ignores_volatile_ids_timestamps_and_line_numbers() -> None:
    a = stable_failure_fingerprint(
        error_type="RemoteProtocolError",
        message="request_id=abc123 failed at 2026-10-02T16:00:00Z",
        stack_frames=['File "worker.py", line 101', "0xABC123"],
        typed_failure="ACTION_TRANSPORT_FAILURE",
        workflow_stage="PATCH",
        failing_test="test_transport",
    )
    b = stable_failure_fingerprint(
        error_type="RemoteProtocolError",
        message="request_id=xyz999 failed at 2026-10-02T16:05:00Z",
        stack_frames=['File "worker.py", line 904', "0xDEF456"],
        typed_failure="ACTION_TRANSPORT_FAILURE",
        workflow_stage="PATCH",
        failing_test="test_transport",
    )
    assert a == b


def test_failure_fingerprint_changes_for_material_failure_change() -> None:
    a = stable_failure_fingerprint(
        error_type="RemoteProtocolError",
        message="connection closed",
        typed_failure="ACTION_TRANSPORT_FAILURE",
        workflow_stage="PATCH",
    )
    b = stable_failure_fingerprint(
        error_type="ValueError",
        message="invalid canonical event id",
        typed_failure="CANONICAL_IDENTITY_FAILURE",
        workflow_stage="PATCH",
    )
    assert a != b


def test_same_failure_with_stage_advancement_is_progress() -> None:
    previous = _attempt("1", stage="REPRODUCE")
    current = _attempt("2", stage="ROOT_CAUSE")
    assert assess_material_progress(previous, current) == ProgressDecision.PROGRESS


def test_acceptance_criterion_advancement_is_progress() -> None:
    previous = _attempt("1", acceptance_pass_count=1)
    current = _attempt("2", acceptance_pass_count=2)
    assert assess_material_progress(previous, current) == ProgressDecision.PROGRESS


def test_new_evidence_or_test_boundary_is_progress() -> None:
    previous = _attempt("1", evidence_hash=stable_content_hash(["trace-a"]))
    current = _attempt("2", evidence_hash=stable_content_hash(["trace-a", "trace-b"]))
    assert assess_material_progress(previous, current) == ProgressDecision.PROGRESS

    later = _attempt(
        "3",
        evidence_hash=current.evidence_hash,
        failing_tests_hash=stable_content_hash(["test_transport", "test_identity"]),
    )
    assert assess_material_progress(current, later) == ProgressDecision.PROGRESS


def test_new_patch_sha_alone_does_not_count_as_progress() -> None:
    previous = _attempt("1", patch_sha="sha-a")
    current = _attempt("2", patch_sha="sha-b")
    assert assess_material_progress(previous, current) == ProgressDecision.NO_PROGRESS


def test_repeated_same_failure_without_progress_goes_to_dlq() -> None:
    history = [
        _attempt("1", patch_sha="sha-a"),
        _attempt("2", patch_sha="sha-b"),
        _attempt("3", patch_sha="sha-c"),
    ]
    assert consecutive_no_progress_count(history) == 2
    assert progress_aware_retry_action(history, no_progress_limit=2, max_attempts=5) == RetryAction.DLQ


def test_progress_resets_trailing_stagnation_count() -> None:
    history = [
        _attempt("1", stage="REPRODUCE"),
        _attempt("2", stage="ROOT_CAUSE"),
        _attempt("3", stage="ROOT_CAUSE", patch_sha="sha-a"),
    ]
    assert consecutive_no_progress_count(history) == 1
    assert progress_aware_retry_action(history, no_progress_limit=2, max_attempts=5) == RetryAction.RETRY


def test_different_failure_fingerprint_resets_stagnation_chain() -> None:
    history = [
        _attempt("1", fingerprint="fp-a"),
        _attempt("2", fingerprint="fp-a"),
        _attempt("3", fingerprint="fp-b"),
    ]
    assert consecutive_no_progress_count(history) == 0
    assert progress_aware_retry_action(history, no_progress_limit=2, max_attempts=5) == RetryAction.RETRY


def test_hard_attempt_ceiling_still_prevents_infinite_loop() -> None:
    history = [
        _attempt("1", fingerprint="fp-1", stage="REPRODUCE"),
        _attempt("2", fingerprint="fp-2", stage="ROOT_CAUSE"),
        _attempt("3", fingerprint="fp-3", stage="PATCH"),
        _attempt("4", fingerprint="fp-4", stage="NARROW_TEST"),
        _attempt("5", fingerprint="fp-5", stage="REGRESSION"),
    ]
    assert progress_aware_retry_action(history, no_progress_limit=2, max_attempts=5) == RetryAction.DLQ
