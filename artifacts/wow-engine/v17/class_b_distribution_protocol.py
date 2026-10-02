"""Class B structural distribution governance for WOW V17.

This module standardizes probability-specialist capabilities without selecting a
statistical family. It is infrastructure only: it never fits, calibrates,
promotes, publishes, or executes a sporting model.
"""
from __future__ import annotations

import hashlib
import hmac
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, NamedTuple, Protocol, runtime_checkable


class ClassBGovernanceError(Exception):
    """Base class for typed Class B governance failures."""


class SpecialistNotQualifiedError(ClassBGovernanceError):
    pass


class DomainBoundsExceededError(ClassBGovernanceError):
    pass


class UnsupportedLineStepError(ClassBGovernanceError):
    pass


class UnnormalizedDistributionError(ClassBGovernanceError):
    pass


class TriadDigestMismatchError(ClassBGovernanceError):
    pass


class MissingTriadBindingError(ClassBGovernanceError):
    pass


class InvalidPushMassError(ClassBGovernanceError):
    pass


class ExposureModelUnqualifiedError(ClassBGovernanceError):
    pass


class DependencyArtifactMissingError(ClassBGovernanceError):
    pass


class DistributionEvaluationError(ClassBGovernanceError):
    pass


class SupportType(str, Enum):
    DISCRETE = "DISCRETE"
    CONTINUOUS = "CONTINUOUS"
    MIXED = "MIXED"


class LineDomain(NamedTuple):
    min_line: float
    max_line: float
    supported_step: float

    def validate_line(self, line: float) -> None:
        if not all(math.isfinite(value) for value in (line, self.min_line, self.max_line, self.supported_step)):
            raise UnsupportedLineStepError("Line-domain values must be finite.")
        if self.supported_step <= 0:
            raise UnsupportedLineStepError("supported_step must be positive.")
        if self.min_line > self.max_line:
            raise DomainBoundsExceededError("min_line must not exceed max_line.")
        if not self.min_line <= line <= self.max_line:
            raise DomainBoundsExceededError(
                f"Requested line {line} falls outside [{self.min_line}, {self.max_line}]."
            )
        steps = (line - self.min_line) / self.supported_step
        if not math.isclose(steps, round(steps), rel_tol=0.0, abs_tol=1e-9):
            raise UnsupportedLineStepError(
                f"Line {line} violates step granularity {self.supported_step}."
            )


@dataclass(frozen=True)
class TriadBinding:
    model_artifact_sha: str
    feature_contract_version: str
    calibration_artifact_id: str
    triad_digest_sha256: str

    def verify_integrity(self) -> bool:
        """Verify deterministic identity binding.

        This is deliberately named a digest, not an HMAC/signature: no secret key
        is involved. Authenticity must remain the responsibility of the existing
        governed artifact/provenance layer.
        """
        values = (
            self.model_artifact_sha,
            self.feature_contract_version,
            self.calibration_artifact_id,
            self.triad_digest_sha256,
        )
        if not all(isinstance(value, str) and value for value in values):
            return False
        raw = f"{self.model_artifact_sha}:{self.feature_contract_version}:{self.calibration_artifact_id}"
        computed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return hmac.compare_digest(computed, self.triad_digest_sha256)


@dataclass(frozen=True)
class VerificationReceipt:
    sport: str
    market_type: str
    stat_type: str
    specialist_id: str
    line_requested: float
    support_type: SupportType
    model_qualified: bool
    can_execute: bool
    verification_matrix: Mapping[str, Any]
    typed_gate_reason: str


@runtime_checkable
class V17DistributionProtocol(Protocol):
    @property
    def specialist_id(self) -> str: ...

    @property
    def support_type(self) -> SupportType: ...

    @property
    def domain_bounds(self) -> LineDomain: ...

    @property
    def triad_binding(self) -> TriadBinding: ...

    def evaluate_cdf(self, line: float) -> float: ...

    def evaluate_push_mass(self, line: float) -> float: ...

    def verify_normalization(self, tolerance: float = 1e-4) -> bool: ...


