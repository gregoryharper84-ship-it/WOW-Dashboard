"""Tiered probability qualification policy for WOW v16 Clean Core.

This policy separates model-backed research qualification from the much stricter
money/final approval ceilings. It does not weaken evidence, specialist,
identity, freshness, calibration-health, or execution gates.

Phase-A precalibration is intentionally conservative. Candidates may therefore
be recognized as model-supported research rows when the fitted specialist,
evidence, failure paths, and calibrated probability are all valid, while still
remaining ineligible for MONEY_QUALIFIED / FINAL_APPROVED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PropQualificationDecision:
    terminal_label: str
    rank_eligible: bool
    model_supported: bool
    money_qualified_allowed: bool
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
    """Classify a prop probability without conflating research and money gates.

    Thresholds are deliberately conservative and are only terminal-label
    thresholds. They never override an upstream blocker.

    RESEARCH_INTEREST
      calibrated p >= 0.57 and lower bound > 0.50

    MODEL_QUALIFIED_HOLD
      calibrated p >= 0.60 and lower bound >= 0.55

    HIGH_CONFIDENCE_MODEL_QUALIFIED_HOLD
      calibrated p >= 0.65 and lower bound >= 0.60

    PRECALIBRATION_SHRINKAGE can reach research/model-qualified HOLD labels but
    remains prohibited from MONEY_QUALIFIED and FINAL_APPROVED.
    """
    blocker_tuple = tuple(dict.fromkeys(str(b) for b in blockers if b))
    if _has_hard_blocker(blocker_tuple):
        return PropQualificationDecision(
            terminal_label="MODEL_UNAVAILABLE",
            rank_eligible=False,
            model_supported=False,
            money_qualified_allowed=False,
            final_approved_allowed=False,
            blockers=blocker_tuple,
        )

    if not probability_publishable:
        return PropQualificationDecision(
            terminal_label="RESEARCH_INTEREST" if calibration_status == "PRECALIBRATION_SHRINKAGE" else "MODEL_QUALIFIED_HOLD",
            rank_eligible=calibration_status == "PRECALIBRATION_SHRINKAGE",
            model_supported=True,
            money_qualified_allowed=False,
            final_approved_allowed=False,
            blockers=blocker_tuple,
        )

    if calibrated_probability is None or calibrated_lower_bound is None:
        return PropQualificationDecision(
            terminal_label="MODEL_UNAVAILABLE",
            rank_eligible=False,
            model_supported=False,
            money_qualified_allowed=False,
            final_approved_allowed=False,
            blockers=blocker_tuple + ("CALIBRATED_PROBABILITY_OR_BOUND_MISSING",),
        )

    p = float(calibrated_probability)
    lb = float(calibrated_lower_bound)
    if not (0.0 < p < 1.0 and 0.0 < lb < 1.0 and lb <= p):
        return PropQualificationDecision(
            terminal_label="MODEL_UNAVAILABLE",
            rank_eligible=False,
            model_supported=False,
            money_qualified_allowed=False,
            final_approved_allowed=False,
            blockers=blocker_tuple + ("PROBABILITY_INVALID",),
        )

    if p >= 0.65 and lb >= 0.60:
        label = "HIGH_CONFIDENCE_MODEL_QUALIFIED_HOLD"
        rank_eligible = True
    elif p >= 0.60 and lb >= 0.55:
        label = "MODEL_QUALIFIED_HOLD"
        rank_eligible = True
    elif p >= 0.57 and lb > 0.50:
        label = "RESEARCH_INTEREST"
        rank_eligible = True
    else:
        label = "NO_LOW_PROBABILITY"
        rank_eligible = False

    precalibration = calibration_status == "PRECALIBRATION_SHRINKAGE"
    return PropQualificationDecision(
        terminal_label=label,
        rank_eligible=rank_eligible,
        model_supported=label != "NO_LOW_PROBABILITY",
        money_qualified_allowed=False if precalibration else label in {"MODEL_QUALIFIED_HOLD", "HIGH_CONFIDENCE_MODEL_QUALIFIED_HOLD"},
        final_approved_allowed=False if precalibration else label == "HIGH_CONFIDENCE_MODEL_QUALIFIED_HOLD",
        blockers=blocker_tuple,
    )
