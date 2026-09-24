"""Research-only prequential MLB run-contrast adaptation challenger.

The challenger operates before the governed score-distribution reducer. It keeps
one game's geometric-mean run environment fixed and scales only the fitted
HOME-vs-AWAY log-run contrast. For each game date, the positive contrast scale
is fitted from strictly earlier settled run counts only; same-day and future
outcomes are excluded.

No production registration, publication, automatic promotion, or execution
capability is provided here.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False


@dataclass(frozen=True)
class PrequentialRunContrastResult:
    home_mu: np.ndarray
    away_mu: np.ndarray
    contrast_scales: np.ndarray
    prior_counts: np.ndarray
    eligible: np.ndarray
    min_prior: int
    method: str = "PREQUENTIAL_RUN_LOG_CONTRAST_V1"
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False


def _positive(values, code: str) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or len(x) == 0 or not np.all(np.isfinite(x)) or np.any(x <= 0.0):
        raise ValueError(code)
    return x


def _runs(values, code: str) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or len(x) == 0 or not np.all(np.isfinite(x)) or np.any(x < 0.0):
        raise ValueError(code)
    return x


def _dates(values) -> np.ndarray:
    try:
        d = np.asarray(values, dtype="datetime64[D]")
    except (TypeError, ValueError) as exc:
        raise ValueError("MLB_RUN_CONTRAST_DATES_INVALID") from exc
    if d.ndim != 1 or len(d) == 0 or np.any(np.isnat(d)):
        raise ValueError("MLB_RUN_CONTRAST_DATES_INVALID")
    return d


def _fit_scale(
    center: np.ndarray,
    contrast: np.ndarray,
    home_runs: np.ndarray,
    away_runs: np.ndarray,
    *,
    max_iter: int,
    lower: float,
    upper: float,
    tol: float,
) -> float:
    scale = 1.0
    for _ in range(max_iter):
        home_mu = np.exp(np.clip(center + scale * contrast, -10.0, 10.0))
        away_mu = np.exp(np.clip(center - scale * contrast, -10.0, 10.0))
        gradient = float(
            np.sum(contrast * ((home_mu - home_runs) - (away_mu - away_runs)))
        )
        hessian = float(np.sum(contrast * contrast * (home_mu + away_mu)))
        if not np.isfinite(hessian) or hessian <= 1e-12:
            raise ValueError("MLB_RUN_CONTRAST_HESSIAN_INVALID")
        updated = scale - gradient / hessian
        scale = float(np.clip(updated, lower, upper))
        if abs(gradient) < tol:
            break
    if not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("MLB_RUN_CONTRAST_SCALE_INVALID")
    return scale


def rolling_run_contrast(
    dates,
    home_mu,
    away_mu,
    home_runs,
    away_runs,
    *,
    min_prior: int = 60,
    max_iter: int = 25,
    lower: float = 0.05,
    upper: float = 5.0,
    tol: float = 1e-8,
) -> PrequentialRunContrastResult:
    """Fit a positive log-run contrast scale from strictly earlier dates.

    For baseline log means ``lh`` and ``la``:

    ``center = (lh + la)/2`` and ``contrast = (lh - la)/2``.

    Adjusted means are ``exp(center +/- scale*contrast)``. This preserves the
    geometric mean of the two fitted run means and, because ``scale > 0``,
    preserves the sign of the HOME-vs-AWAY run contrast.
    """
    d = _dates(dates)
    hm = _positive(home_mu, "MLB_RUN_CONTRAST_HOME_MU_INVALID")
    am = _positive(away_mu, "MLB_RUN_CONTRAST_AWAY_MU_INVALID")
    hr = _runs(home_runs, "MLB_RUN_CONTRAST_HOME_RUNS_INVALID")
    ar = _runs(away_runs, "MLB_RUN_CONTRAST_AWAY_RUNS_INVALID")
    n = len(d)
    if any(len(x) != n for x in (hm, am, hr, ar)):
        raise ValueError("MLB_RUN_CONTRAST_LENGTH_MISMATCH")
    if min_prior < 30:
        raise ValueError("MLB_RUN_CONTRAST_MIN_PRIOR_TOO_SMALL")
    if max_iter < 1 or max_iter > 500:
        raise ValueError("MLB_RUN_CONTRAST_MAX_ITER_INVALID")
    if not (0.0 < lower < upper) or not np.isfinite(lower + upper):
        raise ValueError("MLB_RUN_CONTRAST_BOUNDS_INVALID")
    if not np.isfinite(tol) or tol <= 0.0:
        raise ValueError("MLB_RUN_CONTRAST_TOLERANCE_INVALID")

    log_h = np.log(hm)
    log_a = np.log(am)
    center = (log_h + log_a) / 2.0
    contrast = (log_h - log_a) / 2.0

    out_h = np.full(n, np.nan, dtype=float)
    out_a = np.full(n, np.nan, dtype=float)
    scales = np.full(n, np.nan, dtype=float)
    prior_counts = np.zeros(n, dtype=int)
    eligible = np.zeros(n, dtype=bool)

    for current_date in np.unique(d):
        current = d == current_date
        prior = d < current_date
        prior_n = int(np.sum(prior))
        prior_counts[current] = prior_n
        if prior_n < min_prior:
            continue
        scale = _fit_scale(
            center[prior],
            contrast[prior],
            hr[prior],
            ar[prior],
            max_iter=max_iter,
            lower=lower,
            upper=upper,
            tol=tol,
        )
        out_h[current] = np.exp(np.clip(center[current] + scale * contrast[current], -10.0, 10.0))
        out_a[current] = np.exp(np.clip(center[current] - scale * contrast[current], -10.0, 10.0))
        scales[current] = scale
        eligible[current] = True

    return PrequentialRunContrastResult(
        home_mu=out_h,
        away_mu=out_a,
        contrast_scales=scales,
        prior_counts=prior_counts,
        eligible=eligible,
        min_prior=min_prior,
    )
