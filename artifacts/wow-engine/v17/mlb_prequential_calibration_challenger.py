"""Research-only prequential calibration challenger for WOW V17 MLB.

The calibrator is deliberately chronology strict: a prediction for one game date
may use only settled outcomes from strictly earlier dates. Same-day and future
outcomes never enter that date's fitted slope. The centered logit map keeps 0.5
fixed and, because negative slopes are rejected, cannot reverse the model's
selected side.

This module has no production registration, publication, or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
EPS = 1e-9


@dataclass(frozen=True)
class PrequentialCenteredResult:
    probabilities: np.ndarray
    slopes: np.ndarray
    prior_counts: np.ndarray
    eligible: np.ndarray
    min_prior: int
    method: str = "PREQUENTIAL_CENTERED_LOGIT_V1"
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False


def _probabilities(values) -> np.ndarray:
    p = np.asarray(values, dtype=float)
    if p.ndim != 1 or len(p) == 0 or not np.all(np.isfinite(p)):
        raise ValueError("MLB_PREQUENTIAL_PROBABILITIES_INVALID")
    if np.any((p <= 0.0) | (p >= 1.0)):
        raise ValueError("MLB_PREQUENTIAL_PROBABILITY_DOMAIN_INVALID")
    return np.clip(p, EPS, 1.0 - EPS)


def _outcomes(values) -> np.ndarray:
    y = np.asarray(values, dtype=int)
    if y.ndim != 1 or len(y) == 0 or not np.all(np.isin(y, [0, 1])):
        raise ValueError("MLB_PREQUENTIAL_OUTCOMES_INVALID")
    return y


def _dates(values) -> np.ndarray:
    try:
        dates = np.asarray(values, dtype="datetime64[D]")
    except (TypeError, ValueError) as exc:
        raise ValueError("MLB_PREQUENTIAL_DATES_INVALID") from exc
    if dates.ndim != 1 or len(dates) == 0 or np.any(np.isnat(dates)):
        raise ValueError("MLB_PREQUENTIAL_DATES_INVALID")
    return dates


def _logit(p: np.ndarray) -> np.ndarray:
    return np.log(p / (1.0 - p))


def _sigmoid(values) -> np.ndarray:
    z = np.clip(np.asarray(values, dtype=float), -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-z))


def _fit_centered_slope(
    probabilities: np.ndarray,
    outcomes: np.ndarray,
    *,
    max_iter: int,
    tol: float,
) -> float:
    if len(np.unique(outcomes)) < 2:
        raise ValueError("MLB_PREQUENTIAL_PRIOR_CLASS_DEGENERATE")
    x = _logit(probabilities)
    slope = 1.0
    for _ in range(max_iter):
        mapped = _sigmoid(slope * x)
        gradient = float(np.sum((mapped - outcomes) * x))
        hessian = float(np.sum(mapped * (1.0 - mapped) * x * x))
        if not np.isfinite(hessian) or hessian <= 1e-12:
            raise ValueError("MLB_PREQUENTIAL_HESSIAN_INVALID")
        slope -= gradient / hessian
        if not np.isfinite(slope) or slope <= 0.0:
            raise ValueError("MLB_PREQUENTIAL_SLOPE_INVALID")
        if abs(gradient) < tol:
            break
    return float(slope)


def rolling_centered_predictions(
    dates,
    probabilities,
    outcomes,
    *,
    min_prior: int = 60,
    max_iter: int = 50,
    tol: float = 1e-8,
) -> PrequentialCenteredResult:
    """Fit one centered slope per date from strictly earlier settled rows.

    Rows before ``min_prior`` prior observations accumulate remain ineligible and
    return NaN probabilities/slopes. This makes warm-up explicit instead of
    silently borrowing future or same-day outcomes.
    """
    d = _dates(dates)
    p = _probabilities(probabilities)
    y = _outcomes(outcomes)
    if len(d) != len(p) or len(d) != len(y):
        raise ValueError("MLB_PREQUENTIAL_LENGTH_MISMATCH")
    if min_prior < 30:
        raise ValueError("MLB_PREQUENTIAL_MIN_PRIOR_TOO_SMALL")
    if max_iter < 1 or max_iter > 500:
        raise ValueError("MLB_PREQUENTIAL_MAX_ITER_INVALID")
    if not np.isfinite(tol) or tol <= 0.0:
        raise ValueError("MLB_PREQUENTIAL_TOLERANCE_INVALID")

    mapped = np.full(len(p), np.nan, dtype=float)
    slopes = np.full(len(p), np.nan, dtype=float)
    prior_counts = np.zeros(len(p), dtype=int)
    eligible = np.zeros(len(p), dtype=bool)

    for current_date in np.unique(d):
        current = d == current_date
        prior = d < current_date
        prior_n = int(np.sum(prior))
        prior_counts[current] = prior_n
        if prior_n < min_prior:
            continue
        slope = _fit_centered_slope(
            p[prior],
            y[prior].astype(float),
            max_iter=max_iter,
            tol=tol,
        )
        current_p = _sigmoid(slope * _logit(p[current]))
        mapped[current] = current_p
        slopes[current] = slope
        eligible[current] = True

    return PrequentialCenteredResult(
        probabilities=mapped,
        slopes=slopes,
        prior_counts=prior_counts,
        eligible=eligible,
        min_prior=min_prior,
    )
