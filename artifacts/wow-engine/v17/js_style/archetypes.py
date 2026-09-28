"""Evidence-driven descriptive JS archetype labels."""
from __future__ import annotations

from dataclasses import dataclass

from v17.js_style.contracts import (
    ARCHETYPES,
    ArchetypeResult,
    EVIDENCE_COMPLETE,
    EVIDENCE_INCOMPLETE,
    validate_closed_vocabulary,
)


@dataclass(frozen=True)
class ArchetypeEvidence:
    direction: str
    evidence_complete: bool
    threshold_ceiling_support: bool = False
    role_ceiling_support: bool = False
    matchup_suppression_support: bool = False
    period_window_support: bool = False
    defensive_volume_support: bool = False
    stable_floor_support: bool = False
    promo_anchor_support: bool = False


def classify_archetypes(evidence: ArchetypeEvidence) -> ArchetypeResult:
    direction = str(evidence.direction or "").upper()
    if direction not in {"MORE", "LESS"} or not evidence.evidence_complete:
        return ArchetypeResult(
            evidence_status=EVIDENCE_INCOMPLETE,
            archetypes=(),
            reasons=("direction_or_required_evidence_incomplete",),
        )

    labels: list[str] = []
    reasons: list[str] = []
    if direction == "LESS":
        if evidence.threshold_ceiling_support:
            labels.append("JS_CEILING_LESS")
            reasons.append("threshold_ceiling_support")
        if evidence.role_ceiling_support:
            labels.append("JS_ROLE_LESS")
            reasons.append("role_ceiling_support")
        if evidence.matchup_suppression_support:
            labels.append("JS_MATCHUP_SUPPRESSION_LESS")
            reasons.append("matchup_suppression_support")
        if evidence.period_window_support:
            labels.append("JS_WINDOW_LESS")
            reasons.append("period_window_support")
        if evidence.defensive_volume_support:
            labels.append("JS_DEFENSIVE_VOLUME_LESS")
            reasons.append("defensive_volume_support")
    else:
        if evidence.stable_floor_support:
            labels.append("JS_FLOOR_MORE")
            reasons.append("stable_floor_support")

    if evidence.promo_anchor_support:
        labels.append("JS_PROMO_ANCHOR")
        reasons.append("promo_anchor_support")

    validate_closed_vocabulary(labels, ARCHETYPES, code="JS_ARCHETYPE_UNKNOWN")
    return ArchetypeResult(
        evidence_status=EVIDENCE_COMPLETE,
        archetypes=tuple(labels),
        reasons=tuple(reasons),
    )


__all__ = ["ArchetypeEvidence", "classify_archetypes"]
