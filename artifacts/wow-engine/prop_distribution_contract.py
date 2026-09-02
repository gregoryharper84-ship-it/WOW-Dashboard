"""Governed, direction-free player-prop distribution contract.

This module defines the boundary implemented by a fitted prop provider.  It does
not contain a trained model, calibrator, publication decision, market edge, or
execution path.  Provider output is a raw discrete probability mass function;
line probabilities are derived downstream.

Security/governance invariants:
* callers cannot select model or calibrator versions;
* bundle identity is immutable and router resolved;
* unsupported/OOD inputs abstain;
* provider output is never self-publishable;
* can_execute is always false.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping, Sequence


CERTIFIED_STATES = frozenset({"PROSPECTIVE_CERTIFIED", "CHAMPION"})
FORBIDDEN_REQUEST_FIELDS = frozenset(
    {
        "direction",
        "model_version",
        "model_artifact_version",
        "calibrator_version",
        "specialist_version",
        "certification_id",
    }
)


class PropDistributionContractError(ValueError):
    """A fail-closed provider-contract violation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PropInferenceRequest:
    event_id: str
    player_id: str
    sport: str
    league_season: str
    stat_type: str
    evidence_snapshot_id: str
    market_identity_id: str
    as_of_timestamp: str
    request_id: str
    feature_schema_version: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PropInferenceRequest":
        forbidden = sorted(FORBIDDEN_REQUEST_FIELDS.intersection(payload))
        if forbidden:
            raise PropDistributionContractError(
                "CALLER_CONTROLLED_BUNDLE_OR_DIRECTION_PROHIBITED",
                f"provider request contains router-controlled fields: {forbidden}",
            )
        required = tuple(cls.__dataclass_fields__)
        missing = [name for name in required if not str(payload.get(name, "")).strip()]
        if missing:
            raise PropDistributionContractError(
                "PROP_INFERENCE_IDENTITY_INCOMPLETE",
                f"missing required provider identity fields: {missing}",
            )
        try:
            as_of = datetime.fromisoformat(str(payload["as_of_timestamp"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise PropDistributionContractError(
                "PROP_AS_OF_TIMESTAMP_INVALID", "as_of_timestamp must be ISO-8601"
            ) from exc
        if as_of.tzinfo is None:
            raise PropDistributionContractError(
                "PROP_AS_OF_TIMESTAMP_INVALID", "as_of_timestamp must include a timezone"
            )
        return cls(**{name: str(payload[name]) for name in required})


@dataclass(frozen=True)
class CertifiedBundle:
    model_artifact_version: str
    calibrator_version: str
    feature_transform_version: str
    specialist_version: str
    certification_id: str
    feature_schema_version: str
    training_dataset_hash: str
    training_code_sha: str
    artifact_checksum: str
    lifecycle_state: str
    supported_sport: str
    supported_stat_type: str
    supported_line_min: float
    supported_line_max: float

    def assert_compatible(self, request: PropInferenceRequest, line: float) -> None:
        if self.lifecycle_state not in CERTIFIED_STATES:
            raise PropDistributionContractError(
                "PROP_BUNDLE_NOT_CERTIFIED", "bundle is not prospectively certified"
            )
        if (
            request.feature_schema_version != self.feature_schema_version
            or request.sport.upper() != self.supported_sport.upper()
            or request.stat_type.upper() != self.supported_stat_type.upper()
            or not self.supported_line_min <= float(line) <= self.supported_line_max
        ):
            raise PropDistributionContractError(
                "MODEL_CALIBRATOR_BUNDLE_MISMATCH",
                "request is outside the immutable certified bundle contract",
            )

    @property
    def bundle_fingerprint(self) -> str:
        canonical = "|".join(
            (
                self.model_artifact_version,
                self.calibrator_version,
                self.feature_transform_version,
                self.specialist_version,
                self.certification_id,
                self.feature_schema_version,
                self.training_dataset_hash,
                self.training_code_sha,
                self.artifact_checksum,
            )
        )
        return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CoverageDecision:
    in_distribution: bool
    ood_score: float
    coverage_failures: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isfinite(self.ood_score) or not 0.0 <= self.ood_score <= 1.0:
            raise PropDistributionContractError(
                "OOD_SCORE_INVALID", "ood_score must be finite and within [0, 1]"
            )
        if not self.in_distribution and not self.coverage_failures:
            raise PropDistributionContractError(
                "OOD_REASON_REQUIRED", "an abstention must name at least one coverage failure"
            )


@dataclass(frozen=True)
class RawDiscreteDistribution:
    support: Mapping[int, float]
    coverage: CoverageDecision
    model_artifact_version: str
    training_code_sha: str
    training_dataset_hash: str
    feature_schema_version: str
    feature_transform_sha: str
    feature_snapshot_hash: str
    artifact_checksum: str
    inference_timestamp: str
    distribution_type: str = "DISCRETE_PMF"
    publication_status: str = "NOT_EVALUATED"
    can_execute: bool = False
    # Optional, additive audit trail an adapter may attach to explain how its
    # PMF was derived (e.g. which typed failure-path evidence materially
    # shifted the distribution's mean, and by what factor). Advisory/
    # explanatory only -- this dict can never carry a probability, bound, or
    # terminal label; __post_init__ enforces that. Default empty for every
    # adapter that has nothing to report, so existing callers are unaffected.
    failure_path_evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.distribution_type != "DISCRETE_PMF":
            raise PropDistributionContractError(
                "PROP_DISTRIBUTION_TYPE_UNSUPPORTED", "only DISCRETE_PMF is supported"
            )
        if self.publication_status != "NOT_EVALUATED" or self.can_execute:
            raise PropDistributionContractError(
                "PROVIDER_PUBLICATION_AUTHORITY_PROHIBITED",
                "raw provider output cannot publish or execute",
            )
        if not isinstance(self.failure_path_evidence, Mapping):
            raise PropDistributionContractError(
                "FAILURE_PATH_EVIDENCE_INVALID", "failure_path_evidence must be a mapping"
            )
        forbidden_evidence_keys = {
            "probability", "calibrated_probability", "calibrated_probability_lower_bound",
            "calibrated_probability_upper_bound", "lower_bound", "upper_bound",
            "terminal_label", "can_execute", "probability_publishable",
        }
        leaked = forbidden_evidence_keys.intersection(k.lower() for k in self.failure_path_evidence)
        if leaked:
            raise PropDistributionContractError(
                "FAILURE_PATH_EVIDENCE_AUTHORITY_PROHIBITED",
                f"failure_path_evidence cannot carry governed-output keys: {sorted(leaked)}",
            )
        if not self.support:
            raise PropDistributionContractError("PROP_PMF_EMPTY", "PMF support cannot be empty")
        total = 0.0
        for outcome, probability in self.support.items():
            if isinstance(outcome, bool) or not isinstance(outcome, int) or outcome < 0:
                raise PropDistributionContractError(
                    "PROP_PMF_SUPPORT_INVALID", "outcomes must be non-negative integers"
                )
            p = float(probability)
            if not isfinite(p) or p < 0.0 or p > 1.0:
                raise PropDistributionContractError(
                    "PROP_PMF_PROBABILITY_INVALID", "PMF probabilities must be finite and within [0, 1]"
                )
            total += p
        if abs(total - 1.0) > 1e-9:
            raise PropDistributionContractError(
                "PROP_PMF_NOT_NORMALIZED", f"PMF probability sum is {total!r}, expected 1"
            )
        try:
            inferred_at = datetime.fromisoformat(self.inference_timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PropDistributionContractError(
                "INFERENCE_TIMESTAMP_INVALID", "inference_timestamp must be ISO-8601"
            ) from exc
        if inferred_at.tzinfo is None:
            raise PropDistributionContractError(
                "INFERENCE_TIMESTAMP_INVALID", "inference_timestamp must include a timezone"
            )

    @property
    def expected_value(self) -> float:
        return sum(outcome * float(probability) for outcome, probability in self.support.items())

    @property
    def variance(self) -> float:
        mean = self.expected_value
        return sum(((outcome - mean) ** 2) * float(probability) for outcome, probability in self.support.items())

    def quantile(self, probability: float) -> int:
        if not 0.0 <= probability <= 1.0:
            raise ValueError("quantile probability must be within [0, 1]")
        cumulative = 0.0
        for outcome in sorted(self.support):
            cumulative += float(self.support[outcome])
            if cumulative + 1e-15 >= probability:
                return outcome
        return max(self.support)


@dataclass(frozen=True)
class LineProbabilities:
    line: float
    probability_more: float
    probability_less: float
    push_probability: float


def derive_line_probabilities(
    distribution: RawDiscreteDistribution, line: float
) -> LineProbabilities:
    """Derive mutually exclusive MORE/LESS/PUSH probabilities from one PMF.

    Whole-number settlement:
      MORE 4.0 cashes at >=5 and pushes at 4.
      LESS 4.0 cashes at <=3 and pushes at 4.
    Half-lines have no integer outcome equal to the line and therefore no push.
    """
    if not isfinite(float(line)) or float(line) < 0:
        raise PropDistributionContractError("PROP_LINE_INVALID", "line must be finite and non-negative")
    more = sum(float(p) for outcome, p in distribution.support.items() if outcome > line)
    less = sum(float(p) for outcome, p in distribution.support.items() if outcome < line)
    push = sum(float(p) for outcome, p in distribution.support.items() if outcome == line)
    if abs((more + less + push) - 1.0) > 1e-9:
        raise PropDistributionContractError(
            "PROP_LINE_DERIVATION_NOT_NORMALIZED", "derived line probabilities are inconsistent"
        )
    return LineProbabilities(float(line), more, less, push)


def mix_failure_paths(
    components: Sequence[tuple[float, Mapping[int, float]]],
) -> Mapping[int, float]:
    """Combine role/minutes regimes into one unconditional PMF.

    Each component is (regime_probability, conditional_pmf).  DNP/void
    settlement stays outside this numeric outcome PMF and must be handled by the
    market-identity/settlement layer.
    """
    if not components:
        raise PropDistributionContractError("FAILURE_PATH_MIXTURE_EMPTY", "mixture requires components")
    weight_sum = sum(float(weight) for weight, _ in components)
    if abs(weight_sum - 1.0) > 1e-9:
        raise PropDistributionContractError(
            "FAILURE_PATH_WEIGHTS_NOT_NORMALIZED", "regime weights must sum to 1"
        )
    mixed: dict[int, float] = {}
    for weight, pmf in components:
        if weight < 0:
            raise PropDistributionContractError(
                "FAILURE_PATH_WEIGHT_INVALID", "regime weights cannot be negative"
            )
        component_total = sum(float(p) for p in pmf.values())
        if abs(component_total - 1.0) > 1e-9:
            raise PropDistributionContractError(
                "FAILURE_PATH_COMPONENT_NOT_NORMALIZED", "each conditional PMF must sum to 1"
            )
        for outcome, probability in pmf.items():
            if isinstance(outcome, bool) or not isinstance(outcome, int) or outcome < 0:
                raise PropDistributionContractError(
                    "PROP_PMF_SUPPORT_INVALID", "outcomes must be non-negative integers"
                )
            mixed[outcome] = mixed.get(outcome, 0.0) + float(weight) * float(probability)
    return dict(sorted(mixed.items()))
