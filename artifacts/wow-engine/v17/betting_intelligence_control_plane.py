"""WOW V17 Betting Intelligence product-intelligence control-plane contracts.

This module is the Product Intelligence & Orchestration control plane inside the wider neutral WOW Ecosystem Conductor. It does not originate,
alter, blend, calibrate, certify, publish, or execute sporting probabilities.

It gives WOW one typed place to answer:
- what user/product objective is being pursued,
- which system owns the next step,
- whether every admitted work item has a terminal disposition,
- whether a capability is truly product-ready,
- whether product acceptance is independently proven, and
- whether repeated retries are making material progress.

V17_TERMINAL_REDUCER remains the sole sporting terminal authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CONTRACT_VERSION = "WOW_BETTING_INTELLIGENCE_CONTROL_PLANE_V1"
CONTROL_PLANE_SCOPE = "PRODUCT_INTELLIGENCE_ORCHESTRATION"
ECOSYSTEM_COORDINATOR = "WOW_ECOSYSTEM_CONDUCTOR"


class WorkState(str, Enum):
    ADMITTED = "ADMITTED"
    ROUTED = "ROUTED"
    IN_PROGRESS = "IN_PROGRESS"
    SCORED = "SCORED"
    BLOCKED = "BLOCKED"
    PURGED = "PURGED"
    SUPERSEDED = "SUPERSEDED"
    UNSUPPORTED = "UNSUPPORTED"
    SELECTED = "SELECTED"


TERMINAL_WORK_STATES = frozenset(
    {
        WorkState.SCORED,
        WorkState.BLOCKED,
        WorkState.PURGED,
        WorkState.SUPERSEDED,
        WorkState.UNSUPPORTED,
        WorkState.SELECTED,
    }
)


class ProductState(str, Enum):
    COMPLETE = "COMPLETE"
    WORKING_NOT_COMPLETE = "WORKING_NOT_COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SAFE_HOLD = "SAFE_HOLD"
    UNSUPPORTED = "UNSUPPORTED"


class HandoffStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class StagnationAction(str, Enum):
    RETRY_ALLOWED = "RETRY_ALLOWED"
    DIAGNOSIS_REQUIRED = "DIAGNOSIS_REQUIRED"
    ESCALATE_P0 = "ESCALATE_P0"
    REDESIGN_OR_CAPABILITY_DECISION = "REDESIGN_OR_CAPABILITY_DECISION"


@dataclass(frozen=True)
class ObjectiveContract:
    objective_id: str
    request_id: str
    requested_outcome: str
    scope: Mapping[str, Any]
    required_systems: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    freshness_required: bool = True
    completeness_required: bool = True
    current_owner: str | None = None
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY

    def validate(self) -> None:
        if not self.objective_id.strip():
            raise ValueError("objective_id is required")
        if not self.request_id.strip():
            raise ValueError("request_id is required")
        if not self.requested_outcome.strip():
            raise ValueError("requested_outcome is required")
        if not self.required_systems:
            raise ValueError("required_systems must not be empty")
        if not self.acceptance_criteria:
            raise ValueError("acceptance_criteria must not be empty")
        if self.can_execute:
            raise ValueError("can_execute must remain false")
        if self.terminal_authority != TERMINAL_AUTHORITY:
            raise ValueError("terminal authority cannot be overridden")


@dataclass(frozen=True)
class WorkItem:
    work_item_id: str
    owner: str
    state: WorkState
    blocker_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.work_item_id.strip():
            raise ValueError("work_item_id is required")
        if not self.owner.strip():
            raise ValueError("owner is required")
        if self.state == WorkState.BLOCKED and not self.blocker_codes:
            raise ValueError("BLOCKED work item requires at least one blocker code")


@dataclass(frozen=True)
class CapabilityRecord:
    capability_id: str
    product: str
    sport_or_domain: str
    market_or_route: str
    controlling_specialist: str
    model_state: str
    runtime_state: str
    discovery_state: str
    routing_state: str
    verification_state: str
    product_ready: bool
    blockers: tuple[str, ...] = ()
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY

    def validate(self) -> None:
        required = (
            self.capability_id,
            self.product,
            self.sport_or_domain,
            self.market_or_route,
            self.controlling_specialist,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("capability identity fields must be populated")
        if self.can_execute:
            raise ValueError("can_execute must remain false")
        if self.terminal_authority != TERMINAL_AUTHORITY:
            raise ValueError("terminal authority cannot be overridden")
        if self.product_ready:
            truth = {
                "model_state": self.model_state,
                "runtime_state": self.runtime_state,
                "discovery_state": self.discovery_state,
                "routing_state": self.routing_state,
                "verification_state": self.verification_state,
            }
            not_ready = {
                key: value
                for key, value in truth.items()
                if str(value).upper() not in {"READY", "PASS", "HEALTHY", "COMPLETE"}
            }
            if not_ready:
                raise ValueError(
                    "product_ready cannot be true while readiness dimensions are incomplete: "
                    + repr(not_ready)
                )
            if self.blockers:
                raise ValueError("product_ready cannot be true with blockers")


@dataclass(frozen=True)
class HandoffReceipt:
    work_item_id: str
    from_owner: str
    to_owner: str
    reason_code: str
    status: HandoffStatus
    acceptance_target: str
    evidence_refs: tuple[str, ...] = ()
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY

    def validate(self) -> None:
        if not self.work_item_id.strip():
            raise ValueError("work_item_id is required")
        if not self.from_owner.strip() or not self.to_owner.strip():
            raise ValueError("handoff owners are required")
        if self.from_owner == self.to_owner:
            raise ValueError("handoff must change owner")
        if not self.reason_code.strip():
            raise ValueError("reason_code is required")
        if not self.acceptance_target.strip():
            raise ValueError("acceptance_target is required")
        if self.can_execute:
            raise ValueError("can_execute must remain false")
        if self.terminal_authority != TERMINAL_AUTHORITY:
            raise ValueError("terminal authority cannot be overridden")


@dataclass(frozen=True)
class ProductAcceptance:
    objective_id: str
    criteria: Mapping[str, bool]
    blockers: tuple[str, ...] = ()
    failed: bool = False
    safe_hold: bool = False
    unsupported: bool = False
    independent_verification: bool = False
    reconciliation_balanced: bool = False
    can_execute: bool = False


@dataclass(frozen=True)
class ReconciliationReceipt:
    rows_in: int
    rows_terminal: int
    rows_nonterminal: int
    duplicate_ids: tuple[str, ...]
    missing_terminal_ids: tuple[str, ...]
    balanced: bool
    candidate_conservation_rate: float
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY


@dataclass(frozen=True)
class ProductMetricSnapshot:
    supported_requests: int
    completed_requests: int
    admitted_candidates: int
    terminal_candidates: int
    first_pass_requests: int
    reliable_decisions: int
    desired_decisions: int

    @property
    def product_outcome_completion_rate(self) -> float:
        return _safe_rate(self.completed_requests, self.supported_requests)

    @property
    def candidate_conservation_rate(self) -> float:
        return _safe_rate(self.terminal_candidates, self.admitted_candidates)

    @property
    def first_pass_completion_rate(self) -> float:
        return _safe_rate(self.first_pass_requests, self.supported_requests)

    @property
    def reliable_decision_availability(self) -> float:
        return _safe_rate(self.reliable_decisions, self.desired_decisions)


def _safe_rate(numerator: int, denominator: int) -> float:
    if numerator < 0 or denominator < 0:
        raise ValueError("metric counts must be non-negative")
    if numerator > denominator:
        raise ValueError("metric numerator cannot exceed denominator")
    if denominator == 0:
        return 1.0
    return numerator / denominator


def reconcile_work_items(items: Sequence[WorkItem]) -> ReconciliationReceipt:
    """Enforce work conservation: every admitted item must terminate exactly once."""
    seen: set[str] = set()
    duplicates: list[str] = []
    nonterminal: list[str] = []
    for item in items:
        item.validate()
        if item.work_item_id in seen:
            duplicates.append(item.work_item_id)
        seen.add(item.work_item_id)
        if item.state not in TERMINAL_WORK_STATES:
            nonterminal.append(item.work_item_id)

    rows_in = len(items)
    terminal = rows_in - len(nonterminal)
    balanced = not duplicates and not nonterminal
    conservation = _safe_rate(terminal, rows_in) if rows_in else 1.0
    return ReconciliationReceipt(
        rows_in=rows_in,
        rows_terminal=terminal,
        rows_nonterminal=len(nonterminal),
        duplicate_ids=tuple(sorted(set(duplicates))),
        missing_terminal_ids=tuple(nonterminal),
        balanced=balanced,
        candidate_conservation_rate=conservation,
    )


def evaluate_product_acceptance(acceptance: ProductAcceptance) -> dict[str, Any]:
    """Reduce product readiness without confusing health, merge, or runtime with usability."""
    if acceptance.can_execute:
        raise ValueError("can_execute must remain false")

    missing = tuple(sorted(key for key, passed in acceptance.criteria.items() if not passed))

    if acceptance.safe_hold:
        state = ProductState.SAFE_HOLD
    elif acceptance.unsupported:
        state = ProductState.UNSUPPORTED
    elif acceptance.failed:
        state = ProductState.FAILED
    elif acceptance.blockers:
        state = ProductState.BLOCKED
    elif (
        not missing
        and acceptance.independent_verification
        and acceptance.reconciliation_balanced
    ):
        state = ProductState.COMPLETE
    else:
        state = ProductState.WORKING_NOT_COMPLETE

    return {
        "contract_version": CONTRACT_VERSION,
        "objective_id": acceptance.objective_id,
        "product_state": state.value,
        "missing_acceptance_criteria": list(missing),
        "blockers": list(acceptance.blockers),
        "independent_verification": acceptance.independent_verification,
        "reconciliation_balanced": acceptance.reconciliation_balanced,
        "product_ready": state == ProductState.COMPLETE,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def stagnation_action(repeated_same_blocker_count: int) -> StagnationAction:
    """Product-level escalation rule; retries are not counted as progress."""
    if repeated_same_blocker_count < 0:
        raise ValueError("repeated_same_blocker_count must be non-negative")
    if repeated_same_blocker_count <= 1:
        return StagnationAction.RETRY_ALLOWED
    if repeated_same_blocker_count == 2:
        return StagnationAction.DIAGNOSIS_REQUIRED
    if repeated_same_blocker_count == 3:
        return StagnationAction.ESCALATE_P0
    return StagnationAction.REDESIGN_OR_CAPABILITY_DECISION


def build_control_plane_snapshot(
    *,
    objective: ObjectiveContract,
    capabilities: Iterable[CapabilityRecord],
    work_items: Sequence[WorkItem],
    handoffs: Sequence[HandoffReceipt],
    acceptance: ProductAcceptance,
) -> dict[str, Any]:
    """Create one auditable product-truth snapshot without creating model authority."""
    objective.validate()
    capability_rows = []
    for capability in capabilities:
        capability.validate()
        capability_rows.append(asdict(capability))
    handoff_rows = []
    for handoff in handoffs:
        handoff.validate()
        handoff_rows.append(asdict(handoff))
    reconciliation = reconcile_work_items(work_items)
    acceptance_result = evaluate_product_acceptance(
        ProductAcceptance(
            objective_id=acceptance.objective_id,
            criteria=acceptance.criteria,
            blockers=acceptance.blockers,
            failed=acceptance.failed,
            safe_hold=acceptance.safe_hold,
            unsupported=acceptance.unsupported,
            independent_verification=acceptance.independent_verification,
            reconciliation_balanced=reconciliation.balanced,
            can_execute=False,
        )
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "objective": asdict(objective),
        "capabilities": capability_rows,
        "work_items": [asdict(item) for item in work_items],
        "handoffs": handoff_rows,
        "reconciliation": asdict(reconciliation),
        "acceptance": acceptance_result,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def _runtime_work_state(candidate: Mapping[str, Any]) -> WorkState:
    """Project an Agent Runtime candidate into product-orchestration state."""
    terminal = str(
        candidate.get("terminal_label")
        or candidate.get("terminal_ceiling")
        or ""
    ).upper()
    blockers = tuple(str(code) for code in (candidate.get("blockers") or []))
    if not terminal:
        return WorkState.IN_PROGRESS
    if terminal == "FINAL_APPROVED":
        return WorkState.SCORED
    if "PURGE" in terminal:
        return WorkState.PURGED
    if terminal in {"MODEL_UNAVAILABLE", "NO_SPECIALIST_COVERAGE"}:
        return WorkState.UNSUPPORTED
    if blockers or terminal:
        return WorkState.BLOCKED
    return WorkState.BLOCKED


def build_agent_runtime_product_truth(
    run: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    independent_verification: bool = False,
) -> dict[str, Any]:
    """Project the durable Agent Runtime ledger into Betting Intelligence truth.

    This is intentionally a projection, not a second state machine. The Agent
    Runtime remains the source of run/candidate truth; this function makes that
    truth legible at the product layer without changing any sporting result.
    """
    run_id = str(run.get("run_id") or "")
    if not run_id:
        raise ValueError("run_id is required")

    request_payload = run.get("request_payload")
    request_payload = dict(request_payload) if isinstance(request_payload, Mapping) else {}
    run_status = str(run.get("status") or "")
    run_stage = str(run.get("stage") or "")
    run_terminal = run_status in {"COMPLETED", "COMPLETED_WITH_BLOCKERS", "FAILED", "CANCELED"}

    objective = ObjectiveContract(
        objective_id=f"run:{run_id}",
        request_id=str(request_payload.get("request_id") or run_id),
        requested_outcome=str(
            request_payload.get("requested_outcome")
            or request_payload.get("run_type")
            or run.get("run_type")
            or "GOVERNED_BETTING_INTELLIGENCE_RUN"
        ),
        scope={
            "run_type": run.get("run_type"),
            "as_of": run.get("requested_as_of"),
            "user_timezone": run.get("user_timezone"),
            "lanes": list(request_payload.get("lanes") or []),
            "sports": list(request_payload.get("sports") or []),
        },
        required_systems=(
            "WOW_BETTING_INTELLIGENCE",
            "GOVERNED_SPECIALIST",
            "V17_TERMINAL_REDUCER",
        ),
        acceptance_criteria=(
            "run_terminal",
            "all_candidates_terminal",
            "candidate_reconciliation_balanced",
            "independent_verification",
        ),
        current_owner="WOW_BETTING_INTELLIGENCE",
    )

    work_items: list[WorkItem] = []
    aggregate_blockers: list[str] = []
    for candidate in candidates:
        candidate_id = str(
            candidate.get("candidate_id")
            or candidate.get("canonical_key")
            or ""
        )
        if not candidate_id:
            raise ValueError("candidate identity is required")
        state = _runtime_work_state(candidate)
        blockers = tuple(str(code) for code in (candidate.get("blockers") or []))
        if state == WorkState.BLOCKED and not blockers:
            blockers = ("TERMINAL_DISPOSITION_WITHOUT_TYPED_BLOCKER",)
        aggregate_blockers.extend(blockers)
        work_items.append(
            WorkItem(
                work_item_id=candidate_id,
                owner=str(
                    candidate.get("controlling_worker_id")
                    or "GOVERNED_SPECIALIST"
                ),
                state=state,
                blocker_codes=blockers,
            )
        )

    reconciliation = reconcile_work_items(work_items)
    ledger_balanced = str(run.get("reconciliation_status") or "").upper() == "BALANCED"
    # For an empty, terminal run the runtime reconciliation has already proven
    # a zero-row universe. For non-empty runs both ledgers must agree.
    candidate_reconciliation_balanced = bool(
        run_terminal
        and reconciliation.balanced
        and (ledger_balanced or not candidates)
    )
    all_candidates_terminal = reconciliation.rows_nonterminal == 0
    run_outcome_complete = bool(
        run_status in {"COMPLETED", "COMPLETED_WITH_BLOCKERS"}
        and all_candidates_terminal
        and candidate_reconciliation_balanced
    )

    acceptance = ProductAcceptance(
        objective_id=objective.objective_id,
        criteria={
            "run_terminal": run_terminal,
            "all_candidates_terminal": all_candidates_terminal,
            "candidate_reconciliation_balanced": candidate_reconciliation_balanced,
            "independent_verification": independent_verification,
        },
        blockers=tuple(sorted(set(aggregate_blockers))),
        failed=run_status == "FAILED",
        unsupported=bool(
            candidates
            and all(item.state == WorkState.UNSUPPORTED for item in work_items)
        ),
        independent_verification=independent_verification,
        reconciliation_balanced=candidate_reconciliation_balanced,
    )
    acceptance_result = evaluate_product_acceptance(acceptance)

    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "runtime_projection": {
            "run_status": run_status,
            "run_stage": run_stage,
            "run_terminal": run_terminal,
            "run_outcome_complete": run_outcome_complete,
            "rows_in": len(candidates),
            "rows_terminal": reconciliation.rows_terminal,
            "rows_pending": reconciliation.rows_nonterminal,
            "candidate_conservation_rate": reconciliation.candidate_conservation_rate,
            "agent_runtime_reconciliation_status": run.get("reconciliation_status"),
        },
        "objective": asdict(objective),
        "work_items": [asdict(item) for item in work_items],
        "reconciliation": asdict(reconciliation),
        "acceptance": acceptance_result,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


__all__ = [
    "CAN_EXECUTE",
    "CONTRACT_VERSION",
    "CONTROL_PLANE_SCOPE",
    "ECOSYSTEM_COORDINATOR",
    "TERMINAL_AUTHORITY",
    "CapabilityRecord",
    "HandoffReceipt",
    "HandoffStatus",
    "ObjectiveContract",
    "ProductAcceptance",
    "ProductMetricSnapshot",
    "ProductState",
    "ReconciliationReceipt",
    "StagnationAction",
    "TERMINAL_WORK_STATES",
    "WorkItem",
    "WorkState",
    "build_agent_runtime_product_truth",
    "build_control_plane_snapshot",
    "evaluate_product_acceptance",
    "reconcile_work_items",
    "stagnation_action",
]
