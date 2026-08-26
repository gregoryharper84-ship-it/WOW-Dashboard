"""
calibration.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.4
+ ratified PREDICTIVE_BOUNDS_V1 amendment (Step 3d re-review, 2026-08-26)

Three-stage calibration ladder:
    N < 200   -> Phase A: conservative empirical-Bayes shrinkage
    N >= 200  -> Phase B: Platt scaling, time-aware out-of-fold
    N >= 500  -> Phase C: isotonic candidate, promoted only if it beats
                 Platt on Brier AND (log loss or non-worse) AND ECE

Phase A output is explicitly NOT evidence of proven calibration:
MONEY_QUALIFIED / FINAL_APPROVED are prohibited while
calibration_status == PRECALIBRATION_SHRINKAGE, regardless of how
strong the underlying confidence lane looks.

Phase B/C per-candidate predictive bounds (PREDICTIVE_BOUNDS_V1) were
ratified as a narrow amendment after the Step 3d review found the
original implementation had no bounds method for these phases at all
(see PredictiveBoundsNotRatifiedError's history in git log — replaced by
compute_predictive_bounds() below now that a method is ratified).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence
import math
import numpy as np

PHASE_B_MIN_N = 200
PHASE_C_MIN_N = 500
PHASE_C_MIN_PER_REGION = 30

SHRINKAGE_K = 25.0  # lambda = n_eff / (n_eff + 25)

PREDICTIVE_BOUNDS_METHOD_VERSION = "PREDICTIVE_BOUNDS_V1"
MIN_BOUNDS_BOOTSTRAP_REALIZATIONS = 2000
DEFAULT_MAX_FIT_FAILURE_RATE = 0.05


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


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


class ModelCalibrationUnavailableError(Exception):
    """Raised when a Phase B/C per-candidate predictive-bounds computation
    (compute_predictive_bounds, PREDICTIVE_BOUNDS_V1) cannot produce a
    governed result, per any of the ratified failure conditions: fewer
    than MIN_BOUNDS_BOOTSTRAP_REALIZATIONS valid realizations, a
    calibrator fit-failure rate above tolerance, non-finite or
    order-violating bounds, no eligible historical calibration rows
    before candidate_as_of, or a cohort/method mismatch on the active
    calibrator record. Blocks publication — callers must record
    MODEL_CALIBRATION_UNAVAILABLE, never substitute a partial or
    fabricated interval."""
    pass


@dataclass
class HistoricalCalibrationRow:
    """One verified, settled row from the calibrator's training cohort —
    the input to compute_predictive_bounds()'s bootstrap resampling."""
    raw_probability: float
    outcome: int
    timestamp: str  # ISO 8601


