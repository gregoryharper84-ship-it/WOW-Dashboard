from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Mapping, Sequence


CAN_EXECUTE = False
NORMALIZATION_TOLERANCE = 1e-4


class DistributionProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SupportType(str, Enum):
    DISCRETE = "DISCRETE"
    CONTINUOUS = "CONTINUOUS"
    MIXED = "MIXED"


@dataclass(frozen=True)
class OutcomeDomain:
    min_outcome: float
    max_outcome: float | None
    integer_valued: bool


@dataclass(frozen=True)
class LineDomain:
    min_line: float
    max_line: float
    supported_steps: tuple[float, ...] = ()

    def contains(self, line: float) -> bool:
        value = float(line)
        if not isfinite(value) or not self.min_line <= value <= self.max_line:
            return False
        if not self.supported_steps:
            return True
        base = self.min_line
        return any(
            abs(((value - base) / step) - round((value - base) / step)) <= 1e-9
            for step in self.supported_steps
            if step > 0
        )


class V17DistributionProtocol(ABC):
    """Class B mathematical interface implemented by Class C specialists."""

    @property
    @abstractmethod
    def support_type(self) -> SupportType:
        raise NotImplementedError

    @property
    @abstractmethod
    def outcome_domain(self) -> OutcomeDomain:
        raise NotImplementedError

    @property
    @abstractmethod
    def line_domain(self) -> LineDomain:
        raise NotImplementedError

    @property
    @abstractmethod
    def distribution_version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def evaluate_cdf(self, x: float) -> float:
        """Return P(X <= x)."""
        raise NotImplementedError

    @abstractmethod
    def evaluate_point_mass(self, x: float) -> float:
        """Return exact P(X == x); continuous distributions return 0."""
        raise NotImplementedError

    @abstractmethod
    def evaluate_quantile(self, probability: float) -> float:
        raise NotImplementedError

    @abstractmethod
    def verify_normalization(self, tolerance: float = NORMALIZATION_TOLERANCE) -> bool:
        raise NotImplementedError

    @abstractmethod
    def verify_support(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def distribution_fingerprint(self) -> str:
        """Deterministic fingerprint of the published settlement distribution."""
        raise NotImplementedError


@dataclass(frozen=True)
class SettlementProbabilities:
    line: float
    probability_over: float
    probability_under: float
    probability_push: float


def verify_distribution_protocol(
    distribution: V17DistributionProtocol,
    line: float,
) -> Mapping[str, Any]:
    if not distribution.distribution_version.strip():
        raise DistributionProtocolError(
            "DISTRIBUTION_VERSION_MISSING",
            "distribution_version is required",
        )
    if not distribution.verify_support():
        raise DistributionProtocolError(
            "DISTRIBUTION_SUPPORT_INVALID",
            "published distribution violates declared outcome support",
        )
    if not distribution.verify_normalization():
        raise DistributionProtocolError(
            "DISTRIBUTION_NOT_NORMALIZED",
            "published distribution failed normalization verification",
        )
    if not distribution.line_domain.contains(line):
        raise DistributionProtocolError(
            "LINE_OUT_OF_SPECIALIST_DOMAIN",
            f"line {line} is outside the certified line domain",
        )
    fingerprint = distribution.distribution_fingerprint()
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise DistributionProtocolError(
            "DISTRIBUTION_FINGERPRINT_INVALID",
            "distribution fingerprint must be a SHA-256 hex digest",
        )
    try:
        int(fingerprint, 16)
    except ValueError as exc:
        raise DistributionProtocolError(
            "DISTRIBUTION_FINGERPRINT_INVALID",
            "distribution fingerprint must be hexadecimal",
        ) from exc
    return {
        "implements_distribution_protocol": True,
        "support_type": distribution.support_type.value,
        "normalization_satisfied": True,
        "support_satisfied": True,
        "line_within_domain": True,
        "distribution_version": distribution.distribution_version,
        "distribution_fingerprint": fingerprint,
        "can_execute": CAN_EXECUTE,
    }


def evaluate_market_line(
    distribution: V17DistributionProtocol,
    line: float,
) -> SettlementProbabilities:
    """Class B exact-line evaluator shared across all specialist implementations."""
    verify_distribution_protocol(distribution, line)

    cdf_at_line = float(distribution.evaluate_cdf(float(line)))
    push = float(distribution.evaluate_point_mass(float(line)))
    for name, value in (("cdf", cdf_at_line), ("push", push)):
        if not isfinite(value) or not 0.0 <= value <= 1.0:
            raise DistributionProtocolError(
                "DISTRIBUTION_EVALUATION_INVALID",
                f"{name} must be finite and within [0, 1]",
            )

    under = cdf_at_line - push
    over = 1.0 - cdf_at_line
    if min(under, over, push) < -NORMALIZATION_TOLERANCE:
        raise DistributionProtocolError(
            "DISTRIBUTION_EVALUATION_INVALID",
            "CDF/point-mass combination produced negative settlement probability",
        )

    under = max(0.0, under)
    over = max(0.0, over)
    total = under + over + push
    if abs(total - 1.0) > NORMALIZATION_TOLERANCE:
        raise DistributionProtocolError(
            "SETTLEMENT_PROBABILITIES_NOT_NORMALIZED",
            f"OVER/UNDER/PUSH sum is {total!r}",
        )
    return SettlementProbabilities(float(line), over, under, push)


def compute_dependency_graph_hash(dependencies: Mapping[str, str]) -> str:
    """Canonical hash binding every upstream artifact/version used by a compound specialist."""
    if not dependencies or any(not str(k).strip() or not str(v).strip() for k, v in dependencies.items()):
        raise DistributionProtocolError(
            "DEPENDENCY_GRAPH_INVALID",
            "dependency graph requires non-empty names and artifact/version identifiers",
        )
    canonical = json.dumps(
        {str(k): str(v) for k, v in dependencies.items()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def verify_dependency_artifact(
    *,
    dependencies: Mapping[str, str],
    expected_graph_hash: str,
    dependency_artifact_sha: str,
) -> Mapping[str, Any]:
    graph_hash = compute_dependency_graph_hash(dependencies)
    if graph_hash != expected_graph_hash:
        raise DistributionProtocolError(
            "DEPENDENCY_ARTIFACT_MISMATCH",
            "compound-specialist dependency graph does not match its certified binding",
        )
    if len(dependency_artifact_sha) != 64:
        raise DistributionProtocolError(
            "DEPENDENCY_ARTIFACT_MISSING",
            "dependency artifact SHA-256 is missing or malformed",
        )
    try:
        int(dependency_artifact_sha, 16)
    except ValueError as exc:
        raise DistributionProtocolError(
            "DEPENDENCY_ARTIFACT_MISSING",
            "dependency artifact SHA-256 is missing or malformed",
        ) from exc
    return {
        "dependency_graph_bound": True,
        "dependency_graph_hash": graph_hash,
        "dependency_artifact_sha": dependency_artifact_sha,
        "can_execute": CAN_EXECUTE,
    }
