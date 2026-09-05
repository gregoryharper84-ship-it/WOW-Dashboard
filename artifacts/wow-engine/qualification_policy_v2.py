"""V17 prop sporting-probability qualification policy.

This policy separates a completed governed sporting probability from recommendation
qualification. Exact market price/payout and portfolio construction remain separate
objective lanes and cannot erase a valid sporting probability.

Qualification thresholds intentionally preserve the previously governed WOW policy:
- MODEL_QUALIFIED_HOLD: p >= 0.60 and calibrated lower bound >= 0.55
- HIGH confidence metadata: p >= 0.65 and lower bound >= 0.60
- RESEARCH_INTEREST: p >= 0.57 and lower bound > 0.50, never rank eligible

Phase-A PRECALIBRATION_SHRINKAGE is a valid sporting-probability package but is
not evidence of proven calibration. It may be published for prospective learning,
but it is never rank eligible, model qualified, or eligible for downstream money
evaluation. This mirrors calibration.py's ratified Phase-A prohibition on
MONEY_QUALIFIED / FINAL_APPROVED while preserving the completed probability.

No new uncertainty-width cutoff is introduced without a separately certified policy
artifact. can_execute remains false.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


QUALIFICATION_POLICY_VERSION = "PROP_MODEL_QUALIFICATION_V17_CERTIFIED_THRESHOLDS"
PRECALIBRATION_STATUS = "PRECALIBRATION_SHRINKAGE"


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


def _decision(
    *,
    terminal_label: str,
    confidence_tier: str,
    rank_eligible: bool,
    model_supported: bool,
    model_qualified: bool,
    model_qualification_status: str,
    uncertainty_width: float | None,
    downstream_money_evaluation_allowed: bool,
    blockers: tuple[str, ...],
    reasons: tuple[str, ...],
) -> PropQualificationDecision:
    return PropQualificationDecision(
        terminal_label=terminal_label,
        confidence_tier=confidence_tier,
        rank_eligible=rank_eligible,
        model_supported=model_supported,
        model_qualified=model_qualified,
        model_qualification_status=model_qualification_status,
        qualification_policy_version=QUALIFICATION_POLICY_VERSION,
        uncertainty_width=uncertainty_width,
        downstream_money_evaluation_allowed=downstream_money_evaluation_allowed,
        final_approved_allowed=False,
        blockers=blockers,
        qualification_reasons=reasons,
    )


def classify_prop_probability(
    *,
    calibrated_probability: float | None,
    calibrated_lower_bound: float | None,
    calibrated_upper_bound: float | None = None,
    calibration_status: str | None,
    blockers: Iterable[str] = (),
    probability_publishable: bool,
    model_quality_status: str = "PASS",
    input_complete: bool = True,
) -> PropQualificationDecision:
    blocker_tuple = _normalized(blockers)
    if not input_complete:
        return _decision(
            terminal_label="MODEL_INPUTS_INSUFFICIENT",
            confidence_tier="BLOCKED",
            rank_eligible=False,
            model_supported=False,
            model_qualified=False,
            model_qualification_status="NOT_QUALIFIED",
            uncertainty_width=None,
            downstream_money_evaluation_allowed=False,
            blockers=blocker_tuple + ("MODEL_INPUTS_INSUFFICIENT",),
            reasons=("MODEL_INPUTS_INSUFFICIENT",),
        )

    if any(b in HARD_BLOCKERS for b in blocker_tuple):
        terminal = "MODEL_UNAVAILABLE" if any(
            b in {"MODEL_UNAVAILABLE", "CONTROLLING_SPECIALIST_UNAVAILABLE", "MODEL_CALIBRATION_UNAVAILABLE"}
            for b in blocker_tuple
        ) else "MODEL_INPUTS_INSUFFICIENT"
        return _decision(
            terminal_label=terminal,
            confidence_tier="BLOCKED",
            rank_eligible=False,
            model_supported=False,
            model_qualified=False,
            model_qualification_status="NOT_QUALIFIED",
            uncertainty_width=None,
            downstream_money_evaluation_allowed=False,
            blockers=blocker_tuple,
            reasons=("SPORTING_MODEL_OR_INPUT_GATE_NOT_PASS",),
        )

    if not probability_publishable:
        return _decision(
            terminal_label="MODEL_UNAVAILABLE",
            confidence_tier="PUBLICATION_BLOCKED",
            rank_eligible=False,
            model_supported=False,
            model_qualified=False,
            model_qualification_status="NOT_QUALIFIED",
            uncertainty_width=None,
            downstream_money_evaluation_allowed=False,
            blockers=blocker_tuple + ("PROBABILITY_PUBLICATION_BLOCKED",),
            reasons=("GOVERNED_SPORTING_PROBABILITY_NOT_PUBLISHABLE",),
        )

    try:
        p = float(calibrated_probability)
        lb = float(calibrated_lower_bound)
    except (TypeError, ValueError):
        p = lb = float("nan")
    if not all(isfinite(v) for v in (p, lb)) or not (0.0 < lb <= p < 1.0):
        return _decision(
            terminal_label="MODEL_OUTPUT_INVALID",
            confidence_tier="BLOCKED",
            rank_eligible=False,
            model_supported=False,
            model_qualified=False,
            model_qualification_status="NOT_QUALIFIED",
            uncertainty_width=None,
            downstream_money_evaluation_allowed=False,
            blockers=blocker_tuple + ("CALIBRATED_PROBABILITY_OR_BOUND_MISSING",),
            reasons=("CALIBRATED_PACKAGE_INVALID",),
        )

    width: float | None = None
    if calibrated_upper_bound is not None:
        try:
            ub = float(calibrated_upper_bound)
        except (TypeError, ValueError):
            ub = float("nan")
        if not isfinite(ub) or not (p <= ub < 1.0):
            return _decision(
                terminal_label="MODEL_OUTPUT_INVALID",
                confidence_tier="BLOCKED",
                rank_eligible=False,
                model_supported=False,
                model_qualified=False,
                model_qualification_status="NOT_QUALIFIED",
                uncertainty_width=None,
                downstream_money_evaluation_allowed=False,
                blockers=blocker_tuple + ("CALIBRATED_UPPER_BOUND_INVALID",),
                reasons=("CALIBRATED_PACKAGE_INVALID",),
            )
        width = ub - lb

    calibration_health = str(calibration_status or "").strip().upper()
    if not calibration_health or calibration_health in UNHEALTHY_CALIBRATION_STATES:
        return _decision(
            terminal_label="MODEL_UNAVAILABLE",
            confidence_tier="CALIBRATION_BLOCKED",
            rank_eligible=False,
            model_supported=False,
            model_qualified=False,
            model_qualification_status="NOT_QUALIFIED",
            uncertainty_width=width,
            downstream_money_evaluation_allowed=False,
            blockers=blocker_tuple + ("MODEL_CALIBRATION_UNAVAILABLE",),
            reasons=("ROUTE_CALIBRATION_HEALTH_NOT_PASS",),
        )

    quality = str(model_quality_status or "").strip().upper()
    if quality not in {"PASS", "IN_DISTRIBUTION", "SUPPORTED"}:
        return _decision(
            terminal_label="REJECT_OOD",
            confidence_tier="MODEL_QUALITY_BLOCKED",
            rank_eligible=False,
            model_supported=False,
            model_qualified=False,
            model_qualification_status="NOT_QUALIFIED",
            uncertainty_width=width,
            downstream_money_evaluation_allowed=False,
            blockers=blocker_tuple + ("MODEL_QUALITY_NOT_PASS",),
            reasons=("MODEL_SUPPORT_OR_COVERAGE_NOT_PASS",),
        )

    reasons = [
        f"CALIBRATED_P={p:.6f}",
        f"LOWER_BOUND={lb:.6f}",
        f"CALIBRATION={calibration_health}",
        f"MODEL_QUALITY={quality}",
        "QUALIFICATION_THRESHOLDS=PREVIOUSLY_GOVERNED_POLICY",
    ]
    if width is not None:
        reasons.append(f"INTERVAL_WIDTH_ADVISORY={width:.6f}")

    # Ratified Phase-A ceiling: PRECALIBRATION_SHRINKAGE is a completed,
    # publishable sporting probability used to build the prospective settled
    # calibration cohort, but it is not proven calibration and therefore can
    # never become rank/money qualified. Preserve the probability rather than
    # rewriting it as MODEL_UNAVAILABLE.
    if calibration_health == PRECALIBRATION_STATUS:
        reasons.append("PRECALIBRATION_CEILING=RESEARCH_ONLY_NO_MONEY_QUALIFICATION")
        return _decision(
            terminal_label="RESEARCH_INTEREST",
            confidence_tier="PRECALIBRATION",
            rank_eligible=False,
            model_supported=True,
            model_qualified=False,
            model_qualification_status="MODEL_NOT_QUALIFIED",
            uncertainty_width=width,
            downstream_money_evaluation_allowed=False,
            blockers=blocker_tuple,
            reasons=tuple(reasons),
        )

    if p >= 0.65 and lb >= 0.60:
        tier = "HIGH"
        terminal = "MODEL_QUALIFIED_HOLD"
        qualified = True
    elif p >= 0.60 and lb >= 0.55:
        tier = "STANDARD"
        terminal = "MODEL_QUALIFIED_HOLD"
        qualified = True
    elif p >= 0.57 and lb > 0.50:
        tier = "RESEARCH"
        terminal = "RESEARCH_INTEREST"
        qualified = False
    else:
        tier = "BELOW_THRESHOLD"
        terminal = "NO_LOW_PROBABILITY"
        qualified = False

    return _decision(
        terminal_label=terminal,
        confidence_tier=tier,
        rank_eligible=qualified,
        model_supported=True,
        model_qualified=qualified,
        model_qualification_status="MODEL_QUALIFIED" if qualified else "MODEL_NOT_QUALIFIED",
        uncertainty_width=width,
        downstream_money_evaluation_allowed=qualified,
        blockers=blocker_tuple,
        reasons=tuple(reasons),
    )
