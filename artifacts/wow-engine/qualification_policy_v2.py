"""Tiered probability qualification policy for WOW v16 Clean Core.

This policy separates model-backed research qualification from the much stricter
money/final approval gates. It does not weaken evidence, specialist, identity,
freshness, calibration-health, probability-validity, market, or execution gates.

Only native WOW terminal labels are emitted. Higher confidence is carried as
metadata, not invented as a new terminal label.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PropQualificationDecision:
    terminal_label: str
    confidence_tier: str
    rank_eligible: bool
    model_supported: bool
    downstream_money_evaluation_allowed: bool
    final_approved_allowed: bool
    blockers: tuple[str, ...]


HARD_BLOCKERS = {
    "MODEL_UNAVAILABLE",
    "CONTROLLING_SPECIALIST_UNAVAILABLE",
    "EVIDENCE_INCOMPLETE",
    "ROLE_STATUS_UNAVAILABLE",
    "EXACT_MARKET_IDENTITY_UNAVAILABLE",
    "EVENT_NOT_PREGAME",
    "STALE_EVIDENCE",
    "PROBABILITY_INVALID",
    "MODEL_CALIBRATION_UNAVAILABLE",
}


def _normalized(blockers: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(b).strip().upper() for b in blockers if str(b).strip()))


def _has_hard_blocker(blockers: Iterable[str]) -> bool:
    return any(str(b).upper() in HARD_BLOCKERS for b in blockers)


def classify_prop_probability(
    *,
    calibrated_probability: float | None,
    calibrated_lower_bound: float | None,
    calibration_status: str | None,
    blockers: Iterable[str] = (),
    probability_publishable: bool,
) -> PropQualificationDecision:
    """Classify only a completed governed candidate-level probability lane.

    A higher-level deployment-wide publication blocker (for example an incomplete
    forward-shadow cohort) is owned by the separate calibration/publication lane
    wrapper and must not be rewritten here as proof that the controlling
    specialist is unavailable.

    Thresholds are terminal-label/ranking gates, not wager approval gates:

    RESEARCH_INTEREST
      calibrated p >= 0.57 and lower bound > 0.50

    MODEL_QUALIFIED_HOLD
      calibrated p >= 0.60 and lower bound >= 0.55

    A stronger p >= 0.65 / lower bound >= 0.60 result remains the same native
    MODEL_QUALIFIED_HOLD terminal label and receives confidence_tier=HIGH.

    PRECALIBRATION_SHRINKAGE may reach RESEARCH_INTEREST or
    MODEL_QUALIFIED_HOLD, but it may not advance into the money/final approval
    gates. This function can never itself emit FINAL_APPROVED.
    """
    blocker_tuple = _normalized(blockers)
    if _has_hard_blocker(blocker_tuple):
        return PropQualificationDecision(
            terminal_label="MODEL_UNAVAILABLE",
            confidence_tier="BLOCKED",
            rank_eligible=False,
            model_supported=False,
            downstream_money_evaluation_allowed=False,
            final_approved_allowed=False,
            blockers=blocker_tuple,
        )

    if not probability_publishable:
        return PropQualificationDecision(
            terminal_label="MODEL_UNAVAILABLE",
            confidence_tier="PUBLICATION_BLOCKED",
            rank_eligible=False,
            model_supported=False,
            downstream_money_evaluation_allowed=False,
            final_approved_allowed=False,
            blockers=blocker_tuple + ("PROBABILITY_PUBLICATION_BLOCKED",),
        )

    if calibrated_probability is None or calibrated_lower_bound is None:
        return PropQualificationDecision(
            terminal_label="MODEL_UNAVAILABLE",
            confidence_tier="BLOCKED",
            rank_eligible=False,
            model_supported=False,
            downstream_money_evaluation_allowed=False,
            final_approved_allowed=False,
            blockers=blocker_tuple + ("CALIBRATED_PROBABILITY_OR_BOUND_MISSING",),
        )

    try:
        p = float(calibrated_probability)
        lb = float(calibrated_lower_bound)
    except (TypeError, ValueError):
        p = lb = float("nan")
    if not (0.0 < p < 1.0 and 0.0 < lb < 1.0 and lb <= p):
        return PropQualificationDecision(
            terminal_label="MODEL_UNAVAILABLE",
            confidence_tier="BLOCKED",
            rank_eligible=False,
            model_supported=False,
            downstream_money_evaluation_allowed=False,
            final_approved_allowed=False,
            blockers=blocker_tuple + ("PROBABILITY_INVALID",),
        )

    if p >= 0.65 and lb >= 0.60:
        label = "MODEL_QUALIFIED_HOLD"
        confidence_tier = "HIGH"
        rank_eligible = True
    elif p >= 0.60 and lb >= 0.55:
        label = "MODEL_QUALIFIED_HOLD"
        confidence_tier = "STANDARD"
        rank_eligible = True
    elif p >= 0.57 and lb > 0.50:
        label = "RESEARCH_INTEREST"
        confidence_tier = "RESEARCH"
        rank_eligible = True
    else:
        label = "NO_LOW_PROBABILITY"
        confidence_tier = "BELOW_THRESHOLD"
        rank_eligible = False

    precalibration = str(calibration_status or "").upper() == "PRECALIBRATION_SHRINKAGE"
    downstream_money_evaluation_allowed = (
        not precalibration and label == "MODEL_QUALIFIED_HOLD"
    )
    return PropQualificationDecision(
        terminal_label=label,
        confidence_tier=confidence_tier,
        rank_eligible=rank_eligible,
        model_supported=label in {"RESEARCH_INTEREST", "MODEL_QUALIFIED_HOLD"},
        downstream_money_evaluation_allowed=downstream_money_evaluation_allowed,
        final_approved_allowed=False,
        blockers=blocker_tuple,
    )
