import numpy as np
import pytest

from v17.mlb_prequential_run_contrast_challenger import (
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    rolling_run_contrast,
)


def _sample(days=14, rows_per_day=8, seed=23):
    rng = np.random.default_rng(seed)
    dates = np.repeat(
        np.arange(np.datetime64("2026-08-01"), np.datetime64("2026-08-01") + days),
        rows_per_day,
    )
    n = len(dates)
    center = rng.normal(np.log(4.4), 0.08, n)
    contrast = rng.normal(0, 0.08, n)
    home_mu = np.exp(center + contrast)
    away_mu = np.exp(center - contrast)
    true_scale = 2.0
    home_runs = rng.poisson(np.exp(center + true_scale * contrast))
    away_runs = rng.poisson(np.exp(center - true_scale * contrast))
    return dates, home_mu, away_mu, home_runs, away_runs


def test_run_contrast_is_research_only_and_preserves_center_and_order():
    dates, hm, am, hr, ar = _sample()
    result = rolling_run_contrast(dates, hm, am, hr, ar, min_prior=40)
    eligible = result.eligible

    assert np.any(eligible)
    assert result.can_execute is False
    assert result.probability_publishable is False
    assert result.automatic_promotion is False
    assert CAN_EXECUTE is False
    assert PROBABILITY_PUBLISHABLE is False
    assert AUTOMATIC_PROMOTION is False
    assert np.all(result.contrast_scales[eligible] > 0)

    original_center = np.sqrt(hm[eligible] * am[eligible])
    adjusted_center = np.sqrt(result.home_mu[eligible] * result.away_mu[eligible])
    assert np.allclose(original_center, adjusted_center, atol=1e-12, rtol=1e-12)
    assert np.all((result.home_mu[eligible] >= result.away_mu[eligible]) == (hm[eligible] >= am[eligible]))


def test_same_day_and_future_runs_cannot_change_current_adjusted_means():
    dates, hm, am, hr, ar = _sample(days=16, rows_per_day=7)
    baseline = rolling_run_contrast(dates, hm, am, hr, ar, min_prior=35)

    target_date = np.datetime64("2026-08-10")
    changed_hr = hr.copy()
    changed_ar = ar.copy()
    changed_hr[dates >= target_date] += 8
    changed_ar[dates >= target_date] += 5
    altered = rolling_run_contrast(dates, hm, am, changed_hr, changed_ar, min_prior=35)

    eligible = (dates <= target_date) & baseline.eligible
    assert np.any(eligible)
    assert np.allclose(baseline.home_mu[eligible], altered.home_mu[eligible], atol=0, rtol=0)
    assert np.allclose(baseline.away_mu[eligible], altered.away_mu[eligible], atol=0, rtol=0)
    assert np.allclose(baseline.contrast_scales[eligible], altered.contrast_scales[eligible], atol=0, rtol=0)


def test_run_contrast_warmup_and_bounds_fail_closed():
    dates, hm, am, hr, ar = _sample(days=8, rows_per_day=5)
    result = rolling_run_contrast(dates, hm, am, hr, ar, min_prior=30)
    assert np.all(np.isnan(result.home_mu[~result.eligible]))
    assert np.all(np.isnan(result.away_mu[~result.eligible]))

    with pytest.raises(ValueError, match="MLB_RUN_CONTRAST_MIN_PRIOR_TOO_SMALL"):
        rolling_run_contrast(dates, hm, am, hr, ar, min_prior=29)
    with pytest.raises(ValueError, match="MLB_RUN_CONTRAST_BOUNDS_INVALID"):
        rolling_run_contrast(dates, hm, am, hr, ar, lower=1.0, upper=1.0)
