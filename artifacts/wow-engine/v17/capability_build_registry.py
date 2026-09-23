"""Step-0 build classification for V17 cross-sport capability work.

This registry separates deterministic contracts (D0), fitted/simulation math (D1),
evidence agents (A1), and multi-source research orchestration (A2). It is a
build-planning contract only and never creates model capability.

Controlling sporting probability is D1 for every team/event winner lane below.
A1/A2 components may acquire or reconcile evidence, but they may never publish,
calibrate, rank, or substitute a sporting probability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False

BuildClass = Literal["D0", "D1", "A1", "A2"]
BuildStatus = Literal[
    "CERTIFIED",
    "FORWARD_EVIDENCE_PENDING",
    "CURRENT_DATA_PENDING",
    "EVIDENCE_CORPUS_PENDING",
    "FITTED_SPECIALIST_PENDING",
]


@dataclass(frozen=True)
class CapabilityBuildPlan:
    sport: str
    controlling_probability_class: BuildClass
    evidence_classes: tuple[BuildClass, ...]
    status: BuildStatus
    fitted_model_required: bool
    agent_may_publish_probability: bool
    automatic_certification: bool
    automatic_promotion: bool
    probability_publishable_from_registry: bool
    can_execute: bool = False


TEAM_EVENT_BUILD_REGISTRY: dict[str, CapabilityBuildPlan] = {
    "MLB": CapabilityBuildPlan("MLB", "D1", ("A1",), "CERTIFIED", True, False, False, False, False),
    "NFL": CapabilityBuildPlan("NFL", "D1", ("A1",), "FORWARD_EVIDENCE_PENDING", True, False, False, False, False),
    "NBA": CapabilityBuildPlan("NBA", "D1", ("A1",), "CURRENT_DATA_PENDING", True, False, False, False, False),
    "WNBA": CapabilityBuildPlan("WNBA", "D1", ("A1",), "CURRENT_DATA_PENDING", True, False, False, False, False),
    "NCAAF": CapabilityBuildPlan("NCAAF", "D1", ("A1", "A2"), "EVIDENCE_CORPUS_PENDING", True, False, False, False, False),
    "NCAAB": CapabilityBuildPlan("NCAAB", "D1", ("A1",), "FITTED_SPECIALIST_PENDING", True, False, False, False, False),
    "NHL": CapabilityBuildPlan("NHL", "D1", ("A1",), "EVIDENCE_CORPUS_PENDING", True, False, False, False, False),
    "SOCCER": CapabilityBuildPlan("SOCCER", "D1", ("A1", "A2"), "FITTED_SPECIALIST_PENDING", True, False, False, False, False),
    "TENNIS": CapabilityBuildPlan("TENNIS", "D1", ("A1",), "FITTED_SPECIALIST_PENDING", True, False, False, False, False),
    "PGA": CapabilityBuildPlan("PGA", "D1", ("A1", "A2"), "FITTED_SPECIALIST_PENDING", True, False, False, False, False),
    "MMA": CapabilityBuildPlan("MMA", "D1", ("A1", "A2"), "FITTED_SPECIALIST_PENDING", True, False, False, False, False),
    "BOXING": CapabilityBuildPlan("BOXING", "D1", ("A1", "A2"), "FITTED_SPECIALIST_PENDING", True, False, False, False, False),
    "CRICKET": CapabilityBuildPlan("CRICKET", "D1", ("A1", "A2"), "FITTED_SPECIALIST_PENDING", True, False, False, False, False),
}


def capability_build_plan(sport: str) -> CapabilityBuildPlan:
    normalized = str(sport or "").strip().upper()
    try:
        return TEAM_EVENT_BUILD_REGISTRY[normalized]
    except KeyError as exc:
        raise KeyError(f"V17_BUILD_CLASSIFICATION_UNKNOWN:{normalized}") from exc


def validate_build_registry() -> None:
    expected = set(EXPECTED_TEAM_EVENT_SPORTS)
    actual = set(TEAM_EVENT_BUILD_REGISTRY)
    if actual != expected:
        raise RuntimeError(
            f"V17_BUILD_REGISTRY_COVERAGE_DRIFT:missing={sorted(expected-actual)}:extra={sorted(actual-expected)}"
        )
    for sport, plan in TEAM_EVENT_BUILD_REGISTRY.items():
        if plan.sport != sport:
            raise RuntimeError(f"V17_BUILD_REGISTRY_IDENTITY_DRIFT:{sport}")
        if plan.controlling_probability_class != "D1":
            raise RuntimeError(f"V17_CONTROLLING_PROBABILITY_NOT_D1:{sport}")
        if plan.agent_may_publish_probability:
            raise RuntimeError(f"V17_AGENT_PROBABILITY_PUBLICATION_FORBIDDEN:{sport}")
        if plan.automatic_certification or plan.automatic_promotion:
            raise RuntimeError(f"V17_AUTOMATIC_MODEL_GOVERNANCE_FORBIDDEN:{sport}")
        if plan.probability_publishable_from_registry or plan.can_execute:
            raise RuntimeError(f"V17_BUILD_REGISTRY_SIDE_EFFECT_FORBIDDEN:{sport}")


validate_build_registry()


__all__ = [
    "BuildClass",
    "BuildStatus",
    "CAN_EXECUTE",
    "CapabilityBuildPlan",
    "TEAM_EVENT_BUILD_REGISTRY",
    "capability_build_plan",
    "validate_build_registry",
]
