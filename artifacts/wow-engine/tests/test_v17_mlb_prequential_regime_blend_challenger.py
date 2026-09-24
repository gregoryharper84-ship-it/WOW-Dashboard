import numpy as np
import pytest

from v17.mlb_prequential_regime_blend_challenger import (
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    rolling_regime_blend,
)


def _sample(days=14, rows_per_day=8, seed=23):
    rng = np.random.default_rng(seed)
    n = days * rows_per_day
    dates = np.repeat(
        np.arange(np.datetime64("2026-08-01"), np.datetime64("2026-08-01") + days),
        rows_per_day,
    )
    home_a = np.exp(rng.normal(np.log(4.2), 0.08, n))
    away_a = np.exp(rng.normal(np.log(4.0), 0.08, n))
    home_b = home_a * np.exp(rng.normal(0.08, 0.03, n))
    away_b = away_a * np.exp(rng.normal(-0.06, 0.03, n))
    true_home = np.sqrt(home_a * home_b)
    true_away = np.sqrt(away_a * away_b)
    home_runs = rng.poisson(true_home)
    away_runs = rng.poisson(true_away)
    return dates, home_a, away_a, home_b, away_b, home_runs, away_runs


def test_regime_blend_is_research_only_and_convex_in_log_mu():
    sample = _sample()
    result = rolling_regime_blend(*sample, min_prior=40)

    assert result.can_execute is False
    assert result.probability_publishable is False
    assert result.automatic_promotion is False
    assert CAN_EXECUTE is False
    assert PROBABILITY_PUBLISHABLE is False
    assert AUTOMATIC_PROMOTION is False

    eligible = result.eligible
    assert np.any(eligible)
    assert np.all((result.weights[eligible] >= 0.0) & (result.weights[eligible] <= 1.0))

    _, home_a, away_a, home_b, away_b, *_ = sample
    assert np.all(result.home_mu[eligible] >= np.minimum(home_a, home_b)[eligible] - 1e-12)
    assert np.all(result.home_mu[eligible] <= np.maximum(home_a, home_b)[eligible] + 1e-12)
    assert np.all(result.away_mu[eligible] >= np.minimum(away_a, away_b)[eligible] - 1e-12)
    assert np.all(result.away_mu[eligible] <= np.maximum(away_a, away_b)[eligible] + 1e-12)
    assert np.all(np.isnan(result.home_mu[~eligible]))
    assert np.all(np.isnan(result.away_mu[~eligible]))


def test_same_day_and_future_runs_cannot_change_current_blend():
    sample = list(_sample(days=16, rows_per_day=6))
    dates = sample[0]
    baseline = rolling_regime_blend(*sample, min_prior=36)

    target_date = np.datetime64("2026-08-10")
    changed = list(sample)
    changed[5] = sample[5].copy()
    changed[6] = sample[6].copy()
    changed[5][dates >= target_date] += 9
    changed[6][dates >= target_date] += 7
    altered = rolling_regime_blend(*changed, min_prior=36)

    through_target = dates <= target_date
    eligible = through_target & baseline.eligible
    assert np.any(eligible)
    assert np.allclose(baseline.weights[eligible], altered.weights[eligible], atol=0, rtol=0)
    assert np.allclose(baseline.home_mu[eligible], altered.home_mu[eligible], atol=0, rtol=0)
    assert np.allclose(baseline.away_mu[eligible], altered.away_mu[eligible], atol=0, rtol=0)


def test_regime_blend_warmup_and_fail_closed_validation():
    sample = _sample(days=8, rows_per_day=5)
    result = rolling_regime_blend(*sample, min_prior=30)
    dates = sample[0]
    assert np.all(~result.eligible[dates <= np.datetime64("2026-08-06")])

    with pytest.raises(ValueError, match="MLB_REGIME_BLEND_MIN_PRIOR_TOO_SMALL"):
        rolling_regime_blend(*sample, min_prior=29)

    identical = list(sample)
    identical[3] = identical[1].copy()
    identical[4] = identical[2].copy()
    with pytest.raises(ValueError, match="MLB_REGIME_BLEND_SPECIALISTS_NOT_DISTINCT"):
        rolling_regime_blend(*identical, min_prior=30)
