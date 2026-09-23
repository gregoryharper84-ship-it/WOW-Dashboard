"""Typed separation of cohort reliability and event-specific uncertainty.

Research only. A historical calibration-bin confidence interval is deliberately a
different type from an event-specific fitted-model/bootstrap uncertainty envelope.
Neither type authorizes publication or execution.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


@dataclass(frozen=True)
class CohortReliabilityInterval:
    method: str
    cohort_n: int
    lower: float
    upper: float
    calibration_start: str
    calibration_end: str
    semantic_type: str = "HISTORICAL_COHORT_RELIABILITY_INTERVAL"
    probability_publishable: bool = False
    can_execute: bool = False


@dataclass(frozen=True)
class EventProbabilityUncertainty:
    method: str
    replicate_n: int
    point_probability: float
    lower: float
    upper: float
    confidence: float
    uncertainty_scope: str
    semantic_type: str = "EVENT_SPECIFIC_MODEL_UNCERTAINTY"
    probability_publishable: bool = False
    can_execute: bool = False


def cohort_reliability_interval(*, method, cohort_n, lower, upper, calibration_start, calibration_end):
    if cohort_n <= 0 or not (0 <= lower <= upper <= 1):
        raise ValueError("MLB_COHORT_RELIABILITY_INTERVAL_INVALID")
    return CohortReliabilityInterval(
        method=str(method),
        cohort_n=int(cohort_n),
        lower=float(lower),
        upper=float(upper),
        calibration_start=str(calibration_start),
        calibration_end=str(calibration_end),
    )


def event_uncertainty_from_bootstrap(
    probability_replicates,
    *,
    point_probability: float,
    confidence: float = 0.95,
    minimum_replicates: int = 200,
    uncertainty_scope: str = "FITTED_MODEL_BOOTSTRAP",
):
    values = np.asarray(probability_replicates, dtype=float)
    if values.ndim != 1 or len(values) < minimum_replicates:
        raise ValueError("MLB_EVENT_UNCERTAINTY_BOOTSTRAP_SAMPLE_INSUFFICIENT")
    if not np.all(np.isfinite(values)) or np.any((values < 0) | (values > 1)):
        raise ValueError("MLB_EVENT_UNCERTAINTY_BOOTSTRAP_VALUES_INVALID")
    if not (0 < point_probability < 1) or not (0.80 <= confidence < 1):
        raise ValueError("MLB_EVENT_UNCERTAINTY_PARAMETERS_INVALID")
    alpha = (1.0 - confidence) / 2.0
    lower, upper = np.quantile(values, [alpha, 1.0 - alpha])
    return EventProbabilityUncertainty(
        method="BOOTSTRAP_QUANTILE_V1",
        replicate_n=len(values),
        point_probability=float(point_probability),
        lower=float(lower),
        upper=float(upper),
        confidence=float(confidence),
        uncertainty_scope=str(uncertainty_scope),
    )
