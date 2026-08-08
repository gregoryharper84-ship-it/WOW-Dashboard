"""
gate_engine/moneyline/model_disagreement.py
WOW v16 — Cross-submodel disagreement auditor.

When Elo/power, matchup, lineup/role, and simulation submodels disagree
materially, uncertainty is widened and the calibrated lower bound is lowered.
Do not average conflicting models into false confidence.

can_execute=False unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False

# Disagreement thresholds (absolute probability difference)
_LOW_THRESHOLD      = 0.05   # max pairwise diff < 5pp → LOW
_MODERATE_THRESHOLD = 0.10   # max pairwise diff 5–10pp → MODERATE
# above 10pp → HIGH

# Uncertainty widening factors per grade
_WIDENING_FACTORS: dict[str, float] = {
    "LOW":      1.00,
    "MODERATE": 1.15,
    "HIGH":     1.35,
}


@dataclass
class DisagreementAudit:
    """
    Cross-submodel disagreement measurement output.

    disagreement_grade: LOW | MODERATE | HIGH
    uncertainty_widening_factor: applied multiplicatively to dynamic_uncertainty
                                  in the calibration step
    """
    submodel_probs:              dict[str, float]
    pairwise_diffs:              list[dict[str, Any]]   # [{a, b, diff}]
    max_disagreement:            float
    mean_disagreement:           float
    disagreement_grade:          str      # LOW | MODERATE | HIGH
    uncertainty_widening_factor: float    # 1.0 / 1.15 / 1.35
    active_submodel_count:       int
    notes:                       list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "submodel_probs":              {k: round(v, 4) for k, v in self.submodel_probs.items()},
            "pairwise_diffs":              self.pairwise_diffs,
            "max_disagreement":            round(self.max_disagreement, 4),
            "mean_disagreement":           round(self.mean_disagreement, 4),
            "disagreement_grade":          self.disagreement_grade,
            "uncertainty_widening_factor": round(self.uncertainty_widening_factor, 4),
            "active_submodel_count":       self.active_submodel_count,
            "notes":                       self.notes,
        }


def audit_model_disagreement(submodel_probs: dict[str, float]) -> DisagreementAudit:
    """
    Compute pairwise absolute differences across all submodel point estimates.

    Parameters
    ----------
    submodel_probs : {submodel_name: probability} for all active submodels.
                     Expected keys include any of:
                       h2h_historical, elo_differential, power_rating,
                       matchup_model, simulation_output, lineup_role_model

    Returns DisagreementAudit with grade and uncertainty widening factor.
    """
    items = [(k, v) for k, v in submodel_probs.items()
             if v is not None and 0.0 <= v <= 1.0]
    n = len(items)
    notes: list[str] = []

    if n == 0:
        return DisagreementAudit(
            submodel_probs={},
            pairwise_diffs=[],
            max_disagreement=0.0,
            mean_disagreement=0.0,
            disagreement_grade="LOW",
            uncertainty_widening_factor=1.0,
            active_submodel_count=0,
            notes=["NO_SUBMODELS:treating_as_LOW_disagreement"],
        )

    if n == 1:
        name, prob = items[0]
        return DisagreementAudit(
            submodel_probs={name: prob},
            pairwise_diffs=[],
            max_disagreement=0.0,
            mean_disagreement=0.0,
            disagreement_grade="LOW",
            uncertainty_widening_factor=1.0,
            active_submodel_count=1,
            notes=["SINGLE_SUBMODEL:no_cross_model_disagreement_possible"],
        )

    # Compute all pairwise diffs
    pairwise: list[dict[str, Any]] = []
    for i in range(n):
        for j in range(i + 1, n):
            na, pa = items[i]
            nb, pb = items[j]
            diff = abs(pa - pb)
            pairwise.append({
                "submodel_a": na,
                "submodel_b": nb,
                "prob_a":     round(pa, 4),
                "prob_b":     round(pb, 4),
                "diff":       round(diff, 4),
            })

    diffs = [p["diff"] for p in pairwise]
    max_diff  = max(diffs)
    mean_diff = sum(diffs) / len(diffs)

    if max_diff < _LOW_THRESHOLD:
        grade = "LOW"
    elif max_diff < _MODERATE_THRESHOLD:
        grade = "MODERATE"
    else:
        grade = "HIGH"
        notes.append(
            f"HIGH_DISAGREEMENT:max_pairwise_diff={max_diff:.4f} "
            f"({max_diff*100:.1f}pp) → uncertainty widened by "
            f"{(_WIDENING_FACTORS['HIGH']-1)*100:.0f}%"
        )

    widening = _WIDENING_FACTORS[grade]

    return DisagreementAudit(
        submodel_probs=dict(submodel_probs),
        pairwise_diffs=pairwise,
        max_disagreement=max_diff,
        mean_disagreement=mean_diff,
        disagreement_grade=grade,
        uncertainty_widening_factor=widening,
        active_submodel_count=n,
        notes=notes,
    )
