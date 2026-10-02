from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class WorkerMode(str, Enum):
    RUNNING = "RUNNING"
    SAFE_HOLD = "SAFE_HOLD"


class RetryAction(str, Enum):
    RETRY = "RETRY"
    DLQ = "DLQ"


class RollbackDecision(str, Enum):
    AUTO_ROLLBACK = "AUTO_ROLLBACK"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    NO_ROLLBACK = "NO_ROLLBACK"


class MigrationDecision(str, Enum):
    ALLOW_NON_DESTRUCTIVE = "ALLOW_NON_DESTRUCTIVE"
    HUMAN_INTERVENTION_REQUIRED = "DB_MIGRATION_INTERVENTION_REQUIRED"


TICKET_CONTEXT_FIELDS = {
    "incident_id",
    "severity",
    "state",
    "typed_failure",
    "defect_contract",
    "reproduction_evidence",
    "acceptance_criteria",
    "relevant_paths",
    "governance_invariants",
    "same_ticket_attempts",
    "branch",
    "commit_sha",
    "deployment_id",
}


@dataclass(frozen=True)
class Lease:
    work_item_id: str
    worker_id: str
    lease_epoch: int
    expires_at: str


@dataclass(frozen=True)
class RetryState:
    attempts: int
    repeated_fingerprint_count: int
    progress_events: int


@dataclass(frozen=True)
class CircuitState:
    total_recent_items: int
    systemic_failures: int
    rollback_failures: int
    provider_failures: int


def build_ticket_context(record: dict[str, Any], *, execution_id: str) -> dict[str, Any]:
    """Build a fresh, ticket-scoped execution envelope.

    Unknown fields are deliberately dropped so prior-ticket conversational state
    cannot leak through a generic record payload. The caller must supply a fresh
    execution_id for each ticket execution.
    """
    if not str(execution_id).strip():
        raise ValueError("execution_id is required for isolated ticket context")
    context = {key: record[key] for key in sorted(TICKET_CONTEXT_FIELDS) if key in record}
    context["execution_id"] = str(execution_id)
    context["can_execute"] = CAN_EXECUTE
    context["terminal_authority"] = TERMINAL_AUTHORITY
    return context


def lease_allows_mutation(
    claim: Lease,
    *,
    expected_work_item_id: str,
    latest_epoch: int,
    current_worker_id: str,
    now: str,
) -> bool:
    """Reject expired, stale, foreign, or wrong-ticket workers before mutation."""
    return (
        claim.work_item_id == expected_work_item_id
        and claim.lease_epoch == latest_epoch
        and claim.worker_id == current_worker_id
        and claim.lease_epoch >= 0
        and bool(str(now).strip())
        and str(now) < claim.expires_at
    )


def retry_action(
    state: RetryState,
    *,
    max_attempts: int = 5,
    same_fingerprint_limit: int = 3,
) -> RetryAction:
    """Eject a poisoned ticket when retries are bounded and no progress is occurring."""
    if state.progress_events > 0 and state.attempts < max_attempts:
        return RetryAction.RETRY
    if state.repeated_fingerprint_count >= same_fingerprint_limit:
        return RetryAction.DLQ
    if state.attempts >= max_attempts:
        return RetryAction.DLQ
    return RetryAction.RETRY


def circuit_mode(
    state: CircuitState,
    *,
    min_sample: int = 5,
    systemic_failure_ratio: float = 0.6,
    rollback_failure_limit: int = 2,
    provider_failure_limit: int = 4,
) -> WorkerMode:
    """Fail closed on system-wide failure while allowing observability to continue."""
    if state.rollback_failures >= rollback_failure_limit:
        return WorkerMode.SAFE_HOLD
    if state.provider_failures >= provider_failure_limit:
        return WorkerMode.SAFE_HOLD
    if state.total_recent_items >= min_sample:
        ratio = state.systemic_failures / max(state.total_recent_items, 1)
        if ratio >= systemic_failure_ratio:
            return WorkerMode.SAFE_HOLD
    return WorkerMode.RUNNING


def rollback_decision(
    *,
    production_verification_failed: bool,
    attributable_to_release: bool,
    rollback_artifact_known_good: bool,
    database_state_compatible: bool,
) -> RollbackDecision:
    """Permit automatic application rollback only when causality and safety are proven."""
    if not production_verification_failed:
        return RollbackDecision.NO_ROLLBACK
    if attributable_to_release and rollback_artifact_known_good and database_state_compatible:
        return RollbackDecision.AUTO_ROLLBACK
    return RollbackDecision.HOLD_FOR_REVIEW


def migration_decision(
    *,
    production: bool,
    destructive: bool,
    direction: str,
) -> MigrationDecision:
    """Never authorize an autonomous destructive production DOWN migration."""
    normalized_direction = str(direction or "").upper()
    if production and destructive and normalized_direction == "DOWN":
        return MigrationDecision.HUMAN_INTERVENTION_REQUIRED
    return MigrationDecision.ALLOW_NON_DESTRUCTIVE


def build_dlq_receipt(
    *,
    incident_id: str,
    failure_fingerprint: str,
    attempts: int,
    last_good_state: str,
    recommended_recovery_route: str,
) -> dict[str, Any]:
    return {
        "incident_id": incident_id,
        "state": "AGENT_STALLED",
        "terminal_bucket": "DLQ",
        "failure_fingerprint": failure_fingerprint,
        "attempts": attempts,
        "last_good_state": last_good_state,
        "recommended_recovery_route": recommended_recovery_route,
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def safe_hold_receipt(reasons: Iterable[str]) -> dict[str, Any]:
    return {
        "worker_mode": WorkerMode.SAFE_HOLD.value,
        "mutations_allowed": False,
        "observability_allowed": True,
        "reasons": [str(reason) for reason in reasons if str(reason).strip()],
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
