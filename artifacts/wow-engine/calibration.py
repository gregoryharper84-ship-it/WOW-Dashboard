"""
calibration.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.4

Three-stage calibration ladder:
    N < 200   -> Phase A: conservative empirical-Bayes shrinkage
    N >= 200  -> Phase B: Platt scaling, time-aware out-of-fold
    N >= 500  -> Phase C: isotonic candidate, promoted only if it beats
                 Platt on Brier AND (log loss or non-worse) AND ECE

Phase A output is explicitly NOT evidence of proven calibration:
MONEY_QUALIFIED / FINAL_APPROVED are prohibited while
calibration_status == PRECALIBRATION_SHRINKAGE, regardless of how
strong the underlying confidence lane looks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence
import math
import numpy as np

PHASE_B_MIN_N = 200
PHASE_C_MIN_N = 500
PHASE_C_MIN_PER_REGION = 30

SHRINKAGE_K = 25.0  # lambda = n_eff / (n_eff + 25)


class CalibrationStatus:
    PRECALIBRATION_SHRINKAGE = "PRECALIBRATION_SHRINKAGE"
    PLATT_TIME_SPLIT_V1 = "PLATT_TIME_SPLIT_V1"
    ISOTONIC_V1 = "ISOTONIC_V1"


class MissingResamplerError(Exception):
    """Raised when Phase A shrinkage is requested without a valid
    bootstrap resample_fn. The ratified methodology requires 10th/90th
    percentile bounds from >=2,000 real bootstrap realizations — a
    fabricated symmetric interval is not an acceptable substitute and
    must block publication instead."""
    pass


class PredictiveBoundsNotRatifiedError(Exception):
    """Raised when a caller asks for a per-candidate Phase B/C published
    probability. WOW-PATCH-2026-08-26 v2 Section 8B.4 ratifies a bounds
    method for Phase A (10th/90th percentile bootstrap of the shrinkage
    transform) and cohort-level fit metrics (Brier/log loss/ECE/bias) for
    Phase B/C promotion, but specifies no per-candidate predictive-bounds
    method for Phase B/C itself. `PlattCoefficients.apply()` /
    `IsotonicRegression.predict()` give a point estimate only.

    Inventing a bounds method here would repeat, at the calibration layer,
    the same unauthorized-scoring-shortcut problem this patch exists to
    avoid (see the patch's ORIGIN note and its "METHODOLOGY DECISIONS
    REQUIRED — ChatGPT, not Claude, to specify" section). This blocks
    publication instead of fabricating an interval; a governed Phase B/C
    row requires a ratified bounds method to be specified upstream first."""
    pass


@dataclass
class CalibrationResult:
    calibration_status: str
    calibration_method: str
    calibrated_probability: float
    lower_bound: float
    upper_bound: float
    money_qualified_allowed: bool
    final_approved_allowed: bool


def phase_a_shrinkage(
    p_raw: float,
    n_eff: float,
    rng_seed: int,
    bootstrap_realizations: int = 2000,
    resample_fn=None,
) -> CalibrationResult:
    """
    p_shrunk = 0.5 + lambda * (p_raw - 0.5)
    lambda = n_eff / (n_eff + 25)

    n_eff must reflect model evidence (e.g. effective regime sample size
    / simulation stability), not raw L10 game count — the caller is
    responsible for computing n_eff correctly; this function only applies
    the shrinkage contract.

    Bounds: 10th/90th percentile of a bootstrap/resampled distribution.
    `resample_fn(rng, n) -> array[float]` must be supplied by the caller
    (e.g. resampling the Monte Carlo draws already generated) — this
    function does not invent a resampling distribution.
    """
    if bootstrap_realizations < 2000:
        raise ValueError("Phase A requires >= 2,000 bootstrap realizations (>=10,000 preferred offline)")

    lam = n_eff / (n_eff + SHRINKAGE_K)
    p_shrunk = 0.5 + lam * (p_raw - 0.5)

    if resample_fn is None:
        # No fabricated interval. A missing resampler blocks publication —
        # callers must catch this and set probability_publishable=False /
        # record the gap, exactly like MissingRegimeDataError.
        raise MissingResamplerError(
            "Phase A shrinkage requires a real bootstrap resample_fn to "
            "produce 10th/90th percentile bounds from >=2,000 realizations. "
            "Refusing to fabricate a fallback interval — this blocks "
            "probability publication for this candidate."
        )

    rng = np.random.default_rng(rng_seed)
    samples = resample_fn(rng, bootstrap_realizations)
    lower = float(np.percentile(samples, 10))
    upper = float(np.percentile(samples, 90))

    return CalibrationResult(
        calibration_status=CalibrationStatus.PRECALIBRATION_SHRINKAGE,
        calibration_method="CONSERVATIVE_EMPIRICAL_BAYES_SHRINKAGE_V1",
        calibrated_probability=p_shrunk,
        lower_bound=lower,
        upper_bound=upper,
        money_qualified_allowed=False,   # hard prohibition, 8B.4
        final_approved_allowed=False,    # hard prohibition, 8B.4
    )


@dataclass
class PlattFitMetrics:
    brier: float
    log_loss: float
    ece: float
    calibration_bias: float


@dataclass
class PlattCoefficients:
    a: float
    b: float

    def apply(self, raw_probability: float) -> float:
        p = max(min(raw_probability, 1 - 1e-9), 1e-9)
        logit = math.log(p / (1 - p))
        return _sigmoid(self.a + self.b * logit)


@dataclass
class PlattFitOutcome:
    result: Optional[CalibrationResult]
    metrics: PlattFitMetrics
    coefficients: PlattCoefficients
    fold_train_audit: dict[int, list[int]]  # test_fold -> training fold ids actually used


def phase_b_platt(
    raw_probs: Sequence[float],
    outcomes: Sequence[int],
    fold_assignments: Sequence[int],
    baseline_metrics: Optional[PlattFitMetrics] = None,
) -> PlattFitOutcome:
    """
    True walk-forward (expanding-window) out-of-fold Platt scaling.
    `fold_assignments` must be integers in strictly time-ascending order
    (fold 0 = earliest data, fold k = latest). Each test fold f is scored
    ONLY by a model trained on folds strictly earlier than f (0..f-1) —
    never on later or same-fold data. Fold 0 has no valid training data
    and is excluded from out-of-fold evaluation, so >=6 distinct folds
    are required to get >=5 evaluable OOF folds.

    Returns a PlattFitOutcome; result is None if N < 200, if fewer than
    6 folds are present, or if promotion criteria against a supplied
    baseline are not met. `fold_train_audit` is returned so callers/tests
    can verify no future data leaked into any fold's training set.
    """
    n = len(raw_probs)
    if n < PHASE_B_MIN_N:
        raise ValueError(f"Phase B requires >= {PHASE_B_MIN_N} verified settled rows; got {n}")

    folds = np.asarray(fold_assignments)
    unique_folds = sorted(set(folds.tolist()))
    if len(unique_folds) < 6:
        raise ValueError(
            "Phase B requires >=6 time-ordered folds (fold 0 is a training-only "
            "burn-in with no OOF evaluation, leaving >=5 evaluable folds)"
        )
    if unique_folds != list(range(len(unique_folds))):
        raise ValueError("fold_assignments must be contiguous integers 0..k-1 in time order")

    raw = np.asarray(raw_probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)

    oof_pred = np.full_like(raw, np.nan)
    fold_train_audit: dict[int, list[int]] = {}

    for f in unique_folds[1:]:  # fold 0 excluded — no earlier data exists
        train_folds = [tf for tf in unique_folds if tf < f]
        train_mask = np.isin(folds, train_folds)
        test_mask = folds == f
        a, b = _fit_platt_1d(raw[train_mask], y[train_mask])
        logit_raw = _safe_logit(raw[test_mask])
        oof_pred[test_mask] = _sigmoid(a + b * logit_raw)
        fold_train_audit[int(f)] = train_folds

    evaluated_mask = ~np.isnan(oof_pred)
    metrics = _compute_metrics(oof_pred[evaluated_mask], y[evaluated_mask])

    a_full, b_full = _fit_platt_1d(raw, y)
    coefficients = PlattCoefficients(a=a_full, b=b_full)

    if baseline_metrics is not None:
        improved = (metrics.brier < baseline_metrics.brier) or (metrics.log_loss < baseline_metrics.log_loss)
        not_worse_ece = metrics.ece <= baseline_metrics.ece * 1.02
        if not (improved and not_worse_ece):
            return PlattFitOutcome(result=None, metrics=metrics, coefficients=coefficients, fold_train_audit=fold_train_audit)

    result = CalibrationResult(
        calibration_status=CalibrationStatus.PLATT_TIME_SPLIT_V1,
        calibration_method="PLATT_TIME_SPLIT_V1",
        calibrated_probability=float("nan"),  # per-candidate: coefficients.apply(raw_probability)
        lower_bound=float("nan"),
        upper_bound=float("nan"),
        money_qualified_allowed=True,
        final_approved_allowed=True,
    )
    return PlattFitOutcome(result=result, metrics=metrics, coefficients=coefficients, fold_train_audit=fold_train_audit)


def phase_c_isotonic_eligible(n: int, per_region_counts: Sequence[int]) -> bool:
    # Bug fix: all([]) == True in Python, which incorrectly made an empty
    # per_region_counts list "eligible". Require at least one populated
    # region explicitly.
    if n < PHASE_C_MIN_N:
        return False
    if len(per_region_counts) == 0:
        return False
    return all(c >= PHASE_C_MIN_PER_REGION for c in per_region_counts)


@dataclass
class IsotonicFitOutcome:
    metrics: PlattFitMetrics
    model: object  # sklearn IsotonicRegression, kept opaque here


def phase_c_fit_isotonic(
    raw_probs: Sequence[float],
    outcomes: Sequence[int],
    fold_assignments: Sequence[int],
) -> IsotonicFitOutcome:
    """Real isotonic regression fit/scored with the same walk-forward
    discipline as Phase B (no future-fold leakage)."""
    from sklearn.isotonic import IsotonicRegression

    raw = np.asarray(raw_probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    folds = np.asarray(fold_assignments)
    unique_folds = sorted(set(folds.tolist()))
    if unique_folds != list(range(len(unique_folds))) or len(unique_folds) < 6:
        raise ValueError("Phase C requires the same >=6 contiguous time-ordered folds as Phase B")

    oof_pred = np.full_like(raw, np.nan)
    for f in unique_folds[1:]:
        train_folds = [tf for tf in unique_folds if tf < f]
        train_mask = np.isin(folds, train_folds)
        test_mask = folds == f
        iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-9, y_max=1 - 1e-9)
        iso.fit(raw[train_mask], y[train_mask])
        oof_pred[test_mask] = iso.predict(raw[test_mask])

    evaluated_mask = ~np.isnan(oof_pred)
    metrics = _compute_metrics(oof_pred[evaluated_mask], y[evaluated_mask])

    full_model = IsotonicRegression(out_of_bounds="clip", y_min=1e-9, y_max=1 - 1e-9)
    full_model.fit(raw, y)
    return IsotonicFitOutcome(metrics=metrics, model=full_model)


def phase_c_promote(isotonic_metrics: PlattFitMetrics, platt_metrics: PlattFitMetrics) -> bool:
    """Promotion condition: lower Brier AND lower-or-non-worse log loss
    AND lower ECE. Not automatic just because N crossed 500."""
    return (
        isotonic_metrics.brier < platt_metrics.brier
        and isotonic_metrics.log_loss <= platt_metrics.log_loss
        and isotonic_metrics.ece < platt_metrics.ece
    )


# --- internal helpers -------------------------------------------------

def _safe_logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _fit_platt_1d(raw: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Simple 1D logistic regression (Platt scaling) via Newton's method
    on logit(raw) as the single feature. No external ML dependency
    required."""
    x = _safe_logit(raw)
    a, b = 0.0, 1.0
    for _ in range(50):
        z = a + b * x
        p = _sigmoid(z)
        grad_a = np.sum(p - y)
        grad_b = np.sum((p - y) * x)
        w = p * (1 - p) + 1e-9
        h_aa = np.sum(w)
        h_bb = np.sum(w * x * x)
        h_ab = np.sum(w * x)
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (h_bb * grad_a - h_ab * grad_b) / det
        db = (h_aa * grad_b - h_ab * grad_a) / det
        a -= da
        b -= db
        if abs(da) < 1e-8 and abs(db) < 1e-8:
            break
    return a, b


def _compute_metrics(pred: np.ndarray, y: np.ndarray, n_bins: int = 10) -> PlattFitMetrics:
    pred_c = np.clip(pred, 1e-9, 1 - 1e-9)
    brier = float(np.mean((pred_c - y) ** 2))
    log_loss = float(-np.mean(y * np.log(pred_c) + (1 - y) * np.log(1 - pred_c)))

    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    n = len(pred_c)
    for i in range(n_bins):
        mask = (pred_c >= bins[i]) & (pred_c < bins[i + 1] if i < n_bins - 1 else pred_c <= bins[i + 1])
        if not np.any(mask):
            continue
        conf = float(np.mean(pred_c[mask]))
        acc = float(np.mean(y[mask]))
        ece += (np.sum(mask) / n) * abs(conf - acc)

    bias = float(np.mean(pred_c) - np.mean(y))
    return PlattFitMetrics(brier=brier, log_loss=log_loss, ece=ece, calibration_bias=bias)