@dataclass
class PredictiveBounds:
    lower_bound: float
    calibrated_probability: float
    upper_bound: float
    realizations_used: int
    bounds_method_version: str


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
    timestamps: Sequence[str],
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

    `timestamps` (ISO 8601, same length/order as raw_probs/outcomes) are
    required and cross-checked against every fold split: fold IDs alone
    are a caller's *claim* about chronology, not proof of it. For every
    validation fold f, max(train_timestamp) must be strictly before
    min(validation_timestamp) — a fold split whose IDs look time-ordered
    but whose actual timestamps aren't raises ValueError instead of
    silently leaking future data into training.

    Returns a PlattFitOutcome; result is None if N < 200, if fewer than
    6 folds are present, or if promotion criteria against a supplied
    baseline are not met. `fold_train_audit` is returned so callers/tests
    can verify no future data leaked into any fold's training set.
    """
    n = len(raw_probs)
    if n < PHASE_B_MIN_N:
        raise ValueError(f"Phase B requires >= {PHASE_B_MIN_N} verified settled rows; got {n}")
    if len(timestamps) != n:
        raise ValueError("timestamps must be the same length as raw_probs/outcomes")

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
    ts = np.array([_parse_ts(t) for t in timestamps])

    oof_pred = np.full_like(raw, np.nan)
    fold_train_audit: dict[int, list[int]] = {}

    for f in unique_folds[1:]:  # fold 0 excluded — no earlier data exists
        train_folds = [tf for tf in unique_folds if tf < f]
        train_mask = np.isin(folds, train_folds)
        test_mask = folds == f
        _assert_fold_chronology(f, ts[train_mask], ts[test_mask])
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
        # Cohort-level fit outcome only -- NOT a per-candidate published
        # result. Per-candidate calibrated_probability + bounds come from
        # compute_predictive_bounds() (PREDICTIVE_BOUNDS_V1), using these
        # `coefficients` as the full-data fitted calibrator.
        calibrated_probability=float("nan"),
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
    timestamps: Sequence[str],
) -> IsotonicFitOutcome:
    """Real isotonic regression fit/scored with the same walk-forward
    discipline as Phase B (no future-fold leakage), including the same
    timestamp-verified chronology check -- see phase_b_platt's docstring."""
    from sklearn.isotonic import IsotonicRegression

    raw = np.asarray(raw_probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    folds = np.asarray(fold_assignments)
    unique_folds = sorted(set(folds.tolist()))
    if unique_folds != list(range(len(unique_folds))) or len(unique_folds) < 6:
        raise ValueError("Phase C requires the same >=6 contiguous time-ordered folds as Phase B")
    if len(timestamps) != len(raw_probs):
        raise ValueError("timestamps must be the same length as raw_probs/outcomes")
    ts = np.array([_parse_ts(t) for t in timestamps])

    oof_pred = np.full_like(raw, np.nan)
    for f in unique_folds[1:]:
        train_folds = [tf for tf in unique_folds if tf < f]
        train_mask = np.isin(folds, train_folds)
        test_mask = folds == f
        _assert_fold_chronology(f, ts[train_mask], ts[test_mask])
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


def compute_predictive_bounds(
    *,
    method: str,  # CalibrationStatus.PLATT_TIME_SPLIT_V1 | ISOTONIC_V1
    historical_rows: Sequence[HistoricalCalibrationRow],
    candidate_as_of: Optional[str],
    candidate_raw_probability_sampler: Callable[[np.random.Generator], float],
    full_data_calibrated_probability: float,
    rng_seed: int,
    bootstrap_realizations: int = MIN_BOUNDS_BOOTSTRAP_REALIZATIONS,
    max_fit_failure_rate: float = DEFAULT_MAX_FIT_FAILURE_RATE,
) -> PredictiveBounds:
    """
    Ratified PREDICTIVE_BOUNDS_V1 (WOW-PATCH-2026-08-26 v2, narrow
    amendment, Step 3d re-review 2026-08-26). For every publishable Phase
    B/C candidate:

    1. Use only historical calibration rows with timestamp < candidate_as_of.
    2. Generate >= 2,000 bootstrap realizations.
    3. Per realization: resample the eligible historical cohort (with
       replacement), re-sort the resample chronologically, refit the
       active calibrator (Platt or isotonic) on it, draw a candidate
       raw-probability realization from the sport-specific
       simulation/bootstrap path, and apply the refit calibrator to it.
    4. The full-data fitted calibrator (supplied by the caller as
       `full_data_calibrated_probability`) is the published point estimate.
    5. q10/q90 = 10th/90th percentile of the bootstrap calibrated-
       probability distribution.
    6. lower_bound = min(q10, calibrated_probability); upper_bound =
       max(q90, calibrated_probability) -- widened to guarantee
       lower <= calibrated_probability <= upper.

    Any of the ratified failure conditions (too few valid realizations,
    a future-dated row reaching the resample, an excessive calibrator
    fit-failure rate, non-finite/order-violating bounds, no eligible
    historical rows) raises ModelCalibrationUnavailableError -- callers
    must record MODEL_CALIBRATION_UNAVAILABLE and block publication,
    never substitute a partial interval.
    """
    if bootstrap_realizations < MIN_BOUNDS_BOOTSTRAP_REALIZATIONS:
        raise ValueError(
            f"Predictive bounds require >= {MIN_BOUNDS_BOOTSTRAP_REALIZATIONS} "
            f"bootstrap realizations; got {bootstrap_realizations}"
        )
    if candidate_as_of is None:
        raise ModelCalibrationUnavailableError(
            "candidate_as_of (scoring time) is required to select eligible "
            "historical calibration rows and cannot be omitted"
        )

    as_of_dt = _parse_ts(candidate_as_of)
    # Step 1: filter to strictly-past rows. Every realization resamples
    # only from `eligible`, so no future-dated row can structurally reach
    # a refit -- this is what "no future-dated calibration row used"
    # (a named failure condition) is enforced by construction, not by a
    # post-hoc check.
    eligible = [r for r in historical_rows if _parse_ts(r.timestamp) < as_of_dt]
    if not eligible:
        raise ModelCalibrationUnavailableError(
            f"no historical calibration rows with timestamp before "
            f"candidate_as_of={candidate_as_of!r}"
        )

    rng = np.random.default_rng(rng_seed)
    n = len(eligible)
    calibrated_realizations: list[float] = []
    fit_failures = 0

    for _ in range(bootstrap_realizations):
        idx = rng.integers(0, n, size=n)
        # Step 3b: reconstruct chronological order in the resample before
        # refitting.
        resampled = sorted((eligible[i] for i in idx), key=lambda r: r.timestamp)
        raw = np.array([r.raw_probability for r in resampled])
        y = np.array([r.outcome for r in resampled])

        try:
            if method == CalibrationStatus.PLATT_TIME_SPLIT_V1:
                a, b = _fit_platt_1d(raw, y)
                candidate_raw = candidate_raw_probability_sampler(rng)
                calibrated = PlattCoefficients(a=a, b=b).apply(candidate_raw)
            elif method == CalibrationStatus.ISOTONIC_V1:
                from sklearn.isotonic import IsotonicRegression
                model = IsotonicRegression(out_of_bounds="clip", y_min=1e-9, y_max=1 - 1e-9)
                model.fit(raw, y)
                candidate_raw = candidate_raw_probability_sampler(rng)
                calibrated = float(model.predict([candidate_raw])[0])
            else:
                raise ValueError(f"unrecognized calibration method: {method!r}")
        except Exception:
            fit_failures += 1
            continue

        if not math.isfinite(calibrated):
            fit_failures += 1
            continue
        calibrated_realizations.append(calibrated)

    realizations_used = len(calibrated_realizations)
    fit_failure_rate = fit_failures / bootstrap_realizations

    if realizations_used < MIN_BOUNDS_BOOTSTRAP_REALIZATIONS:
        raise ModelCalibrationUnavailableError(
            f"only {realizations_used} valid bootstrap realizations produced "
            f"(< {MIN_BOUNDS_BOOTSTRAP_REALIZATIONS} required)"
        )
    if fit_failure_rate > max_fit_failure_rate:
        raise ModelCalibrationUnavailableError(
            f"calibrator fit failure rate {fit_failure_rate:.2%} exceeds "
            f"tolerance {max_fit_failure_rate:.2%}"
        )

    q10 = float(np.percentile(calibrated_realizations, 10))
    q90 = float(np.percentile(calibrated_realizations, 90))
    lower_bound = min(q10, full_data_calibrated_probability)
    upper_bound = max(q90, full_data_calibrated_probability)

    if not (math.isfinite(lower_bound) and math.isfinite(upper_bound)):
        raise ModelCalibrationUnavailableError("non-finite predictive bounds")
    if not (0 < lower_bound <= full_data_calibrated_probability <= upper_bound < 1):
        raise ModelCalibrationUnavailableError(
            f"bounds ordering violated: 0 < {lower_bound} <= "
            f"{full_data_calibrated_probability} <= {upper_bound} < 1"
        )

    return PredictiveBounds(
        lower_bound=lower_bound,
        calibrated_probability=full_data_calibrated_probability,
        upper_bound=upper_bound,
        realizations_used=realizations_used,
        bounds_method_version=PREDICTIVE_BOUNDS_METHOD_VERSION,
    )


# --- internal helpers -------------------------------------------------

def _safe_logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _assert_fold_chronology(fold: int, train_ts: np.ndarray, test_ts: np.ndarray) -> None:
    """Step 3d review constraint: fold IDs alone are a claim about
    chronology, not proof. Verify it directly against timestamps for
    every split with both train and test rows."""
    if train_ts.size == 0 or test_ts.size == 0:
        return
    max_train_ts = train_ts.max()
    min_test_ts = test_ts.min()
    if not (max_train_ts < min_test_ts):
        raise ValueError(
            f"fold {fold}: max(train_timestamp)={max_train_ts.isoformat()} is not "
            f"strictly before min(validation_timestamp)={min_test_ts.isoformat()} -- "
            f"fold_assignments do not match actual chronological order"
        )


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
