from __future__ import annotations

from hashlib import sha256
import json
from typing import Mapping

from prop_distribution_contract import RawDiscreteDistribution
from v17.distribution_protocol import (
    LineDomain,
    OutcomeDomain,
    SupportType,
    V17DistributionProtocol,
)


class RawDiscreteDistributionAdapter(V17DistributionProtocol):
    """Expose the existing governed discrete PMF through the abstract Class B protocol."""

    def __init__(
        self,
        raw: RawDiscreteDistribution,
        *,
        distribution_version: str,
        min_line: float,
        max_line: float,
        supported_steps: tuple[float, ...] = (),
    ):
        self.raw = raw
        self._distribution_version = distribution_version
        self._line_domain = LineDomain(min_line, max_line, supported_steps)

    @property
    def support_type(self) -> SupportType:
        return SupportType.DISCRETE

    @property
    def outcome_domain(self) -> OutcomeDomain:
        outcomes = tuple(self.raw.support)
        return OutcomeDomain(float(min(outcomes)), float(max(outcomes)), True)

    @property
    def line_domain(self) -> LineDomain:
        return self._line_domain

    @property
    def distribution_version(self) -> str:
        return self._distribution_version

    def evaluate_cdf(self, x: float) -> float:
        return sum(float(p) for outcome, p in self.raw.support.items() if outcome <= x)

    def evaluate_point_mass(self, x: float) -> float:
        if not float(x).is_integer():
            return 0.0
        return float(self.raw.support.get(int(x), 0.0))

    def evaluate_quantile(self, probability: float) -> float:
        return float(self.raw.quantile(probability))

    def verify_normalization(self, tolerance: float = 1e-4) -> bool:
        total = sum(float(p) for p in self.raw.support.values())
        return abs(total - 1.0) <= tolerance

    def verify_support(self) -> bool:
        return all(
            isinstance(outcome, int)
            and not isinstance(outcome, bool)
            and outcome >= 0
            for outcome in self.raw.support
        )

    def distribution_fingerprint(self) -> str:
        canonical = json.dumps(
            {
                "distribution_version": self.distribution_version,
                "support": sorted(
                    (int(outcome), float(probability))
                    for outcome, probability in self.raw.support.items()
                ),
                "model_artifact_version": self.raw.model_artifact_version,
                "training_code_sha": self.raw.training_code_sha,
                "training_dataset_hash": self.raw.training_dataset_hash,
                "feature_schema_version": self.raw.feature_schema_version,
                "feature_snapshot_hash": self.raw.feature_snapshot_hash,
                "artifact_checksum": self.raw.artifact_checksum,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(canonical).hexdigest()
