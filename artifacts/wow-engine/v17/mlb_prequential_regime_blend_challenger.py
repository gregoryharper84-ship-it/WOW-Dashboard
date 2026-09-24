"""Research-only prequential MLB run-regime blend challenger.

The challenger blends two independently fitted run specialists in log-mean space.
For each game date, one blend weight is fitted from strictly earlier settled HOME
and AWAY run counts by Poisson likelihood. Same-day and future outcomes are never
eligible for that date's fit.

This module has no production registration, publication, or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False


@dataclass(frozen=True)
class PrequentialRegimeBlendResult:
    home_mu: np.ndarray
    away_mu: np.ndarray
    weights: np.ndarray
    prior_counts: np.ndarray
    eligible: np.ndarray
    min_prior: int
    method: str = "PREQUENTIAL_LOG_MU_REGIME_BLEND_V1"
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False


def _dates(values) -> np.ndarray:
    try:
        dates = np.asarray(values, dtype="datetime64[D]")
    except (TypeError, ValueError) as exc:
        raise ValueError("MLB_REGIME_BLEND_DATES_INVALID") from exc
    if dates.ndim != 1 or len(dates) == 0 or np.any(np.isnat(dates)):
        raise ValueError("MLB_REGIME_BLEND_DATES_INVALID")
    return dates


def _mus(values, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or len(arr) == 0 or not np.all(np.isfinite(arr)):
        raise ValueError(f"MLB_REGIME_BLEND_{name}_INVALID")
    if np.any(arr <= 0.0):
        raise ValueError(f"MLB_REGIME_BLEND_{name}_DOMAIN_INVALID")
    return arr


def _runs(values, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or len(arr) == 0 or not np.all(np.isfinite(arr)):
        raise ValueError(f"MLB_REGIME_BLEND_{name}_INVALID")
    if np.any(arr < 0.0) or np.any(arr != np.floor(arr)):
        raise ValueError(f"MLB_REGIME_BLEND_{name}_DOMAIN_INVALID")
    return arr


def _fit_weight(
    home_mu_a: np.ndarray,
    away_mu_a: np.ndarray,
    home_mu_b: np.ndarray,
    away_mu_b: np.ndarray,
    home_runs: np.ndarray,
    away_runs: np.ndarray,
    *,
    max_iter: int,
    tol: float,
) -> float:
    base = np.concatenate((np.log(home_mu_a), np.log(away_mu_a)))
    target = np.concatenate((home_runs, away_runs))
    delta = np.concatenate(
        (
            np.log(home_mu_b) - np.log(home_mu_a),
            np.log(away_mu_b) - np.log(away_mu_a),
        )
    )
    if not np.any(np.abs(delta) > 1e-12):
        raise ValueError("MLB_REGIME_BLEND_SPECIALISTS_NOT_DISTINCT")

    weight = 0.5
    for _ in range(max_iter):
        mu = np.exp(np.clip(base + weight * delta, -20.0, 20.0))
        gradient = float(np.sum(delta * (mu - target)))
        hessian = float(np.sum(delta * delta * mu))
        if not np.isfinite(hessian) or hessian <= 1e-12:
            raise ValueError("MLB_REGIME_BLEND_HESSIAN_INVALID")
        next_weight = float(np.clip(weight - gradient / hessian, 0.0, 1.0))
        if abs(next_weight - weight) < tol:
            weight = next_weight
            break
        weight = next_weight
    if not np.isfinite(weight) or weight < 0.0 or weight > 1.0:
        raise ValueError("MLB_REGIME_BLEND_WEIGHT_INVALID")
    return weight


def rolling_regime_blend(
    dates,
    home_mu_a,
    away_mu_a,
    home_mu_b,
    away_mu_b,
    home_runs,
    away_runs,
    *,
    min_prior: int = 60,
    max_iter: int = 50,
    tol: float = 1e-8,
) -> PrequentialRegimeBlendResult:
    """Blend two run specialists using only strictly earlier settled outcomes."""
    d = _dates(dates)
    h_a = _mus(home_mu_a, "HOME_MU_A")
    a_a = _mus(away_mu_a, "AWAY_MU_A")
    h_b = _mus(home_mu_b, "HOME_MU_B")
    a_b = _mus(away_mu_b, "AWAY_MU_B")
    h_runs = _runs(home_runs, "HOME_RUNS")
    a_runs = _runs(away_runs, "AWAY_RUNS")

    arrays = (h_a, a_a, h_b, a_b, h_runs, a_runs)
    if any(len(arr) != len(d) for arr in arrays):
        raise ValueError("MLB_REGIME_BLEND_LENGTH_MISMATCH")
    if min_prior < 30:
        raise ValueError("MLB_REGIME_BLEND_MIN_PRIOR_TOO_SMALL")
    if max_iter < 1 or max_iter > 500:
        raise ValueError("MLB_REGIME_BLEND_MAX_ITER_INVALID")
    if not np.isfinite(tol) or tol <= 0.0:
        raise ValueError("MLB_REGIME_BLEND_TOLERANCE_INVALID")

    out_home = np.full(len(d), np.nan, dtype=float)
    out_away = np.full(len(d), np.nan, dtype=float)
    weights = np.full(len(d), np.nan, dtype=float)
    prior_counts = np.zeros(len(d), dtype=int)
    eligible = np.zeros(len(d), dtype=bool)

    for current_date in np.unique(d):
        current = d == current_date
        prior = d < current_date
        prior_n = int(np.sum(prior))
        prior_counts[current] = prior_n
        if prior_n < min_prior:
            continue

        weight = _fit_weight(
            h_a[prior],
            a_a[prior],
            h_b[prior],
            a_b[prior],
            h_runs[prior],
            a_runs[prior],
            max_iter=max_iter,
            tol=tol,
        )
        out_home[current] = np.exp(
            (1.0 - weight) * np.log(h_a[current]) + weight * np.log(h_b[current])
        )
        out_away[current] = np.exp(
            (1.0 - weight) * np.log(a_a[current]) + weight * np.log(a_b[current])
        )
        weights[current] = weight
        eligible[current] = True

    return PrequentialRegimeBlendResult(
        home_mu=out_home,
        away_mu=out_away,
        weights=weights,
        prior_counts=prior_counts,
        eligible=eligible,
        min_prior=min_prior,
    )
