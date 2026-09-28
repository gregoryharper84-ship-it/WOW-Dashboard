"""Closed contracts for the V17 JS-style research overlay.

No field in these contracts is probability authority.  The overlay can describe
research interest and construction structure only; governed model output remains
owned by the existing controlling specialist and V17 terminal reducer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from v17.js_style import (
    CAN_EXECUTE,
    JS_CLUSTER_SCHEMA_V1,
    JS_PROBABILITY_AUTHORITY,
    JS_STYLE_FEATURE_SCHEMA_V1,
    JS_STYLE_RULESET_V1,
    WOW_JS_STYLE_INTELLIGENCE_V17,
)

EVIDENCE_COMPLETE = "JS_EVIDENCE_COMPLETE"
EVIDENCE_INCOMPLETE = "JS_EVIDENCE_INCOMPLETE"

ARCHETYPES = frozenset(
    {
        "JS_CEILING_LESS",
        "JS_ROLE_LESS",
        "JS_MATCHUP_SUPPRESSION_LESS",
        "JS_WINDOW_LESS",
        "JS_DEFENSIVE_VOLUME_LESS",
        "JS_FLOOR_MORE",
        "JS_PROMO_ANCHOR",
    }
)

THESIS_CLASSIFICATIONS = frozenset(
    {
        "THESIS_COHERENT",
        "THESIS_NEUTRAL",
        "THESIS_CONFLICTING",
        "SHARED_FRAGILITY",
    }
)

RESEARCH_COMPONENT_KEYS = (
    "threshold_asymmetry",
    "role_workload_ceiling",
    "opponent_context_fit",
    "distribution_support",
    "stat_path_robustness",
    "period_window_fit",
    "game_thesis_coherence",
)

RESEARCH_COMPONENT_MAXIMA: Mapping[str, float] = {
    "threshold_asymmetry": 25.0,
    "role_workload_ceiling": 20.0,
    "opponent_context_fit": 15.0,
    "distribution_support": 15.0,
    "stat_path_robustness": 10.0,
    "period_window_fit": 5.0,
    "game_thesis_coherence": 10.0,
}

PENALTY_KEYS = frozenset(
    {
        "uncertain_role_minutes",
        "lineup_change",
        "foul_sensitivity",
        "overtime_sensitivity",
        "usage_spike_path",
        "unclear_settlement",
        "tiny_cohort",
        "contradictory_evidence",
    }
)


@dataclass(frozen=True)
class ThresholdEvidence:
    exact_settlement_threshold: float | None
    direction: str
    period: str | None
    comparable_values: tuple[float, ...] = ()
    role_adjusted_median: float | None = None
    source_provenance: str | None = None
    as_of: str | None = None
    cohort_name: str | None = None


@dataclass(frozen=True)
class ThresholdBurdenResult:
    evidence_status: str
    robust_dispersion: float | None
    threshold_burden_robust: float | None
    threshold_distribution_position: float | None
    sample_size: int
    missing_evidence: tuple[str, ...] = ()
    feature_schema_version: str = JS_STYLE_FEATURE_SCHEMA_V1
    js_probability_authority: bool = JS_PROBABILITY_AUTHORITY
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class ResearchPriorityResult:
    evidence_status: str
    components: Mapping[str, float]
    penalties: Mapping[str, float]
    js_research_priority: float | None
    ruleset_version: str = JS_STYLE_RULESET_V1
    js_probability_authority: bool = JS_PROBABILITY_AUTHORITY
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class ArchetypeResult:
    evidence_status: str
    archetypes: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    ruleset_version: str = JS_STYLE_RULESET_V1
    js_probability_authority: bool = JS_PROBABILITY_AUTHORITY
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class ThesisClusterResult:
    cluster_thesis_id: str
    cluster_thesis_version: str
    cluster_direction: str | None
    shared_driver: str | None
    joint_benefit_paths: tuple[str, ...]
    joint_failure_paths: tuple[str, ...]
    dependence_type: str | None
    dependence_evidence_status: str
    classification: str
    cluster_schema_version: str = JS_CLUSTER_SCHEMA_V1
    js_probability_authority: bool = JS_PROBABILITY_AUTHORITY
    joint_probability_computed: bool = False
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class BoardSnapshot:
    board_snapshot_id: str
    provider: str
    captured_at: str
    as_of: str
    source_snapshot_digest: str
    feature_schema_version: str = JS_STYLE_FEATURE_SCHEMA_V1
    ruleset_version: str = JS_STYLE_RULESET_V1
    js_probability_authority: bool = JS_PROBABILITY_AUTHORITY
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class CandidateObservation:
    board_snapshot_id: str
    provider_event_alias: str | None
    canonical_event_id: str | None
    participant_id: str | None
    participant_name: str
    sport: str
    stat: str
    period: str
    exact_settlement_threshold: float
    direction: str
    promo_type: str | None
    settlement_rule_version: str
    archetypes: tuple[str, ...]
    js_research_priority: float | None
    controlling_specialist_identity: str | None
    governed_scoring_status: str | None
    governed_typed_blocker: str | None
    selected_by_js: bool | None
    eventual_settlement: str | None = None
    observation_metadata: Mapping[str, str] = field(default_factory=dict)
    feature_schema_version: str = JS_STYLE_FEATURE_SCHEMA_V1
    ruleset_version: str = JS_STYLE_RULESET_V1
    js_probability_authority: bool = JS_PROBABILITY_AUTHORITY
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE


def validate_closed_vocabulary(values: Sequence[str], allowed: frozenset[str], *, code: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"{code}:{','.join(unknown)}")


def validate_governance_invariants() -> None:
    if JS_PROBABILITY_AUTHORITY is not False or CAN_EXECUTE is not False:
        raise RuntimeError("JS_STYLE_GOVERNANCE_INVARIANT_VIOLATION")


validate_governance_invariants()

__all__ = [
    "ARCHETYPES",
    "ArchetypeResult",
    "BoardSnapshot",
    "CandidateObservation",
    "EVIDENCE_COMPLETE",
    "EVIDENCE_INCOMPLETE",
    "PENALTY_KEYS",
    "RESEARCH_COMPONENT_KEYS",
    "RESEARCH_COMPONENT_MAXIMA",
    "ResearchPriorityResult",
    "THESIS_CLASSIFICATIONS",
    "ThesisClusterResult",
    "ThresholdBurdenResult",
    "ThresholdEvidence",
    "validate_closed_vocabulary",
]
