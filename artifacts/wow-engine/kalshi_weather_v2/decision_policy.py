from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Mapping


class DecisionAction(str, Enum):
    TRADE_NOW = "TRADE_NOW"
    WAIT = "WAIT"
    ABSTAIN = "ABSTAIN"


class AbstentionReason(str, Enum):
    INSUFFICIENT_EDGE = "INSUFFICIENT_EDGE"
    HIGH_EPISTEMIC_UNCERTAINTY = "HIGH_EPISTEMIC_UNCERTAINTY"
    SETTLEMENT_AMBIGUITY = "SETTLEMENT_AMBIGUITY"
    LOW_EDGE_SURVIVAL = "LOW_EDGE_SURVIVAL"
    POOR_LIQUIDITY = "POOR_LIQUIDITY"
    ADVERSE_SELECTION_RISK = "ADVERSE_SELECTION_RISK"
    WAIT_FOR_HIGH_VOI_EVENT = "WAIT_FOR_HIGH_VOI_EVENT"


class DecisionPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class DecisionPolicy:
    """Versioned decision-policy metadata with no implicit production thresholds."""

    policy_id: str
    version: str
    evidence_id: str
    effective_from: str
    lane: str | None = None
    minimum_edge: float | None = None
    minimum_voi_gain: float | None = None
    minimum_edge_survival_probability: float | None = None
    research_only: bool = True
    can_execute: bool = False

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise DecisionPolicyError("DECISION_POLICY_ID_MISSING")
        if not self.version.strip():
            raise DecisionPolicyError("DECISION_POLICY_VERSION_MISSING")
        if not self.evidence_id.strip():
            raise DecisionPolicyError("DECISION_POLICY_EVIDENCE_MISSING")
        if not self.effective_from.strip():
            raise DecisionPolicyError("DECISION_POLICY_EFFECTIVE_FROM_MISSING")
        if self.can_execute:
            raise DecisionPolicyError("DECISION_POLICY_EXECUTION_PROHIBITED")
        for name in ("minimum_edge", "minimum_voi_gain", "minimum_edge_survival_probability"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise DecisionPolicyError(f"{name.upper()}_INVALID")
            number = float(value)
            if not math.isfinite(number):
                raise DecisionPolicyError(f"{name.upper()}_INVALID")
            if name == "minimum_edge_survival_probability" and not (0.0 <= number <= 1.0):
                raise DecisionPolicyError(f"{name.upper()}_OUT_OF_RANGE")


class DecisionPolicyRegistry:
    """Versioned registry downstream of the independent weather probability."""

    def __init__(self) -> None:
        self._policies: dict[tuple[str, str], DecisionPolicy] = {}

    def register(self, policy: DecisionPolicy) -> DecisionPolicy:
        key = (policy.policy_id, policy.version)
        existing = self._policies.get(key)
        if existing is not None and existing != policy:
            raise DecisionPolicyError("DECISION_POLICY_VERSION_COLLISION")
        self._policies[key] = policy
        return policy

    def get(self, policy_id: str, version: str) -> DecisionPolicy:
        try:
            return self._policies[(policy_id, version)]
        except KeyError as exc:
            raise DecisionPolicyError("DECISION_POLICY_NOT_FOUND") from exc

    def snapshot(self) -> Mapping[str, DecisionPolicy]:
        return {
            f"{policy_id}@{version}": policy
            for (policy_id, version), policy in sorted(self._policies.items())
        }
