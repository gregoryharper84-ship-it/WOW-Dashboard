"""Universal V17 certified numerical-computation runtime.

Sport-agnostic by design. Exactly one controlling specialist owns each candidate,
selects the certified model family, freezes inputs, and registers the numerical
adapter. This layer executes and verifies math; it never self-selects a sporting
model and never publishes governed probability by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping


class V17Lane(str, Enum):
    PROP = "PROP"
    TEAM_EVENT_ML = "TEAM_EVENT_ML"


class ModelFamily(str, Enum):
    LOGISTIC_REGRESSION = "LOGISTIC_REGRESSION"
    BRADLEY_TERRY = "BRADLEY_TERRY"
    ELO_DERIVED = "ELO_DERIVED"
    SCORE_DISTRIBUTION_SIMULATION = "SCORE_DISTRIBUTION_SIMULATION"
    SPORT_SPECIFIC_EVENT_SIMULATION = "SPORT_SPECIFIC_EVENT_SIMULATION"
    DISCRETE_PMF = "DISCRETE_PMF"
    POISSON = "POISSON"
    NEGATIVE_BINOMIAL = "NEGATIVE_BINOMIAL"
    BINOMIAL = "BINOMIAL"
    NORMAL = "NORMAL"
    TRUNCATED_NORMAL = "TRUNCATED_NORMAL"
    LOGNORMAL = "LOGNORMAL"
    GAMMA = "GAMMA"
    ZERO_INFLATED_COUNT = "ZERO_INFLATED_COUNT"
    EMPIRICAL_RESIDUAL = "EMPIRICAL_RESIDUAL"
    MIXTURE_MODEL = "MIXTURE_MODEL"
    EVENT_TREE = "EVENT_TREE"
    MONTE_CARLO = "MONTE_CARLO"


class VerificationStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PASS = "PASS"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"


class NumericalFailure(str, Enum):
    MODEL_SCORER_FAILED = "MODEL_SCORER_FAILED"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    MODEL_INPUTS_INSUFFICIENT = "MODEL_INPUTS_INSUFFICIENT"
    CERTIFIED_ADAPTER_UNAVAILABLE = "CERTIFIED_ADAPTER_UNAVAILABLE"
    COMPUTATION_VERIFICATION_FAILED = "COMPUTATION_VERIFICATION_FAILED"
    COMPUTATION_VERIFICATION_CONFLICT = "COMPUTATION_VERIFICATION_CONFLICT"


@dataclass(frozen=True)
class CertifiedComputationRequest:
    candidate_id: str
    lane: V17Lane
    sport: str
    market_or_stat: str
    controlling_specialist: str
    model_version: str
    model_family: ModelFamily
    certified_inputs: Mapping[str, Any]
    feature_vector_version: str | None = None
    simulation_count: int | None = None
    random_seed: int | None = None
    verification_required: bool = False
    verification_tolerance: float = 1e-6

    def validate(self) -> None:
        required_text = {
            "candidate_id": self.candidate_id,
            "sport": self.sport,
            "market_or_stat": self.market_or_stat,
            "controlling_specialist": self.controlling_specialist,
            "model_version": self.model_version,
        }
        missing = [name for name, value in required_text.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"MODEL_INPUTS_INSUFFICIENT:{','.join(missing)}")
        if not self.certified_inputs:
            raise ValueError("MODEL_INPUTS_INSUFFICIENT:certified_inputs")
        if self.simulation_count is not None and self.simulation_count <= 0:
            raise ValueError("MODEL_INPUTS_INSUFFICIENT:simulation_count")
        if self.verification_tolerance < 0:
            raise ValueError("MODEL_INPUTS_INSUFFICIENT:verification_tolerance")
        if self.model_family not in supported_model_families(self.lane):
            raise ValueError("MODEL_INPUTS_INSUFFICIENT:model_family_not_valid_for_lane")


@dataclass(frozen=True)
class NumericalComputationResult:
    candidate_id: str
    lane: V17Lane
    sport: str
    market_or_stat: str
    controlling_specialist: str
    model_version: str
    model_family: ModelFamily
    computation_engine: str
    computation_method: str
    raw_probability: float
    unconditional_probability: float
    computation_version: str | None = None
    feature_vector_version: str | None = None
    simulation_count: int | None = None
    random_seed: int | None = None
    convergence_status: str | None = None
    distribution_diagnostics: Mapping[str, Any] = field(default_factory=dict)
    sensitivity_summary: Mapping[str, Any] = field(default_factory=dict)
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED
    verification_method: str | None = None
    verification_probability: float | None = None
    verification_delta: float | None = None
    verification_tolerance: float | None = None

    def validate_probability_contract(self) -> None:
        for name, value in (("raw_probability", self.raw_probability), ("unconditional_probability", self.unconditional_probability)):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"MODEL_OUTPUT_INVALID:{name}")
        if self.verification_probability is not None and not 0.0 <= float(self.verification_probability) <= 1.0:
            raise ValueError("MODEL_OUTPUT_INVALID:verification_probability")


@dataclass(frozen=True)
class NumericalExecutionOutcome:
    completed: bool
    result: NumericalComputationResult | None = None
    failure: NumericalFailure | None = None
    detail: str | None = None

    def validate(self) -> None:
        if self.completed:
            if self.result is None or self.failure is not None:
                raise ValueError("MODEL_OUTPUT_INVALID:execution_outcome")
            self.result.validate_probability_contract()
        elif self.failure is None or self.result is not None:
            raise ValueError("MODEL_OUTPUT_INVALID:execution_outcome")


@dataclass(frozen=True)
class GovernedProbabilityEnvelope:
    """Numerical output plus downstream calibration/governance state."""
    numerical_result: NumericalComputationResult
    calibration_status: str
    calibrated_probability: float | None
    calibrated_lower_bound: float | None
    calibrated_upper_bound: float | None
    rank_eligible: bool
    model_qualified: bool
    market_status: str | None
    terminal_label: str
    blockers: tuple[str, ...] = ()

    def validate(self) -> None:
        self.numerical_result.validate_probability_contract()
        for name, value in (("calibrated_probability", self.calibrated_probability), ("calibrated_lower_bound", self.calibrated_lower_bound), ("calibrated_upper_bound", self.calibrated_upper_bound)):
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"MODEL_OUTPUT_INVALID:{name}")
        if self.rank_eligible and self.calibrated_lower_bound is None:
            raise ValueError("MODEL_OUTPUT_INVALID:rank_eligible_without_lower_bound")
        if self.calibrated_lower_bound is not None and self.calibrated_upper_bound is not None and self.calibrated_lower_bound > self.calibrated_upper_bound:
            raise ValueError("MODEL_OUTPUT_INVALID:calibration_interval")


PrimaryCalculator = Callable[[CertifiedComputationRequest], NumericalComputationResult]
IndependentVerifier = Callable[[CertifiedComputationRequest, NumericalComputationResult], tuple[float, str]]


@dataclass(frozen=True)
class CertifiedNumericalAdapter:
    """Registered by a controlling specialist; never auto-generated by the engine."""
    adapter_id: str
    lane: V17Lane
    sport: str
    market_or_stat: str
    controlling_specialist: str
    model_family: ModelFamily
    computation_version: str
    primary: PrimaryCalculator
    verifier: IndependentVerifier | None = None

    def key(self) -> tuple[str, str, str, str]:
        return (self.lane.value, self.sport.strip().upper(), self.market_or_stat.strip().lower(), self.controlling_specialist.strip())


class CertifiedNumericalRegistry:
    """Registry supports any sport without changing core terminal semantics."""
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str, str, str], CertifiedNumericalAdapter] = {}

    def register(self, adapter: CertifiedNumericalAdapter) -> None:
        if not adapter.adapter_id.strip() or not adapter.controlling_specialist.strip():
            raise ValueError("MODEL_INPUTS_INSUFFICIENT:adapter_identity")
        if adapter.model_family not in supported_model_families(adapter.lane):
            raise ValueError("MODEL_INPUTS_INSUFFICIENT:adapter_model_family")
        key = adapter.key()
        existing = self._adapters.get(key)
        if existing is not None and existing.adapter_id != adapter.adapter_id:
            raise ValueError("MODEL_OUTPUT_INVALID:duplicate_controlling_adapter")
        self._adapters[key] = adapter

    def resolve(self, request: CertifiedComputationRequest) -> CertifiedNumericalAdapter | None:
        sport = request.sport.strip().upper()
        market = request.market_or_stat.strip().lower()
        specialist = request.controlling_specialist.strip()
        candidates = ((request.lane.value, sport, market, specialist), (request.lane.value, sport, "*", specialist), (request.lane.value, "*", market, specialist), (request.lane.value, "*", "*", specialist))
        for key in candidates:
            adapter = self._adapters.get(key)
            if adapter is not None and adapter.model_family is request.model_family:
                return adapter
        return None

    def registered_keys(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(sorted(self._adapters))


def execute_certified_computation(request: CertifiedComputationRequest, *, registry: CertifiedNumericalRegistry) -> NumericalExecutionOutcome:
    """Execute one specialist-selected computation and optional independent verify."""
    try:
        request.validate()
    except ValueError as exc:
        return NumericalExecutionOutcome(False, failure=NumericalFailure.MODEL_INPUTS_INSUFFICIENT, detail=str(exc))
    adapter = registry.resolve(request)
    if adapter is None:
        return NumericalExecutionOutcome(False, failure=NumericalFailure.CERTIFIED_ADAPTER_UNAVAILABLE, detail="no_exact_specialist_adapter")
    try:
        result = adapter.primary(request)
        result = replace(result, computation_engine="PYTHON_PRIMARY", computation_version=adapter.computation_version, feature_vector_version=request.feature_vector_version)
        result.validate_probability_contract()
    except ValueError as exc:
        return NumericalExecutionOutcome(False, failure=NumericalFailure.MODEL_OUTPUT_INVALID, detail=str(exc))
    except Exception as exc:
        return NumericalExecutionOutcome(False, failure=NumericalFailure.MODEL_SCORER_FAILED, detail=type(exc).__name__)
    if request.verification_required:
        if adapter.verifier is None:
            return NumericalExecutionOutcome(False, failure=NumericalFailure.COMPUTATION_VERIFICATION_FAILED, detail="required_verifier_unavailable")
        try:
            verifier_probability, verifier_method = adapter.verifier(request, result)
            status, delta = verify_independent_probability(primary_probability=result.unconditional_probability, verifier_probability=float(verifier_probability), tolerance=request.verification_tolerance)
        except Exception as exc:
            return NumericalExecutionOutcome(False, failure=NumericalFailure.COMPUTATION_VERIFICATION_FAILED, detail=type(exc).__name__)
        result = replace(result, verification_status=status, verification_method=verifier_method, verification_probability=float(verifier_probability), verification_delta=delta, verification_tolerance=request.verification_tolerance)
        if status is VerificationStatus.CONFLICT:
            return NumericalExecutionOutcome(False, failure=NumericalFailure.COMPUTATION_VERIFICATION_CONFLICT, detail=f"delta={delta:.12g}")
    outcome = NumericalExecutionOutcome(True, result=result)
    outcome.validate()
    return outcome


def verify_independent_probability(*, primary_probability: float, verifier_probability: float, tolerance: float) -> tuple[VerificationStatus, float]:
    if not 0.0 <= primary_probability <= 1.0 or not 0.0 <= verifier_probability <= 1.0:
        raise ValueError("MODEL_OUTPUT_INVALID:verification_probability")
    if tolerance < 0:
        raise ValueError("MODEL_INPUTS_INSUFFICIENT:verification_tolerance")
    delta = abs(primary_probability - verifier_probability)
    return (VerificationStatus.PASS if delta <= tolerance else VerificationStatus.CONFLICT), delta


def supported_model_families(lane: V17Lane) -> tuple[ModelFamily, ...]:
    """Candidate families only; the controlling specialist selects the certified family."""
    if lane is V17Lane.TEAM_EVENT_ML:
        return (ModelFamily.LOGISTIC_REGRESSION, ModelFamily.BRADLEY_TERRY, ModelFamily.ELO_DERIVED, ModelFamily.SCORE_DISTRIBUTION_SIMULATION, ModelFamily.SPORT_SPECIFIC_EVENT_SIMULATION, ModelFamily.MIXTURE_MODEL, ModelFamily.MONTE_CARLO)
    return (ModelFamily.DISCRETE_PMF, ModelFamily.POISSON, ModelFamily.NEGATIVE_BINOMIAL, ModelFamily.BINOMIAL, ModelFamily.NORMAL, ModelFamily.TRUNCATED_NORMAL, ModelFamily.LOGNORMAL, ModelFamily.GAMMA, ModelFamily.ZERO_INFLATED_COUNT, ModelFamily.EMPIRICAL_RESIDUAL, ModelFamily.MIXTURE_MODEL, ModelFamily.EVENT_TREE, ModelFamily.MONTE_CARLO)


DEFAULT_NUMERICAL_REGISTRY = CertifiedNumericalRegistry()


__all__ = ["CertifiedComputationRequest", "CertifiedNumericalAdapter", "CertifiedNumericalRegistry", "DEFAULT_NUMERICAL_REGISTRY", "GovernedProbabilityEnvelope", "ModelFamily", "NumericalComputationResult", "NumericalExecutionOutcome", "NumericalFailure", "V17Lane", "VerificationStatus", "execute_certified_computation", "supported_model_families", "verify_independent_probability"]
