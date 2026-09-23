import numpy as np
import pytest

from v17.mlb_forward_discrimination_challenger import (
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    PROBABILITY_PUBLISHABLE,
    ProbabilityMetrics,
    direct36_from_run_pair,
    fit_two_logit_stack,
    promotion_blockers,
)


def test_forward_run38_pair_reconstructs_historical_direct36_example_exactly():
    home = np.asarray([
        1, 6.8, 9.8, 1.4, 4.4, 5.8, 16, 3, 0.6, 1, 0.4, 1,
        2.6, 1, 1, 0.457627118644068, 0.120481927710843,
        0.072289156626506, 0, 167, 33, 9, 1, 7.2, 0.227272727272727,
        0.0909090909090909, 0.227272727272727, 0.0454545454545455,
        15, 22, 76, 0.710526315789474, 5, 76, 10.6, 5, 1, 5,
    ])
    away = np.asarray([
        0, 5.2, 9.2, 1, 5.2, 9.2, 14, 2.6, 1, 0.2, 0, 1,
        3.8, 0.8, 0.6, 2.49230769230769, 0.197530864197531,
        0.0740740740740741, 0.0123456790123457, 219, 46, 10, 1, 1.8,
        0.142857142857143, 0.0952380952380952, 0.142857142857143, 0,
        15, 21, 90, 0.566666666666667, 5, 90, 10.6, 5, 1, 5,
    ])
    expected = np.asarray([
        1.6, 0.6, 0.4, -0.8, -3.4, 2, 1.2, 0.4, -0.2, 0.8, 0.4,
        -0.4, 2.03468057366362, 0.0770489364866875, 0.00178491744756805,
        0.0123456790123457, 52, 13, 1, 0, -5.4, -0.0844155844155844,
        0.00432900432900433, -0.0844155844155844, -0.0454545454545455,
        0, -1, 14, -0.143859649122807, 0, 14, 10.6, 5, 0, 1, 5,
    ])
    actual = direct36_from_run_pair(home, away)
    assert np.allclose(actual, expected, atol=1e-12, rtol=0)


def test_forward_transform_fails_closed_on_orientation_or_park_mismatch():
    home = np.ones(38)
    away = np.zeros(38)
    home[34:36] = [9.2, 40]
    away[34:36] = [9.2, 40]
    direct36_from_run_pair(home, away)

    bad_orientation = away.copy()
    bad_orientation[0] = 1
    with pytest.raises(ValueError, match="MLB_FORWARD_RUN_PAIR_ORIENTATION_INVALID"):
        direct36_from_run_pair(home, bad_orientation)

    bad_park = away.copy()
    bad_park[34] = 9.3
    with pytest.raises(ValueError, match="MLB_FORWARD_PARK_CONTEXT_MISMATCH"):
        direct36_from_run_pair(home, bad_park)


def test_two_logit_stack_fit_is_research_only_and_recovers_both_signals():
    rng = np.random.default_rng(7)
    n = 2500
    run_logit = rng.normal(0, 0.35, n)
    direct_logit = rng.normal(0, 0.45, n)
    true_logit = 0.08 + 2.2 * run_logit + 0.9 * direct_logit
    true_p = 1 / (1 + np.exp(-true_logit))
    y = rng.binomial(1, true_p)
    run_p = 1 / (1 + np.exp(-run_logit))
    direct_p = 1 / (1 + np.exp(-direct_logit))

    fit = fit_two_logit_stack(run_p, direct_p, y, ridge=0.001)
    assert fit.fit_n == n
    assert fit.run_logit_weight > 1.0
    assert fit.direct_logit_weight > 0.25
    assert fit.can_execute is False
    assert fit.probability_publishable is False
    assert fit.automatic_promotion is False
    assert CAN_EXECUTE is False
    assert PROBABILITY_PUBLISHABLE is False
    assert AUTOMATIC_PROMOTION is False


def test_promotion_gate_rejects_better_calibration_when_auc_does_not_improve():
    incumbent = ProbabilityMetrics(
        n=90,
        brier=0.2447,
        log_loss=0.6825,
        selected_side_hit_rate=0.5333,
        ece=0.1545,
        max_bin_gap=0.3997,
        roc_auc=0.6645,
    )
    challenger = ProbabilityMetrics(
        n=90,
        brier=0.2353,
        log_loss=0.6628,
        selected_side_hit_rate=0.6444,
        ece=0.0975,
        max_bin_gap=0.1727,
        roc_auc=0.6512,
    )
    assert promotion_blockers(incumbent, challenger) == ("DISCRIMINATION_NOT_IMPROVED",)
