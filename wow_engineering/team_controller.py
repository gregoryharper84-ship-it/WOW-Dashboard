"""Deterministic controller for the WOW V17 multi-agent engineering team.

This module does not score sports, alter model probabilities, or execute wagers.
It coordinates engineering work while preserving V17 governance.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable, Optional

TERMINAL_OUTCOMES = {
    "FIXED_AND_VERIFIED",
    "PR_CREATED",
    "EXPERIMENT_CREATED",
    "DUPLICATE",
    "NOT_REPRODUCIBLE",
    "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
}

SEVERITY_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


@dataclass(frozen=True)
class Incident:
    incident_id: str
    severity: str
    priority: int
    lifecycle_stage: str
    next_action: str
    terminal_outcome: Optional[str] = None
    hard_blocked: bool = False
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None

    @property
    def terminal(self) -> bool:
        return self.terminal_outcome in TERMINAL_OUTCOMES

    @property
    def actionable(self) -> bool:
        return not self.terminal and not self.hard_blocked


class LeaseConflict(RuntimeError):
    pass


class GovernanceViolation(RuntimeError):
    pass


def choose_primary_incident(incidents: Iterable[Incident]) -> Optional[Incident]:
    """Choose exactly one highest-priority actionable incident.

    R0/R1 reliability incidents always preempt R2/R3 work. Within severity,
    higher numeric priority wins, then incident_id provides deterministic order.
    """
    candidates = [i for i in incidents if i.actionable]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda i: (SEVERITY_ORDER.get(i.severity, 99), -i.priority, i.incident_id),
    )[0]


def improvement_capacity_available(incidents: Iterable[Incident]) -> bool:
    """Improvement work may consume execution capacity only if no actionable R0/R1 exists."""
    return not any(i.actionable and i.severity in {"R0", "R1"} for i in incidents)


def acquire_implementation_lease(
    incident: Incident,
    *,
    owner: str,
    now: datetime,
    expires_at: datetime,
) -> Incident:
    """Acquire the single implementation lease for an incident."""
    if owner != "implementation":
        raise GovernanceViolation("only the implementation agent may hold a repair lease")
    if incident.terminal:
        raise GovernanceViolation("terminal incidents cannot be leased")
    if expires_at <= now:
        raise GovernanceViolation("lease expiry must be after acquisition time")

    active = (
        incident.lease_owner is not None
        and incident.lease_expires_at is not None
        and incident.lease_expires_at > now
    )
    if active:
        raise LeaseConflict(f"incident {incident.incident_id} already has an active lease")

    return replace(
        incident,
        lease_owner=owner,
        lease_expires_at=expires_at,
        lifecycle_stage="IMPLEMENTING",
    )


def validate_frontier_action(*, change_class: str, action: str) -> None:
    """Guard the Continuous Improvement Agent from unsafe promotion actions."""
    prohibited = {"PROMOTE_PRODUCTION_MODEL", "ALTER_PRODUCTION_PROBABILITY", "EXECUTE_WAGER"}
    if action in prohibited:
        raise GovernanceViolation(f"frontier intelligence may not perform {action}")
    if change_class == "C" and action not in {
        "CREATE_HYPOTHESIS",
        "CREATE_CHALLENGER",
        "RUN_HISTORICAL_REPLAY",
        "RUN_COUNTEREXAMPLE_REVIEW",
        "RUN_HOLDOUT_VALIDATION",
        "RUN_REGRESSION",
        "CREATE_RECOMMENDATION",
    }:
        raise GovernanceViolation("Class C work must remain on the governed challenger path")


def overnight_status(incidents: Iterable[Incident]) -> str:
    """Apply the agreed strict overnight success definition."""
    if any(i.actionable and i.severity in {"R0", "R1"} for i in incidents):
        return "INCOMPLETE_ENGINEERING_RUN"
    return "ENGINEERING_RUN_COMPLETE"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
