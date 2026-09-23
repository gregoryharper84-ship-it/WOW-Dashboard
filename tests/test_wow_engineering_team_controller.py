from datetime import datetime, timedelta, timezone

import pytest

from wow_engineering.team_controller import (
    GovernanceViolation,
    Incident,
    LeaseConflict,
    acquire_implementation_lease,
    choose_primary_incident,
    improvement_capacity_available,
    overnight_status,
    validate_frontier_action,
)


def incident(identifier, severity, priority, *, blocked=False, terminal=None):
    return Incident(
        incident_id=identifier,
        severity=severity,
        priority=priority,
        lifecycle_stage="DETECTED",
        next_action="reproduce",
        hard_blocked=blocked,
        terminal_outcome=terminal,
    )


def test_r0_preempts_higher_numeric_priority_r2():
    selected = choose_primary_incident([
        incident("r2", "R2", 999),
        incident("r0", "R0", 1),
    ])
    assert selected.incident_id == "r0"


def test_higher_priority_wins_within_same_severity():
    selected = choose_primary_incident([
        incident("a", "R1", 10),
        incident("b", "R1", 20),
    ])
    assert selected.incident_id == "b"


def test_blocked_and_terminal_incidents_are_not_selected():
    selected = choose_primary_incident([
        incident("blocked", "R0", 100, blocked=True),
        incident("closed", "R0", 100, terminal="FIXED_AND_VERIFIED"),
        incident("live", "R1", 1),
    ])
    assert selected.incident_id == "live"


def test_r0_r1_preempt_improvement_capacity():
    assert not improvement_capacity_available([incident("x", "R1", 1)])
    assert improvement_capacity_available([incident("x", "R2", 1)])


def test_only_implementation_agent_can_take_lease():
    now = datetime.now(timezone.utc)
    with pytest.raises(GovernanceViolation):
        acquire_implementation_lease(
            incident("x", "R1", 1), owner="frontier_intelligence", now=now, expires_at=now + timedelta(minutes=30)
        )


def test_active_lease_cannot_be_replaced():
    now = datetime.now(timezone.utc)
    leased = acquire_implementation_lease(
        incident("x", "R1", 1), owner="implementation", now=now, expires_at=now + timedelta(minutes=30)
    )
    with pytest.raises(LeaseConflict):
        acquire_implementation_lease(
            leased, owner="implementation", now=now + timedelta(minutes=1), expires_at=now + timedelta(minutes=31)
        )


def test_frontier_agent_cannot_promote_production_model():
    with pytest.raises(GovernanceViolation):
        validate_frontier_action(change_class="C", action="PROMOTE_PRODUCTION_MODEL")


def test_class_c_must_remain_on_challenger_path():
    validate_frontier_action(change_class="C", action="CREATE_CHALLENGER")
    with pytest.raises(GovernanceViolation):
        validate_frontier_action(change_class="C", action="DIRECT_PRODUCTION_PATCH")


def test_overnight_is_incomplete_with_actionable_r1():
    assert overnight_status([incident("x", "R1", 1)]) == "INCOMPLETE_ENGINEERING_RUN"


def test_overnight_can_close_when_r0_r1_are_terminal_or_blocked():
    assert overnight_status([
        incident("x", "R0", 2, terminal="FIXED_AND_VERIFIED"),
        incident("y", "R1", 1, blocked=True),
        incident("z", "R2", 100),
    ]) == "ENGINEERING_RUN_COMPLETE"
