"""V17 quarantine for pre-governance moneyline probability components.

Several legacy Flask-era modules call sport lanes ACTIVE/PROVISIONAL and contain
hand-authored probability formulas.  Under V17 those labels are not certification
artifacts and those formulas are not governed fitted probability packages.

This registry permits reuse of explicitly listed evidence/acquisition contracts,
while prohibiting any legacy raw/calibrated probability, bound, model-status, or
rank signal from satisfying V17 model capability.
"""
from __future__ import annotations

from dataclasses import dataclass

CAN_EXECUTE = False


@dataclass(frozen=True)
class LegacyComponentDisposition:
    component: str
    sport_scope: tuple[str, ...]
    evidence_reuse_allowed: bool
    probability_reuse_allowed: bool
    certification_reuse_allowed: bool
    rank_reuse_allowed: bool
    disposition: str
    can_execute: bool = False


LEGACY_COMPONENTS: tuple[LegacyComponentDisposition, ...] = (
    LegacyComponentDisposition(
        component="gate_engine.moneyline.team_acquisition",
        sport_scope=("NBA", "WNBA", "TENNIS", "MLB"),
        evidence_reuse_allowed=True,
        probability_reuse_allowed=False,
        certification_reuse_allowed=False,
        rank_reuse_allowed=False,
        disposition="EVIDENCE_ONLY_REVALIDATE_PROVENANCE_AND_TIMESTAMP",
    ),
    LegacyComponentDisposition(
        component="gate_engine.moneyline.sport_model",
        sport_scope=("NBA", "WNBA", "NFL", "NHL", "SOCCER", "TENNIS", "MMA"),
        evidence_reuse_allowed=False,
        probability_reuse_allowed=False,
        certification_reuse_allowed=False,
        rank_reuse_allowed=False,
        disposition="RESEARCH_BASELINE_ONLY_NOT_GOVERNED_MODEL",
    ),
    LegacyComponentDisposition(
        component="gate_engine.moneyline_probability._SPORT_MODEL_REGISTRY",
        sport_scope=("MLB", "NBA", "WNBA", "NFL", "NHL", "SOCCER", "TENNIS", "MMA"),
        evidence_reuse_allowed=False,
        probability_reuse_allowed=False,
        certification_reuse_allowed=False,
        rank_reuse_allowed=False,
        disposition="LEGACY_STATUS_LABELS_HAVE_NO_V17_AUTHORITY",
    ),
    LegacyComponentDisposition(
        component="gate_engine.tennis_total_games",
        sport_scope=("TENNIS",),
        evidence_reuse_allowed=True,
        probability_reuse_allowed=False,
        certification_reuse_allowed=False,
        rank_reuse_allowed=False,
        disposition="RESEARCH_SIMULATION_COMPONENT_REQUIRES_V17_REFIT_VALIDATION",
    ),
)


def legacy_component_disposition(component: str) -> LegacyComponentDisposition:
    normalized = str(component or "").strip()
    for row in LEGACY_COMPONENTS:
        if row.component == normalized:
            return row
    raise KeyError(f"V17_LEGACY_COMPONENT_UNCLASSIFIED:{normalized}")


def assert_legacy_probability_quarantine() -> None:
    for row in LEGACY_COMPONENTS:
        if row.probability_reuse_allowed:
            raise RuntimeError(f"V17_LEGACY_PROBABILITY_REUSE_FORBIDDEN:{row.component}")
        if row.certification_reuse_allowed:
            raise RuntimeError(f"V17_LEGACY_CERTIFICATION_REUSE_FORBIDDEN:{row.component}")
        if row.rank_reuse_allowed:
            raise RuntimeError(f"V17_LEGACY_RANK_REUSE_FORBIDDEN:{row.component}")
        if row.can_execute:
            raise RuntimeError(f"V17_LEGACY_EXECUTION_FORBIDDEN:{row.component}")


assert_legacy_probability_quarantine()


__all__ = [
    "CAN_EXECUTE",
    "LEGACY_COMPONENTS",
    "LegacyComponentDisposition",
    "assert_legacy_probability_quarantine",
    "legacy_component_disposition",
]
