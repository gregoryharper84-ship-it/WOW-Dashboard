"""Research-only centered logit calibration challenger for WOW V17 MLB.

This calibrator changes probability resolution through a fitted logit slope while
leaving 0.5 fixed. Therefore it cannot flip the model-selected winner merely
because of a pooled home-prevalence intercept.

No production authority: automatic promotion, publication and execution remain
disabled.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
EPS = 1e-6


@dataclass(frozen=True)
class CenteredLogitCalibration:
    slope: float
    fit_n: int
    method: str = "CENTERED_LOGIT_SLOPE_V1"
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False

    def transform(self, probabilities):
        p = _probabilities(probabilities)
        logits = np.log(p / (1.0 - p))
        z = np.clip(self.slope * logits, -40.0, 40.0)
        return np.clip(1.0 / (1.0 + np.exp(-z)), EPS, 1.0 - EPS)


@dataclass(frozen=True)
class CenteredCalibrationEvaluation:
    n: int
    brier: float
    log_loss: float
    selected_side_hit_rate: float
    side_flips_from_raw: int
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False


def _probabilities(values):
    p = np.asarray(values, dtype=float)
    if p.ndim != 1 or len(p) == 0 or not np.all(np.isfinite(p)):
        raise ValueError("MLB_CENTERED_CALIBRATION_PROBABILITIES_INVALID")
    if np.any((p <= 0.0) | (p >= 1.0)):
        raise ValueError("MLB_CENTERED_CALIBRATION_PROBABILITY_DOMAIN_INVALID")
    return np.clip(p, EPS, 1.0 - EPS)


def _outcomes(values):
    y = np.asarray(values, dtype=int)
    if y.ndim != 1 or len(y) == 0 or not np.all(np.isin(y, [0, 1])):
        raise ValueError("MLB_CENTERED_CALIBRATION_OUTCOMES_INVALID")
    if len(np.unique(y)) < 2:
        raise ValueError("MLB_CENTERED_CALIBRATION_OUTCOME_CLASS_DEGENERATE")
    return y


def fit_centered_logit_calibration(probabilities, outcomes) -> CenteredLogitCalibration:
    """Fit a no-intercept logistic map on logit(p).

    With no intercept, p=0.5 maps exactly to 0.5 and the sign of logit(p) is
    preserved for every positive fitted slope. This is the intended challenger
    when the incumbent prevalence shift is observed to reverse marginal sides.
    """
    p = _probabilities(probabilities)
    y = _outcomes(outcomes)
    if len(p) != len(y):
        raise ValueError("MLB_CENTERED_CALIBRATION_LENGTH_MISMATCH")
    x = np.log(p / (1.0 - p)).reshape(-1, 1)
    model = LogisticRegression(
        fit_intercept=False,
        C=1e8,
        solver="lbfgs",
        max_iter=2000,
        random_state=0,
    )
    model.fit(x, y)
    slope = float(model.coef_[0, 0])
    if not np.isfinite(slope) or slope <= 0.0:
        raise ValueError("MLB_CENTERED_CALIBRATION_SLOPE_INVALID")
    return CenteredLogitCalibration(slope=slope, fit_n=len(p))


def evaluate_centered_calibration(calibration, probabilities, outcomes) -> CenteredCalibrationEvaluation:
    p = _probabilities(probabilities)
    y = _outcomes(outcomes)
    mapped = calibration.transform(p)
    raw_side = p >= 0.5
    mapped_side = mapped >= 0.5
    hit = np.where(mapped_side, y == 1, y == 0)
    return CenteredCalibrationEvaluation(
        n=len(p),
        brier=float(brier_score_loss(y, mapped)),
        log_loss=float(log_loss(y, mapped, labels=[0, 1])),
        selected_side_hit_rate=float(np.mean(hit)),
        side_flips_from_raw=int(np.sum(raw_side != mapped_side)),
    )
