"""V17 prop sporting-probability qualification policy.

The policy answers only the model-lane question: given a valid governed sporting
probability package, is this side strong/reliable enough for betting
consideration?  Market price/payout, EV and portfolio construction are separate
objective lanes and cannot erase a completed sporting probability.

`MODEL_QUALIFIED` is an explicit decision field; the outer terminal may remain
`MODEL_QUALIFIED_HOLD` until downstream value/card objectives are evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


QUALIFICATION_POLICY_VERSION = "PROP_MODEL_QUALIFICATION_V1"
MAX_QUALIFIED_INTERVAL_WIDTH = 0.20
MAX_STRONG_INTERVAL_WIDTH = 0.18


@dataclass(frozen=True)
class PropQualificationDecision:
    terminal_label: str
    confidence_tier: str
    rank_eligible: bool
    model_supported: bool
    model_qualified: bool
    model_qualification_status: str
    qualification_policy_version: str
    uncertainty_width: float | None
    downstream_money_evaluation_allowed: bool
    final_approved_allowed: bool
    blockers: tuple[str, ...]
    qualification_reasons: tuple[str, ...]


# Sporting/model blockers only. Market/payout evidence is deliberately absent.
HARD_BLOCKERS = {
    "MODEL_UNAVAILABLE",
    "CONTROLLING_SPECIALIST_UNAVAILABLE",
    "EVIDENCE_INCOMPLETE",
    "ROLE_STATUS_UNAVAILABLE",
    "EVENT_NOT_PREGAME",
    "STALE_EVIDENCE",
    "PROBABILITY_INVALID",
    "MODEL_CALIBRATION_UNAVAILABLE",
}

UNHEALTHY_CALIBRATION_STATES = {
    "UNKNOWN",
    "UNKNOWN_OR_BLOCKED",
    "UNAVAILABLE",
    "FAILED",
    "BLOCKED",
    "CALIBRATION_BLOCKED",
}


def _normalized(blockers: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(b).strip().upper() for b in blockers if str(b).strip()))


def _blocked(
    *, terminal_label: str, confidence_tier: str, blockers: tuple[str, ...], reasons: tuple[str, ...]
) -> PropQualificationDecision:
    return PropQualificationDecision(
        terminal_label=terminal_label,
        confidence_tier=confidence_tier,
        rank_eligible=False,
        model_supported=False,
        model_qualified=False,
        model_qualification_status="NOT_QUALIFIED",
        qualification_policy_version=QUALIFICATION_POLICY_VERSION,
        uncertainty_width=None,
        downstream_money_evaluation_allowed=False,
        final_approved_allowed=False,
        blockers=blockers,
        qualification_reasons=reasons,
    )


def classify_prop_probability(
    *,
    calibrated_probability: float | None,
    calibrated_lower_bound: float | None,
    calibrated_upper_bound: float | None,
    calibration_status: str | None,
    blockers: Iterable[str] = (),
    probability_publishable: bool,
    model_quality_status: str = "PASS",
    input_complete: bool = True,
) -> PropQualificationDecision:
    blocker_tuple = _normalized(blockers)
    if any(b in HARD_BLOCKERS for b in blocker_tuple) or not input_complete:
        return _blocked(
            terminal_label="MODEL_INPUTS_INSUFFICIENT" if not input_complete else "MODEL_UNAVAILABLE",
            confidence_tier="BLOCKED",
            blockers=blocker_tuple + (() if input_complete else ("MODEL_INPUTS_INSUFFICIENT",)),
            reasons=("SPORTING_MODEL_OR_INPUT_GATE_NOT_PASS",),
        )

    if not probability_publishable:
        return _blocked(
            terminal_label="MODEL_UNAVAILABLE",
            confidence_tier="PUBLICATION_BLOCKED",
            blockers=blocker_tuple + ("PROBABILITY_PUBLICATION_BLOCKED",),
            reasons=("GOVERNED_SPORTING_PROBABILITY_NOT_PUBLISHABLE",),
        )

    try:
        p = float(calibrated_probability)
        lb = float(calibrated_lower_bound)
        ub = float(calibrated_upper_bound)
    except (TypeError, ValueError):
        p = lb = ub = float("nan")
    if not all(isfinite(v) for v in (p, lb, ub)) or not (0.0 < lb <= p <= ub < 1.0):
        return _blocked(
            terminal_label="MODEL_OUTPUT_INVALID",
            confidence_tier="BLOCKED",
            blockers=blocker_tuple + ("CALIBRATED_PROBABILITY_OR_BOUND_MISSING",),
            reasons=("CALIBRATED_PACKAGE_INVALID",),
        )

    calibration_health = str(calibration_status or "").strip().upper()
    if not calibration_health or calibration_health in UNHEALTHY_CALIBRATION_STATES:
        return _blocked(
            terminal_label="MODEL_UNAVAILABLE",
            confidence_tier="CALIBRATION_BLOCKED",
            blockers=blocker_tuple + ("MODEL_CALIBRATION_UNAVAILABLE",),
            reasons=("ROUTE_CALIBRATION_HEALTH_NOT_PASS",),
        )

    quality = str(model_quality_status or "").strip().upper()
    if quality not in {"PASS", "IN_DISTRIBUTION", "SUPPORTED"}:
        return _blocked(
            terminal_label="REJECT_OOD",
            confidence_tier="MODEL_QUALITY_BLOCKED",
            blockers=blocker_tuple + ("MODEL_QUALITY_NOT_PASS",),
            reasons=("MODEL_SUPPORT_OR_COVERAGE_NOT_PASS",),
        )

    width = ub - lb
    reasons = [
        f"CALIBRATED_P={p:.6f}",
        f"LOWER_BOUND={lb:.6f}",
        f"INTERVAL_WIDTH={width:.6f}",
        f"CALIBRATION={calibration_health}",
        f"MODEL_QUALITY={quality}",
    ]

    # Qualification is intentionally conservative but no longer a point-probability
    # only threshold.  The lower bound and uncertainty width are load-bearing.
    if p >= 0.65 and lb >= 0.60 and width <= MAX_STRONG_INTERVAL_WIDTH:
        tier, qualified = "ELITE", True
    elif p >= 0.60 and lb >= 0.57 and width <= MAX_STRONG_INTERVAL_WIDTH:
        tier, qualified = "STRONG", True
    elif p >= 0.57 and lb >= 0.55 and width <= MAX_QUALIFIED_INTERVAL_WIDTH:
        tier, qualified = "QUALIFIED", True
    elif p >= 0.54 and lb > 0.50:
        tier, qualified = "LEAN", False
    elif p <= 0.46:
        tier, qualified = "OPPOSITE_SIDE_LEAN", False
    else:
        tier, qualified = "NEUTRAL", False

    if qualified:
        terminal_label = "MODEL_QUALIFIED_HOLD"
        model_status = "MODEL_QUALIFIED"
    elif tier in {"LEAN", "OPPOSITE_SIDE_LEAN"}:
        terminal_label = "RESEARCH_INTEREST"
        model_status = "MODEL_NOT_QUALIFIED"
    else:
        terminal_label = "NO_LOW_PROBABILITY"
        model_status = "MODEL_NOT_QUALIFIED"

    # Value qualification is downstream and price-dependent.  This flag means the
    # model package is eligible to enter that lane, not that value/EV has passed.
    value_lane_allowed = qualified
    return PropQualificationDecision(
        terminal_label=terminal_label,
        confidence_tier=tier,
        rank_eligible=qualified,
        model_supported=True,
        model_qualified=qualified,
        model_qualification_status=model_status,
        qualification_policy_version=QUALIFICATION_POLICY_VERSION,
        uncertainty_width=width,
        downstream_money_evaluation_allowed=value_lane_allowed,
        final_approved_allowed=False,
        blockers=blocker_tuple,
        qualification_reasons=tuple(reasons),
    )
