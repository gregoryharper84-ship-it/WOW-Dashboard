"""
gate_engine/moneyline/external_analyst/contradiction_engine.py
WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE

Contradiction analysis for the External Analyst Intelligence layer.

Compares each independent analyst opinion against:
  1. The WOW core model's side (wow_side = "home" | "away")
  2. Other independent analyst opinions (to detect inter-analyst conflict)

Outputs ContradictionReport with all six required governance fields:
  external_analyst_agreement_count
  external_analyst_contradiction_count
  external_analyst_consensus_side
  external_analyst_conflict_flag
  external_analyst_conflict_reasons
  unresolved_claims

Key governance rules:
  - contradiction_count >= 1 credible independent analyst → route to contradiction audit
  - contradiction_count >= 2 → force_contradiction_review = True
  - Analysts disagree with each other → ANALYST_CONSENSUS_UNRESOLVED
  - Analyst consensus NEVER flips the pick or directly adjusts probability
  - Agreement/contradiction count NEVER grants direct probability weight

can_execute=False unconditional.
"""
from __future__ import annotations

from typing import Any

from gate_engine.moneyline.external_analyst.types import (
    AnalystOpinion,
    ContradictionReport,
    AnalystConsensus,
    AnalystSourceStatus,
)

can_execute: bool = False  # UNCONDITIONAL

# Thresholds for escalation
_FORCE_REVIEW_THRESHOLD = 2    # >= 2 independent opposing → force review
_HIGH_PRIORITY_AGREE    = 3    # >= 3 agreeing → HIGH research priority


def _is_credible(opinion: AnalystOpinion) -> bool:
    """
    An opinion is credible for contradiction counting when:
    - source_status is RETRIEVED (not stale, proxy, or unobtainable)
    - side is a recognized value ("home" or "away" for moneyline)
    - is_syndicated_copy is False (deduplicated)
    """
    return (
        opinion.source_status == AnalystSourceStatus.RETRIEVED
        and not opinion.is_syndicated_copy
        and opinion.side in ("home", "away")
    )


def _sides_agree(analyst_side: str | None, wow_side: str | None) -> bool | None:
    """
    Return True if analyst and WOW pick the same side.
    Return None if either side is unknown.
    """
    if not analyst_side or not wow_side:
        return None
    return analyst_side.lower().strip() == wow_side.lower().strip()


def run_contradiction_analysis(
    independent_opinions: list[AnalystOpinion],
    wow_side:             str | None,
    wow_independent_prob: float | None = None,
) -> ContradictionReport:
    """
    Analyze independent analyst opinions against the WOW model side.

    Parameters
    ----------
    independent_opinions  : Deduplicated analyst opinions (one per family)
    wow_side              : WOW core model's favored side ("home" | "away")
    wow_independent_prob  : WOW independent probability (for future calibration)

    Returns ContradictionReport.
    """
    report = ContradictionReport()
    notes: list[str] = []
    unresolved: list[str] = []
    conflict_reasons: list[str] = []

    credible = [op for op in independent_opinions if _is_credible(op)]
    report.independent_analyst_count = len(credible)

    if not credible:
        report.external_analyst_consensus_side = AnalystConsensus.ABSENT
        report.research_priority = "NORMAL"
        report.analyst_consensus_notes = ["NO_CREDIBLE_ANALYST_OPINIONS_RETRIEVED"]
        return report

    # Tally agreement / contradiction vs WOW
    agree_count    = 0
    oppose_count   = 0
    unknown_count  = 0
    analyst_sides: list[str] = []

    for op in credible:
        agreement = _sides_agree(op.side, wow_side)
        if agreement is True:
            agree_count += 1
            analyst_sides.append(op.side or "")
        elif agreement is False:
            oppose_count += 1
            analyst_sides.append(op.side or "")
            conflict_reasons.append(
                f"{op.source_name}/{op.analyst_name or 'staff'}"
                f" picks {op.side} (WOW={wow_side})"
            )
        else:
            unknown_count += 1

        # Collect unverified claims from thesis tags
        for claim in op.thesis_tags.all_claims():
            if claim and claim not in unresolved:
                unresolved.append(claim)

    report.external_analyst_agreement_count    = agree_count
    report.external_analyst_contradiction_count = oppose_count

    # Analyst-consensus side
    unique_sides = set(s for s in analyst_sides if s)
    if len(unique_sides) == 0:
        report.external_analyst_consensus_side = AnalystConsensus.ABSENT
    elif len(unique_sides) == 1:
        # All credible analysts agree on one side
        consensus = unique_sides.pop()
        if wow_side and consensus.lower() == wow_side.lower():
            report.external_analyst_consensus_side = AnalystConsensus.AGREE
        else:
            report.external_analyst_consensus_side = AnalystConsensus.OPPOSE
    else:
        # Analysts pick different sides → unresolved
        report.external_analyst_consensus_side = AnalystConsensus.ANALYST_CONSENSUS_UNRESOLVED
        notes.append(
            f"ANALYST_CONSENSUS_UNRESOLVED: "
            f"credible analysts disagree ({sorted(unique_sides)}); "
            "candidate held at lower confidence ceiling until specialist model resolves"
        )

    # Conflict flag
    report.external_analyst_conflict_flag = oppose_count >= 1
    report.external_analyst_conflict_reasons = conflict_reasons

    # Force review threshold
    report.force_contradiction_review = oppose_count >= _FORCE_REVIEW_THRESHOLD
    if report.force_contradiction_review:
        notes.append(
            f"FORCE_CONTRADICTION_REVIEW: {oppose_count} independent analysts oppose "
            f"WOW side={wow_side}; explicit review required before high-confidence publication"
        )
    elif oppose_count >= 1:
        notes.append(
            f"CONTRADICTION_REVIEW_ROUTED: {oppose_count} independent analyst(s) "
            f"oppose WOW side={wow_side}"
        )

    # Research priority
    if oppose_count >= _FORCE_REVIEW_THRESHOLD:
        report.research_priority = "HIGH"
    elif oppose_count >= 1 or agree_count >= _HIGH_PRIORITY_AGREE:
        report.research_priority = "ELEVATED"
    else:
        report.research_priority = "NORMAL"

    # Unresolved claims (all analyst-stated factual claims pending verification)
    report.unresolved_claims = unresolved

    report.analyst_consensus_notes = notes
    return report
