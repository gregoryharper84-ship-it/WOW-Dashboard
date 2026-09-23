import numpy as np

from v17.mlb_centered_calibration_challenger import (
    evaluate_centered_calibration,
    fit_centered_logit_calibration,
)


def _underconfident_sample(n=600):
    p = np.linspace(0.40, 0.60, n)
    logit = np.log(p / (1 - p))
    q = 1 / (1 + np.exp(-2.4 * logit))
    y = np.asarray([((i * 37) % 1000) / 1000 < q[i] for i in range(n)], dtype=int)
    return p, y


def test_centered_fit_increases_resolution_and_keeps_half_fixed():
    p, y = _underconfident_sample()
    cal = fit_centered_logit_calibration(p, y)
    mapped = cal.transform(np.asarray([0.45, 0.5, 0.55]))
    assert cal.slope > 1.0
    assert mapped[0] < 0.45
    assert mapped[1] == 0.5
    assert mapped[2] > 0.55
    assert cal.can_execute is False
    assert cal.probability_publishable is False
    assert cal.automatic_promotion is False


def test_centered_fit_never_flips_raw_selected_side():
    p, y = _underconfident_sample()
    cal = fit_centered_logit_calibration(p, y)
    result = evaluate_centered_calibration(cal, p, y)
    assert result.side_flips_from_raw == 0
    assert result.can_execute is False
    assert result.probability_publishable is False
    assert result.automatic_promotion is False
