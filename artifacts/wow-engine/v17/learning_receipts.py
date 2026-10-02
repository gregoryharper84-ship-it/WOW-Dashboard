"""Typed LearningReceipt contracts for WOW V17 out-of-band learning.

Execution and reflection remain decoupled. Receipts may inform future agent hydration
or challenger research, but they cannot change sporting probabilities, terminal
authority, or execution permissions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

RECEIPT_TYPES = {"ENGINEERING", "MODEL"}
VALIDITY_STATES = {"ACTIVE", "SUSPECT_VERSION_DRIFT", "STALE_HISTORICAL", "SUPERSEDED"}
HYDRATION_EXCLUDED_STATES = {"STALE_HISTORICAL", "SUPERSEDED"}

TERMINAL_ENGINEERING_STATES = {
    "FIXED_AND_VERIFIED",
    "VERIFIED_CLOSED",
    "PR_CREATED",
    "EXPERIMENT_CREATED",
    "DUPLICATE",
    "NOT_REPRODUCIBLE",
    "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _norm_list(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Iterable):
        return ()
    normalized = {_norm(value) for value in values if _norm(value)}
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class ReceiptSource:
    ticket_id: str
    terminal_state: str
    repository: str
    commit_sha: str | None = None
    run_id: str | None = None
    agent_role: str | None = None


@dataclass(frozen=True)
class Applicability:
    subsystem: str
    error_fingerprint: str | None = None
    sport_code: str | None = None
    component_paths: tuple[str, ...] = ()
    dependency_versions: tuple[tuple[str, str], ...] = ()
    target_code_hash: str | None = None
    ast_fingerprint: str | None = None


@dataclass(frozen=True)
class ReceiptValidity:
    status: str = "ACTIVE"
    superseded_by: str | None = None
    invalidation_reason: str | None = None


@dataclass(frozen=True)
class DiscreditedApproach:
    strategy: str
    result: str
    reason: str
    patch_sha: str | None = None
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EngineeringLearningPayload:
    observed_failure: str
    expected_behavior: str
    root_cause: str
    corrective_change: str | None
    reproduction: tuple[str, ...]
    validation: tuple[str, ...]
    regression_protection: tuple[str, ...]
    failure_fingerprints: tuple[str, ...] = ()
    discredited_approaches: tuple[DiscreditedApproach, ...] = ()
    architectural_lessons: tuple[str, ...] = ()
    related_components: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelLearningPayload:
    sport: str
    controlling_specialist: str
    hypothesis: str
    cohort_definition: str
    sample_size: int
    effect_size: float | None
    uncertainty: Mapping[str, Any]
    baseline_metrics: Mapping[str, Any]
    observed_metrics: Mapping[str, Any]
    calibration_analysis: Mapping[str, Any]
    counterexamples: tuple[str, ...] = ()
    challenger_id: str | None = None
    replay_results: Mapping[str, Any] = field(default_factory=dict)
    holdout_results: Mapping[str, Any] = field(default_factory=dict)
    forward_validation: Mapping[str, Any] = field(default_factory=dict)
    regression_results: Mapping[str, Any] = field(default_factory=dict)
    protected_strengths: tuple[str, ...] = ()
    governance_disposition: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningReceipt:
    receipt_id: str
    receipt_type: str
    schema_version: str
    created_at: str
    source: ReceiptSource
    applicability: Applicability
    validity: ReceiptValidity
    evidence_refs: tuple[str, ...]
    payload: EngineeringLearningPayload | ModelLearningPayload
    can_execute: bool = CAN_EXECUTE
    terminal_authority: str = TERMINAL_AUTHORITY

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChallengerGate:
    min_sample: int
    min_effect_size: float
    require_uncertainty_bound: bool = True
    require_repeatability: bool = True
    require_multiple_test_adjustment: bool = True
    require_temporal_stability: bool = True


def validate_receipt(receipt: LearningReceipt) -> list[str]:
    errors: list[str] = []
    receipt_type = _norm(receipt.receipt_type)
    if receipt_type not in RECEIPT_TYPES:
        errors.append("invalid receipt_type")
    if receipt.can_execute is not False:
        errors.append("receipt must preserve can_execute=false")
    if receipt.terminal_authority != TERMINAL_AUTHORITY:
        errors.append("receipt must preserve V17_TERMINAL_REDUCER")
    if not receipt.receipt_id.strip():
        errors.append("receipt_id required")
    if not receipt.schema_version.strip():
        errors.append("schema_version required")
    if not receipt.created_at.strip():
        errors.append("created_at required")
    if not receipt.source.ticket_id.strip():
        errors.append("source.ticket_id required")
    if not receipt.source.repository.strip():
        errors.append("source.repository required")
    if _norm(receipt.validity.status) not in VALIDITY_STATES:
        errors.append("invalid validity.status")
    if not receipt.applicability.subsystem.strip():
        errors.append("applicability.subsystem required")

    if receipt_type == "ENGINEERING":
        if not isinstance(receipt.payload, EngineeringLearningPayload):
            errors.append("ENGINEERING receipt requires EngineeringLearningPayload")
        if _norm(receipt.source.terminal_state) not in TERMINAL_ENGINEERING_STATES:
            errors.append("engineering receipt requires governed terminal state")
    elif receipt_type == "MODEL":
        if not isinstance(receipt.payload, ModelLearningPayload):
            errors.append("MODEL receipt requires ModelLearningPayload")
        elif receipt.payload.sample_size < 1:
            errors.append("model receipt sample_size must be positive")
        if receipt.applicability.sport_code is None:
            errors.append("model receipt requires sport_code")
        disposition = (
            receipt.payload.governance_disposition
            if isinstance(receipt.payload, ModelLearningPayload)
            else {}
        )
        if disposition.get("production_behavior_changed") is not False:
            errors.append("model receipt must set production_behavior_changed=false")
        if disposition.get("can_execute") is not False:
            errors.append("model receipt governance must set can_execute=false")
    return errors


def mark_validity(
    receipt: LearningReceipt,
    *,
    current_target_code_hash: str | None = None,
    current_ast_fingerprint: str | None = None,
    current_dependency_versions: Mapping[str, str] | None = None,
) -> ReceiptValidity:
    """Evaluate code/dependency drift without deleting historical evidence."""
    if _norm(receipt.validity.status) == "SUPERSEDED":
        return receipt.validity

    app = receipt.applicability
    if app.target_code_hash and current_target_code_hash and app.target_code_hash != current_target_code_hash:
        return ReceiptValidity(
            status="SUSPECT_VERSION_DRIFT",
            invalidation_reason="target_code_hash changed",
        )
    if app.ast_fingerprint and current_ast_fingerprint and app.ast_fingerprint != current_ast_fingerprint:
        return ReceiptValidity(
            status="STALE_HISTORICAL",
            invalidation_reason="primary AST fingerprint changed",
        )

    expected = dict(app.dependency_versions)
    current = dict(current_dependency_versions or {})
    for name, version in expected.items():
        if name in current and current[name] != version:
            return ReceiptValidity(
                status="STALE_HISTORICAL",
                invalidation_reason=f"dependency version changed: {name}",
            )
    return ReceiptValidity(status="ACTIVE")


def _compatible_metadata(target: Mapping[str, Any], receipt: LearningReceipt) -> bool:
    if _norm(receipt.validity.status) in HYDRATION_EXCLUDED_STATES:
        return False
    app = receipt.applicability

    target_subsystem = _norm(target.get("subsystem"))
    if not target_subsystem or _norm(app.subsystem) != target_subsystem:
        return False

    target_fp = _norm(target.get("error_fingerprint"))
    receipt_fp = _norm(app.error_fingerprint)
    if target_fp and receipt_fp and target_fp != receipt_fp:
        return False
    if target_fp and not receipt_fp:
        return False

    target_sport = _norm(target.get("sport_code"))
    receipt_sport = _norm(app.sport_code)
    if target_sport and receipt_sport and target_sport != receipt_sport:
        return False
    if target_sport and not receipt_sport:
        return False

    target_paths = set(_norm_list(target.get("component_paths")))
    receipt_paths = set(_norm_list(app.component_paths))
    if target_paths and receipt_paths and not (target_paths & receipt_paths):
        return False

    return True


def hydrate_receipts(
    target: Mapping[str, Any],
    receipts: Sequence[LearningReceipt],
    *,
    top_k: int = 5,
) -> list[LearningReceipt]:
    """Metadata gates eligibility; deterministic ranking happens only afterward.

    Semantic/vector ranking may be layered on top of this function, but it must not
    re-introduce receipts rejected by these hard filters.
    """
    if top_k < 1:
        return []

    eligible = [receipt for receipt in receipts if _compatible_metadata(target, receipt)]

    def rank(receipt: LearningReceipt) -> tuple[int, int, str]:
        exact_fp = int(
            bool(_norm(target.get("error_fingerprint")))
            and _norm(receipt.applicability.error_fingerprint) == _norm(target.get("error_fingerprint"))
        )
        exact_sport = int(
            bool(_norm(target.get("sport_code")))
            and _norm(receipt.applicability.sport_code) == _norm(target.get("sport_code"))
        )
        return (-exact_fp, -exact_sport, receipt.receipt_id)

    return sorted(eligible, key=rank)[:top_k]


def challenger_gate_reasons(
    payload: ModelLearningPayload,
    gate: ChallengerGate,
    *,
    uncertainty_bound_satisfied: bool,
    repeatability_satisfied: bool,
    multiple_test_adjustment_satisfied: bool,
    temporal_stability_satisfied: bool,
) -> list[str]:
    """Return explicit reasons a model-learning hypothesis is not challenger eligible.

    The threshold contract is supplied by the governed sport/market policy. This
    intentionally avoids embedding a universal N or p-value rule in shared code.
    """
    reasons: list[str] = []
    if payload.sample_size < gate.min_sample:
        reasons.append("MIN_SAMPLE")
    if payload.effect_size is None or abs(payload.effect_size) < gate.min_effect_size:
        reasons.append("MIN_EFFECT_SIZE")
    if gate.require_uncertainty_bound and not uncertainty_bound_satisfied:
        reasons.append("UNCERTAINTY_BOUND")
    if gate.require_repeatability and not repeatability_satisfied:
        reasons.append("REPEATABILITY")
    if gate.require_multiple_test_adjustment and not multiple_test_adjustment_satisfied:
        reasons.append("MULTIPLE_TEST_ADJUSTMENT")
    if gate.require_temporal_stability and not temporal_stability_satisfied:
        reasons.append("TEMPORAL_STABILITY")
    return reasons


__all__ = [
    "Applicability",
    "CAN_EXECUTE",
    "ChallengerGate",
    "DiscreditedApproach",
    "EngineeringLearningPayload",
    "HYDRATION_EXCLUDED_STATES",
    "LearningReceipt",
    "ModelLearningPayload",
    "ReceiptSource",
    "ReceiptValidity",
    "TERMINAL_AUTHORITY",
    "challenger_gate_reasons",
    "hydrate_receipts",
    "mark_validity",
    "validate_receipt",
]
