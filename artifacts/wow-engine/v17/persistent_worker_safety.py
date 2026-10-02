from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable, Sequence


CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class WorkerMode(str, Enum):
    RUNNING = "RUNNING"
    SAFE_HOLD = "SAFE_HOLD"


class RetryAction(str, Enum):
    RETRY = "RETRY"
    DLQ = "DLQ"


class ProgressDecision(str, Enum):
    PROGRESS = "PROGRESS"
    NO_PROGRESS = "NO_PROGRESS"


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
class AttemptReceipt:
    attempt_id: str
    ticket_id: str
    failure_fingerprint: str
    workflow_stage: str
    first_failing_boundary: str
    typed_failure: str
    patch_sha: str
    failing_tests_hash: str
    acceptance_pass_count: int
    evidence_hash: str
    context_token_count: int = 0


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


WORKFLOW_STAGE_ORDER = {
    "DISCOVERED": 0,
    "REPRODUCE": 1,
    "ROOT_CAUSE": 2,
    "PATCH": 3,
    "NARROW_TEST": 4,
    "REGRESSION": 5,
    "REVIEW": 6,
    "RELEASE": 7,
    "PRODUCTION_VERIFY": 8,
}

_VOLATILE_PATTERNS = (
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b20\d{2}-\d{2}-\d{2}[T ][0-9:.+-]+Z?\b"), "<timestamp>"),
    (re.compile(r"0x[0-9a-f]+", re.I), "<hex>"),
    (re.compile(r"\b(?:request|run|trace|correlation)[-_ ]?id[=: ]+[A-Za-z0-9._:-]+", re.I), "request_id=<id>"),
    (re.compile(r'File "([^"]+)", line \d+'), r'File "\1", line <n>'),
)


def _normalize_failure_text(value: str) -> str:
    normalized = str(value or "").strip()
    for pattern, replacement in _VOLATILE_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return " ".join(normalized.split())


def stable_failure_fingerprint(
    *,
    error_type: str,
    message: str,
    stack_frames: Sequence[str] = (),
    typed_failure: str = "",
    workflow_stage: str = "",
    failing_test: str = "",
) -> str:
    """Hash stable failure structure while removing volatile execution identifiers."""
    payload = {
        "error_type": _normalize_failure_text(error_type).upper(),
        "message": _normalize_failure_text(message),
        "stack_frames": [_normalize_failure_text(frame) for frame in list(stack_frames)[:5]],
        "typed_failure": _normalize_failure_text(typed_failure).upper(),
        "workflow_stage": _normalize_failure_text(workflow_stage).upper(),
        "failing_test": _normalize_failure_text(failing_test),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_content_hash(values: Iterable[str]) -> str:
    canonical = json.dumps(sorted(str(value) for value in values), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_material_progress(previous: AttemptReceipt, current: AttemptReceipt) -> ProgressDecision:
    """Use deterministic state changes, not cosmetic patch churn, to recognize progress."""
    previous_stage = WORKFLOW_STAGE_ORDER.get(previous.workflow_stage.upper(), -1)
    current_stage = WORKFLOW_STAGE_ORDER.get(current.workflow_stage.upper(), -1)
    if current_stage > previous_stage:
        return ProgressDecision.PROGRESS
    if current.acceptance_pass_count > previous.acceptance_pass_count:
        return ProgressDecision.PROGRESS
    if current.first_failing_boundary and current.first_failing_boundary != previous.first_failing_boundary:
        return ProgressDecision.PROGRESS
    if current.evidence_hash and current.evidence_hash != previous.evidence_hash:
        return ProgressDecision.PROGRESS
    if current.failing_tests_hash and current.failing_tests_hash != previous.failing_tests_hash:
        return ProgressDecision.PROGRESS
    return ProgressDecision.NO_PROGRESS


def consecutive_no_progress_count(history: Sequence[AttemptReceipt]) -> int:
    """Count trailing same-fingerprint attempts with no deterministic material progress."""
    if len(history) < 2:
        return 0
    count = 0
    for previous, current in zip(reversed(history[:-1]), reversed(history[1:])):
        if current.failure_fingerprint != previous.failure_fingerprint:
            break
        if assess_material_progress(previous, current) == ProgressDecision.PROGRESS:
            break
        count += 1
    return count


def progress_aware_retry_action(
    history: Sequence[AttemptReceipt],
    *,
    no_progress_limit: int = 2,
    max_attempts: int = 5,
) -> RetryAction:
    """DLQ only after deterministic stagnation or a hard attempt ceiling."""
    if len(history) >= max_attempts:
        return RetryAction.DLQ
    if consecutive_no_progress_count(history) >= no_progress_limit:
        return RetryAction.DLQ
    return RetryAction.RETRY


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
