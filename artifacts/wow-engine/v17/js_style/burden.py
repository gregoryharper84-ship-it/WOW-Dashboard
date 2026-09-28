"""Robust descriptive threshold-burden features for JS-style research.

These features measure where an exact settlement threshold sits relative to a
pregame comparable-role distribution. They are not hit probabilities, model
confidence, lower bounds, or qualification authority.
"""
from __future__ import annotations

from statistics import median

from v17.js_style.contracts import (
    EVIDENCE_COMPLETE,
    EVIDENCE_INCOMPLETE,
    ThresholdBurdenResult,
    ThresholdEvidence,
)

MIN_COMPARABLE_SAMPLE = 5
_MAD_NORMALIZER = 1.4826
_IQR_NORMALIZER = 1.349


def _quantile(values: tuple[float, ...], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("JS_THRESHOLD_EMPTY_DISTRIBUTION")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    fraction = position - lo
    return ordered[lo] * (1.0 - fraction) + ordered[hi] * fraction


def _robust_dispersion(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    centre = median(values)
    mad = median(tuple(abs(float(value) - centre) for value in values))
    if mad > 0:
        return float(mad) * _MAD_NORMALIZER
    iqr = _quantile(values, 0.75) - _quantile(values, 0.25)
    if iqr > 0:
        return float(iqr) / _IQR_NORMALIZER
    return None


def _distribution_position(values: tuple[float, ...], threshold: float) -> float:
    ordered = tuple(float(value) for value in values)
    return sum(value <= threshold for value in ordered) / len(ordered)


def compute_threshold_burden(evidence: ThresholdEvidence) -> ThresholdBurdenResult:
    missing: list[str] = []
    direction = str(evidence.direction or "").upper()
    if direction not in {"MORE", "LESS"}:
        missing.append("direction")
    if evidence.exact_settlement_threshold is None:
        missing.append("exact_settlement_threshold")
    if not str(evidence.period or "").strip():
        missing.append("period")
    if evidence.role_adjusted_median is None:
        missing.append("role_adjusted_median")
    if len(evidence.comparable_values) < MIN_COMPARABLE_SAMPLE:
        missing.append("comparable_sample")
    if not str(evidence.source_provenance or "").strip():
        missing.append("source_provenance")
    if not str(evidence.as_of or "").strip():
        missing.append("as_of")

    if missing:
        return ThresholdBurdenResult(
            evidence_status=EVIDENCE_INCOMPLETE,
            robust_dispersion=None,
            threshold_burden_robust=None,
            threshold_distribution_position=None,
            sample_size=len(evidence.comparable_values),
            missing_evidence=tuple(missing),
        )

    values = tuple(float(value) for value in evidence.comparable_values)
    dispersion = _robust_dispersion(values)
    if dispersion is None or dispersion <= 0:
        return ThresholdBurdenResult(
            evidence_status=EVIDENCE_INCOMPLETE,
            robust_dispersion=None,
            threshold_burden_robust=None,
            threshold_distribution_position=None,
            sample_size=len(values),
            missing_evidence=("robust_dispersion",),
        )

    threshold = float(evidence.exact_settlement_threshold)
    role_median = float(evidence.role_adjusted_median)
    # Positive means the exact threshold is farther in the research-favorable
    # direction relative to the role median. Direction itself contributes no
    # score; it only orients the signed distance.
    signed_distance = (
        threshold - role_median if direction == "LESS" else role_median - threshold
    )
    burden = signed_distance / dispersion

    return ThresholdBurdenResult(
        evidence_status=EVIDENCE_COMPLETE,
        robust_dispersion=dispersion,
        threshold_burden_robust=burden,
        threshold_distribution_position=_distribution_position(values, threshold),
        sample_size=len(values),
        missing_evidence=(),
    )


__all__ = ["MIN_COMPARABLE_SAMPLE", "compute_threshold_burden"]