class ClassBGateValidator:
    """Fail-closed structural gate subordinate to V17_TERMINAL_REDUCER."""

    def __init__(self, normalization_tolerance: float = 1e-4):
        if not math.isfinite(normalization_tolerance) or normalization_tolerance <= 0:
            raise ValueError("normalization_tolerance must be finite and positive")
        self.tolerance = normalization_tolerance

    def validate_and_evaluate(
        self,
        specialist: V17DistributionProtocol,
        *,
        sport: str,
        market_type: str,
        stat_type: str,
        line: float,
        is_combo_prop: bool = False,
        has_certified_dependency: bool = False,
        requires_exposure_model: bool = False,
        has_certified_exposure: bool = False,
    ) -> VerificationReceipt:
        matrix: dict[str, Any] = {}
        try:
            if not isinstance(specialist, V17DistributionProtocol):
                raise SpecialistNotQualifiedError(
                    f"{type(specialist).__name__} fails V17DistributionProtocol."
                )
            matrix["protocol_implemented"] = True

            triad = specialist.triad_binding
            if not isinstance(triad, TriadBinding):
                raise MissingTriadBindingError("Triad binding payload is missing or malformed.")
            if not triad.verify_integrity():
                raise TriadDigestMismatchError("SHA256 triad identity digest mismatch.")
            matrix["triad_digest_verified"] = True

            specialist.domain_bounds.validate_line(line)
            matrix["domain_bounds_valid"] = True

            if not specialist.verify_normalization(self.tolerance):
                raise UnnormalizedDistributionError(
                    f"Specialist {specialist.specialist_id} probability mass is not normalized."
                )
            matrix["normalization_valid"] = True

            support_type = specialist.support_type
            if support_type in (SupportType.DISCRETE, SupportType.MIXED):
                push_mass = specialist.evaluate_push_mass(line)
                if not isinstance(push_mass, (int, float)) or not math.isfinite(push_mass) or not 0.0 <= push_mass <= 1.0:
                    raise InvalidPushMassError(f"Invalid push mass {push_mass!r} for line {line}.")
                matrix["push_mass_evaluated"] = True

            if requires_exposure_model and not has_certified_exposure:
                raise ExposureModelUnqualifiedError(
                    "Missing required certified playing-time/snap exposure model."
                )
            matrix["exposure_model_certified"] = True if requires_exposure_model else "N/A"

            if is_combo_prop and not has_certified_dependency:
                raise DependencyArtifactMissingError(
                    "Combo prop requested without a certified joint-dependency artifact."
                )
            matrix["dependency_artifact_certified"] = True if is_combo_prop else "N/A"

            cdf_value = specialist.evaluate_cdf(line)
            if not isinstance(cdf_value, (int, float)) or not math.isfinite(cdf_value) or not 0.0 <= cdf_value <= 1.0:
                raise DistributionEvaluationError(f"CDF value {cdf_value!r} outside [0, 1].")
            matrix["cdf_evaluation_valid"] = True

            return VerificationReceipt(
                sport=sport,
                market_type=market_type,
                stat_type=stat_type,
                specialist_id=specialist.specialist_id,
                line_requested=line,
                support_type=support_type,
                model_qualified=True,
                can_execute=False,
                verification_matrix=matrix,
                typed_gate_reason="QUALIFIED_FOR_V17_TERMINAL_REDUCER",
            )
        except ClassBGovernanceError as err:
            return VerificationReceipt(
                sport=sport,
                market_type=market_type,
                stat_type=stat_type,
                specialist_id=getattr(specialist, "specialist_id", "UNREGISTERED"),
                line_requested=line,
                support_type=getattr(specialist, "support_type", SupportType.DISCRETE),
                model_qualified=False,
                can_execute=False,
                verification_matrix=matrix,
                typed_gate_reason=f"{type(err).__name__}: {err}",
            )
        except Exception as err:
            # Unexpected specialist/runtime defects remain distinguishable; never
            # collapse them into MODEL_UNAVAILABLE and never authorize execution.
            return VerificationReceipt(
                sport=sport,
                market_type=market_type,
                stat_type=stat_type,
                specialist_id="UNRESOLVED",
                line_requested=line,
                support_type=SupportType.DISCRETE,
                model_qualified=False,
                can_execute=False,
                verification_matrix=matrix,
                typed_gate_reason=f"DistributionEvaluationError: {type(err).__name__}: {err}",
            )


__all__ = [
    "ClassBGateValidator",
    "ClassBGovernanceError",
    "DependencyArtifactMissingError",
    "DistributionEvaluationError",
    "DomainBoundsExceededError",
    "ExposureModelUnqualifiedError",
    "InvalidPushMassError",
    "LineDomain",
    "MissingTriadBindingError",
    "SpecialistNotQualifiedError",
    "SupportType",
    "TriadBinding",
    "TriadDigestMismatchError",
    "UnnormalizedDistributionError",
    "UnsupportedLineStepError",
    "V17DistributionProtocol",
    "VerificationReceipt",
]
