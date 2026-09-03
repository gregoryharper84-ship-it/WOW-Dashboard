"""Universal V17 certified numerical-computation contract.

Sport-agnostic by design. This layer never creates governed sporting probability
by itself. Exactly one controlling specialist owns each candidate and chooses a
certified numerical method appropriate to that sport/stat/event.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class V17Lane(str, Enum):
    PROP = "PROP"
    TEAM_EVENT_ML = "TEAM_EVENT_ML"


class ModelFamily(str, Enum):
    LOGISTIC_REGRESSION = "LOGISTIC_REGRESSION"
    BRADLEY_TERRY = "BRADLEY_TERRY"
    ELO_DERIVED = "ELO_DERIVED"
    SCORE_DISTRIBUTION_SIMULATION = "SCORE_DISTRIBUTION_SIMULATION"
    SPORT_SPECIFIC_EVENT_SIMULATION = "SPORT_SPECIFIC_EVENT_SIMULATION"
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
        for name, value in (
            ("raw_probability", self.raw_probability),
            ("unconditional_probability", self.unconditional_probability),
        ):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"MODEL_OUTPUT_INVALID:{name}")
        if self.verification_probability is not None and not 0.0 <= float(self.verification_probability) <= 1.0:
            raise ValueError("MODEL_OUTPUT_INVALID:verification_probability")


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
        for name, value in (
            ("calibrated_probability", self.calibrated_probability),
            ("calibrated_lower_bound", self.calibrated_lower_bound),
            ("calibrated_upper_bound", self.calibrated_upper_bound),
        ):
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"MODEL_OUTPUT_INVALID:{name}")
        if self.rank_eligible and self.calibrated_lower_bound is None:
            raise ValueError("MODEL_OUTPUT_INVALID:rank_eligible_without_lower_bound")


def verify_independent_probability(
    *, primary_probability: float, verifier_probability: float, tolerance: float
) -> tuple[VerificationStatus, float]:
    if not 0.0 <= primary_probability <= 1.0 or not 0.0 <= verifier_probability <= 1.0:
        raise ValueError("MODEL_OUTPUT_INVALID:verification_probability")
    if tolerance < 0:
        raise ValueError("MODEL_INPUTS_INSUFFICIENT:verification_tolerance")
    delta = abs(primary_probability - verifier_probability)
    status = VerificationStatus.PASS if delta <= tolerance else VerificationStatus.CONFLICT
    return status, delta


def supported_model_families(lane: V17Lane) -> tuple[ModelFamily, ...]:
    """Candidate families only; the controlling specialist selects the certified family."""
    if lane is V17Lane.TEAM_EVENT_ML:
        return (
            ModelFamily.LOGISTIC_REGRESSION,
            ModelFamily.BRADLEY_TERRY,
            ModelFamily.ELO_DERIVED,
            ModelFamily.SCORE_DISTRIBUTION_SIMULATION,
            ModelFamily.SPORT_SPECIFIC_EVENT_SIMULATION,
            ModelFamily.MIXTURE_MODEL,
            ModelFamily.MONTE_CARLO,
        )
    return (
        ModelFamily.POISSON,
        ModelFamily.NEGATIVE_BINOMIAL,
        ModelFamily.BINOMIAL,
        ModelFamily.NORMAL,
        ModelFamily.TRUNCATED_NORMAL,
        ModelFamily.LOGNORMAL,
        ModelFamily.GAMMA,
        ModelFamily.ZERO_INFLATED_COUNT,
        ModelFamily.EMPIRICAL_RESIDUAL,
        ModelFamily.MIXTURE_MODEL,
        ModelFamily.EVENT_TREE,
        ModelFamily.MONTE_CARLO,
    )


__all__ = [
    "CertifiedComputationRequest",
    "GovernedProbabilityEnvelope",
    "ModelFamily",
    "NumericalComputationResult",
    "NumericalFailure",
    "V17Lane",
    "VerificationStatus",
    "supported_model_families",
    "verify_independent_probability",
]
