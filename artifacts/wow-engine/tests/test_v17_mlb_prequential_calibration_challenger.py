import numpy as np
import pytest

from v17.mlb_prequential_calibration_challenger import (
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    rolling_centered_predictions,
)


def _sample(days=12, rows_per_day=10, seed=17):
    rng = np.random.default_rng(seed)
    n = days * rows_per_day
    dates = np.repeat(
        np.arange(np.datetime64("2026-08-01"), np.datetime64("2026-08-01") + days),
        rows_per_day,
    )
    raw_logit = rng.normal(0, 0.20, n)
    p = 1 / (1 + np.exp(-raw_logit))
    true_p = 1 / (1 + np.exp(-(2.5 * raw_logit)))
    y = rng.binomial(1, true_p)
    return dates, p, y


def test_prequential_centered_calibration_is_research_only_and_side_preserving():
    dates, p, y = _sample()
    result = rolling_centered_predictions(dates, p, y, min_prior=40)

    assert result.can_execute is False
    assert result.probability_publishable is False
    assert result.automatic_promotion is False
    assert CAN_EXECUTE is False
    assert PROBABILITY_PUBLISHABLE is False
    assert AUTOMATIC_PROMOTION is False

    eligible = result.eligible
    assert np.sum(eligible) > 0
    assert np.all(result.slopes[eligible] > 0)
    assert np.all((result.probabilities[eligible] >= 0.5) == (p[eligible] >= 0.5))
    assert np.all(np.isnan(result.probabilities[~eligible]))


def test_same_day_and_future_outcomes_cannot_change_current_predictions():
    dates, p, y = _sample(days=14, rows_per_day=8)
    baseline = rolling_centered_predictions(dates, p, y, min_prior=40)

    target_date = np.datetime64("2026-08-09")
    altered_y = y.copy()
    altered_y[dates >= target_date] = 1 - altered_y[dates >= target_date]
    altered = rolling_centered_predictions(dates, p, altered_y, min_prior=40)

    through_target = dates <= target_date
    eligible = through_target & baseline.eligible
    assert np.any(eligible)
    assert np.allclose(
        baseline.probabilities[eligible],
        altered.probabilities[eligible],
        atol=0,
        rtol=0,
    )
    assert np.allclose(baseline.slopes[eligible], altered.slopes[eligible], atol=0, rtol=0)


def test_prequential_warmup_is_explicit_and_minimum_is_fail_closed():
    dates, p, y = _sample(days=8, rows_per_day=5)
    result = rolling_centered_predictions(dates, p, y, min_prior=30)
    assert np.all(~result.eligible[dates <= np.datetime64("2026-08-06")])

    with pytest.raises(ValueError, match="MLB_PREQUENTIAL_MIN_PRIOR_TOO_SMALL"):
        rolling_centered_predictions(dates, p, y, min_prior=29)
