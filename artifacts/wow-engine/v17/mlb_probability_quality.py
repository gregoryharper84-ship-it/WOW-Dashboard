"""Research-only MLB probability-quality challenger utilities for WOW V17.

This module must never act as production probability authority.  It exists to
measure the incumbent, fit temporal calibration challengers, canonicalize
immutable replay rows, and expose dominance diagnostics without changing P(win).

Permanent invariants:
- automatic promotion is disabled;
- probability publication is disabled;
- wager/order execution is disabled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
EPS = 1e-6


class ProbabilityQualityError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CalibrationRow:
    event_id: str
    event_time: str
    raw_home_probability: float
    home_win: bool


@dataclass(frozen=True)
class ImmutableGradeRow:
    official_event_id: str
    snapshot_time: str
    score_snapshot_id: str
    lineup_status_at_score: str
    selected_probability: float
    selected_won: bool
    selected_margin: int


@dataclass(frozen=True)
class CalibrationMetrics:
    n: int
    brier: float
    log_loss: float
    ece_equal_count: float
    max_equal_count_gap: float
    calibration_intercept: float
    calibration_slope: float
    roc_auc: float
    selected_side_hit_rate: float
    probability_stddev: float
    probability_p10: float
    probability_p90: float


@dataclass(frozen=True)
class CalibrationHealthPolicy:
    max_brier: float
    max_log_loss: float
    max_ece: float
    max_bin_gap: float
    max_abs_calibration_intercept: float
    min_calibration_slope: float
    max_calibration_slope: float
    min_n: int


@dataclass(frozen=True)
class CalibrationHealthAssessment:
    status: str
    blockers: tuple[str, ...]
    metrics: CalibrationMetrics
    probability_publishable: bool = False
    can_execute: bool = False


@dataclass(frozen=True)
class ChallengerMetrics:
    method: str
    split: str
    metrics: CalibrationMetrics


@dataclass(frozen=True)
class TemporalCalibrationChallenge:
    total_n: int
    fit_n: int
    selection_n: int
    untouched_test_n: int
    incumbent_method: str
    selected_challenger_method: str
    selection_metrics: tuple[ChallengerMetrics, ...]
    untouched_test_metrics: tuple[ChallengerMetrics, ...]
    test_used_for_selection: bool
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["selection_metrics"] = [asdict(item) for item in self.selection_metrics]
        payload["untouched_test_metrics"] = [asdict(item) for item in self.untouched_test_metrics]
        return payload


@dataclass(frozen=True)
class DominanceDiagnostic:
    regulation_win_probability: float
    tie_after_9_probability: float
    extra_inning_selected_win_probability: float | None
    outright_win_probability: float
    win_by_2_plus_probability: float
    win_by_4_plus_probability: float
    win_by_6_plus_probability: float
    expected_run_differential: float
    loss_by_4_plus_probability: float
    blowout_asymmetry_score: float
    probability_sum: float
    can_execute: bool = False


def _probability_array(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or len(arr) == 0 or not np.all(np.isfinite(arr)):
        raise ProbabilityQualityError("MLB_CALIBRATION_PROBABILITIES_INVALID", "probabilities must be finite 1-D values")
    if np.any((arr <= 0.0) | (arr >= 1.0)):
        raise ProbabilityQualityError("MLB_CALIBRATION_PROBABILITY_DOMAIN_INVALID", "probabilities must be inside (0,1)")
    return np.clip(arr, EPS, 1.0 - EPS)


def _outcome_array(values: Sequence[bool | int]) -> np.ndarray:
    arr = np.asarray(values, dtype=int)
    if arr.ndim != 1 or len(arr) == 0 or not np.all(np.isin(arr, [0, 1])):
        raise ProbabilityQualityError("MLB_CALIBRATION_OUTCOMES_INVALID", "outcomes must be binary")
    if len(np.unique(arr)) < 2:
        raise ProbabilityQualityError("MLB_CALIBRATION_OUTCOME_CLASS_DEGENERATE", "both outcome classes are required")
    return arr


def _logit(probabilities: np.ndarray) -> np.ndarray:
    p = np.clip(probabilities, EPS, 1.0 - EPS)
    return np.log(p / (1.0 - p))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def champion_intercept_map(probabilities: Sequence[float], intercept_shift: float) -> np.ndarray:
    """Apply the incumbent V2D prevalence/intercept mapping exactly."""
    p = _probability_array(probabilities)
    if not isfinite(float(intercept_shift)):
        raise ProbabilityQualityError("MLB_INCUMBENT_INTERCEPT_INVALID", "finite intercept_shift required")
    return np.clip(_sigmoid(_logit(p) + float(intercept_shift)), EPS, 1.0 - EPS)


def canonicalize_early_grades(rows: Iterable[ImmutableGradeRow]) -> tuple[ImmutableGradeRow, ...]:
    """Keep exactly the earliest immutable scored row per official event.

    Historical duplicates remain immutable in storage; replay must not count them
    more than once.  The exact earliest `(snapshot_time, score_snapshot_id)` wins.
    """
    chosen: dict[str, ImmutableGradeRow] = {}
    for row in rows:
        event_id = str(row.official_event_id or "").strip()
        if not event_id:
            raise ProbabilityQualityError("MLB_GRADE_EVENT_ID_MISSING", "official_event_id required")
        current = chosen.get(event_id)
        if current is None or (row.snapshot_time, row.score_snapshot_id) < (current.snapshot_time, current.score_snapshot_id):
            chosen[event_id] = row
    return tuple(sorted(chosen.values(), key=lambda row: (row.snapshot_time, row.official_event_id)))


def equal_count_calibration_error(probabilities: Sequence[float], outcomes: Sequence[bool | int], bins: int = 10) -> tuple[float, float]:
    p = _probability_array(probabilities)
    y = _outcome_array(outcomes)
    if len(p) != len(y):
        raise ProbabilityQualityError("MLB_CALIBRATION_LENGTH_MISMATCH", "probability/outcome lengths differ")
    if bins < 2:
        raise ValueError("bins must be >=2")
    order = np.argsort(p, kind="stable")
    weighted_gap = 0.0
    max_gap = 0.0
    for chunk in np.array_split(order, min(bins, len(order))):
        if len(chunk) == 0:
            continue
        gap = abs(float(np.mean(p[chunk])) - float(np.mean(y[chunk])))
        weighted_gap += (len(chunk) / len(p)) * gap
        max_gap = max(max_gap, gap)
    return float(weighted_gap), float(max_gap)


def calibration_intercept_slope(probabilities: Sequence[float], outcomes: Sequence[bool | int]) -> tuple[float, float]:
    p = _probability_array(probabilities)
    y = _outcome_array(outcomes)
    x = _logit(p).reshape(-1, 1)
    model = LogisticRegression(C=1e8, solver="lbfgs", max_iter=2000, random_state=0)
    model.fit(x, y)
    return float(model.intercept_[0]), float(model.coef_[0, 0])


def compute_calibration_metrics(probabilities: Sequence[float], outcomes: Sequence[bool | int]) -> CalibrationMetrics:
    p = _probability_array(probabilities)
    y = _outcome_array(outcomes)
    if len(p) != len(y):
        raise ProbabilityQualityError("MLB_CALIBRATION_LENGTH_MISMATCH", "probability/outcome lengths differ")
    ece, max_gap = equal_count_calibration_error(p, y, bins=10)
    intercept, slope = calibration_intercept_slope(p, y)
    selected_correct = np.where(p >= 0.5, y == 1, y == 0)
    return CalibrationMetrics(
        n=int(len(p)),
        brier=float(brier_score_loss(y, p)),
        log_loss=float(log_loss(y, p, labels=[0, 1])),
        ece_equal_count=ece,
        max_equal_count_gap=max_gap,
        calibration_intercept=intercept,
        calibration_slope=slope,
        roc_auc=float(roc_auc_score(y, p)),
        selected_side_hit_rate=float(np.mean(selected_correct)),
        probability_stddev=float(np.std(p)),
        probability_p10=float(np.quantile(p, 0.10)),
        probability_p90=float(np.quantile(p, 0.90)),
    )


def assess_calibration_health(metrics: CalibrationMetrics, policy: CalibrationHealthPolicy) -> CalibrationHealthAssessment:
    """Quantitative research assessor; it never changes production health state."""
    blockers: list[str] = []
    if metrics.n < policy.min_n:
        blockers.append("CALIBRATION_SAMPLE_INSUFFICIENT")
    if metrics.brier > policy.max_brier:
        blockers.append("CALIBRATION_BRIER_EXCEEDS_LIMIT")
    if metrics.log_loss > policy.max_log_loss:
        blockers.append("CALIBRATION_LOG_LOSS_EXCEEDS_LIMIT")
    if metrics.ece_equal_count > policy.max_ece:
        blockers.append("CALIBRATION_ECE_EXCEEDS_LIMIT")
    if metrics.max_equal_count_gap > policy.max_bin_gap:
        blockers.append("CALIBRATION_BIN_GAP_EXCEEDS_LIMIT")
    if abs(metrics.calibration_intercept) > policy.max_abs_calibration_intercept:
        blockers.append("CALIBRATION_INTERCEPT_OUT_OF_RANGE")
    if not (policy.min_calibration_slope <= metrics.calibration_slope <= policy.max_calibration_slope):
        blockers.append("CALIBRATION_SLOPE_OUT_OF_RANGE")
    return CalibrationHealthAssessment(
        status="PASS" if not blockers else "REVIEW_REQUIRED",
        blockers=tuple(blockers),
        metrics=metrics,
    )


class _FittedCalibrator:
    def __init__(self, method: str, predict: Callable[[np.ndarray], np.ndarray]):
        self.method = method
        self._predict = predict

    def predict(self, probabilities: Sequence[float]) -> np.ndarray:
        p = _probability_array(probabilities)
        return np.clip(self._predict(p), EPS, 1.0 - EPS)


def _fit_platt(probabilities: np.ndarray, outcomes: np.ndarray) -> _FittedCalibrator:
    x = _logit(probabilities).reshape(-1, 1)
    model = LogisticRegression(C=1e8, solver="lbfgs", max_iter=2000, random_state=0)
    model.fit(x, outcomes)
    return _FittedCalibrator("PLATT_LOGIT_AFFINE_V1", lambda p: model.predict_proba(_logit(p).reshape(-1, 1))[:, 1])


def _fit_beta(probabilities: np.ndarray, outcomes: np.ndarray) -> _FittedCalibrator:
    def features(p: np.ndarray) -> np.ndarray:
        q = np.clip(p, EPS, 1.0 - EPS)
        return np.column_stack([np.log(q), -np.log1p(-q)])

    model = LogisticRegression(C=1e8, solver="lbfgs", max_iter=2000, random_state=0)
    model.fit(features(probabilities), outcomes)
    return _FittedCalibrator("BETA_CALIBRATION_V1", lambda p: model.predict_proba(features(p))[:, 1])


def _fit_isotonic(probabilities: np.ndarray, outcomes: np.ndarray) -> _FittedCalibrator:
    model = IsotonicRegression(y_min=EPS, y_max=1.0 - EPS, out_of_bounds="clip")
    model.fit(probabilities, outcomes)
    return _FittedCalibrator("ISOTONIC_MONOTONE_V1", lambda p: np.asarray(model.predict(p), dtype=float))


def _fit_identity() -> _FittedCalibrator:
    return _FittedCalibrator("IDENTITY_RAW_V1", lambda p: np.asarray(p, dtype=float))


def _metric_record(method: str, split: str, p: np.ndarray, y: np.ndarray) -> ChallengerMetrics:
    return ChallengerMetrics(method=method, split=split, metrics=compute_calibration_metrics(p, y))


def temporal_calibration_challenge(
    rows: Sequence[CalibrationRow],
    *,
    incumbent_intercept_shift: float,
    fit_fraction: float = 0.60,
    selection_fraction: float = 0.20,
    minimum_partition_n: int = 50,
) -> TemporalCalibrationChallenge:
    """Compare calibration challengers without using untouched test for selection.

    Rows must be chronological. Challenger family selection uses the middle
    selection block.  The final block is untouched until after selection.
    For transparency we also report each challenger on the untouched test, but
    that table is diagnostic only and cannot auto-promote a method.
    """
    if len(rows) < minimum_partition_n * 3:
        raise ProbabilityQualityError("MLB_CHALLENGER_SAMPLE_INSUFFICIENT", str(len(rows)))
    ordered = sorted(rows, key=lambda row: (row.event_time, row.event_id))
    if list(rows) != ordered:
        raise ProbabilityQualityError("MLB_CHALLENGER_ROWS_NOT_CHRONOLOGICAL", "rows must be ordered")
    if not (0.50 <= fit_fraction <= 0.70 and 0.15 <= selection_fraction <= 0.25):
        raise ValueError("unsupported split fractions")
    if fit_fraction + selection_fraction > 0.85:
        raise ValueError("at least 15% must remain untouched")

    p = _probability_array([row.raw_home_probability for row in rows])
    y = _outcome_array([row.home_win for row in rows])
    fit_end = int(len(rows) * fit_fraction)
    selection_end = int(len(rows) * (fit_fraction + selection_fraction))
    if min(fit_end, selection_end - fit_end, len(rows) - selection_end) < minimum_partition_n:
        raise ProbabilityQualityError("MLB_CHALLENGER_PARTITION_INSUFFICIENT", "each chronological block needs enough rows")

    p_fit, y_fit = p[:fit_end], y[:fit_end]
    p_selection, y_selection = p[fit_end:selection_end], y[fit_end:selection_end]
    p_test, y_test = p[selection_end:], y[selection_end:]

    challengers = (_fit_identity(), _fit_platt(p_fit, y_fit), _fit_beta(p_fit, y_fit), _fit_isotonic(p_fit, y_fit))
    selection_records: list[ChallengerMetrics] = []
    selection_scores: dict[str, tuple[float, float, float]] = {}
    for calibrator in challengers:
        mapped = calibrator.predict(p_selection)
        record = _metric_record(calibrator.method, "SELECTION", mapped, y_selection)
        selection_records.append(record)
        selection_scores[calibrator.method] = (
            record.metrics.brier,
            record.metrics.log_loss,
            record.metrics.ece_equal_count,
        )

    # Proper scores dominate selection; ECE breaks exact/near ties only.
    selected_method = min(selection_scores, key=lambda name: selection_scores[name])

    # Refit challengers on every row available before the untouched test.
    p_pretest, y_pretest = p[:selection_end], y[:selection_end]
    refit = (
        _fit_identity(),
        _fit_platt(p_pretest, y_pretest),
        _fit_beta(p_pretest, y_pretest),
        _fit_isotonic(p_pretest, y_pretest),
    )
    incumbent_test = champion_intercept_map(p_test, incumbent_intercept_shift)
    test_records: list[ChallengerMetrics] = [
        _metric_record("INCUMBENT_LOGIT_INTERCEPT_PREVALENCE_V1", "UNTOUCHED_TEST", incumbent_test, y_test)
    ]
    for calibrator in refit:
        test_records.append(_metric_record(calibrator.method, "UNTOUCHED_TEST_DIAGNOSTIC", calibrator.predict(p_test), y_test))

    return TemporalCalibrationChallenge(
        total_n=len(rows),
        fit_n=fit_end,
        selection_n=selection_end - fit_end,
        untouched_test_n=len(rows) - selection_end,
        incumbent_method="INCUMBENT_LOGIT_INTERCEPT_PREVALENCE_V1",
        selected_challenger_method=selected_method,
        selection_metrics=tuple(selection_records),
        untouched_test_metrics=tuple(test_records),
        test_used_for_selection=False,
    )


def dominance_from_score_pmfs(
    home_pmf: Sequence[float],
    away_pmf: Sequence[float],
    *,
    selected_side: str,
    extra_inning_selected_win_probability: float | None = None,
) -> DominanceDiagnostic:
    """Compute margin-tail diagnostics from a normalized fitted score distribution.

    This diagnostic does not and must not rewrite the separately governed
    outright win probability.
    """
    h = np.asarray(home_pmf, dtype=float)
    a = np.asarray(away_pmf, dtype=float)
    if h.ndim != 1 or a.ndim != 1 or len(h) < 2 or len(a) < 2:
        raise ProbabilityQualityError("MLB_DOMINANCE_PMF_INVALID", "1-D score PMFs required")
    if not np.all(np.isfinite(h)) or not np.all(np.isfinite(a)) or np.any(h < 0) or np.any(a < 0):
        raise ProbabilityQualityError("MLB_DOMINANCE_PMF_INVALID", "PMF values must be finite/non-negative")
    hsum, asum = float(h.sum()), float(a.sum())
    if abs(hsum - 1.0) > 1e-6 or abs(asum - 1.0) > 1e-6:
        raise ProbabilityQualityError("MLB_DOMINANCE_PMF_NOT_NORMALIZED", f"home={hsum};away={asum}")
    side = str(selected_side or "").upper()
    if side not in {"HOME", "AWAY"}:
        raise ProbabilityQualityError("MLB_DOMINANCE_SIDE_INVALID", side)

    joint = np.outer(h, a)
    hi, ai = np.indices(joint.shape)
    diff = hi - ai if side == "HOME" else ai - hi
    win = float(joint[diff > 0].sum())
    tie = float(joint[diff == 0].sum())
    if extra_inning_selected_win_probability is None:
        outright_win = win
        extras_selected = None
    else:
        extras_selected = float(extra_inning_selected_win_probability)
        if not isfinite(extras_selected) or not 0.0 <= extras_selected <= 1.0:
            raise ProbabilityQualityError(
                "MLB_DOMINANCE_EXTRA_INNING_PROBABILITY_INVALID",
                str(extra_inning_selected_win_probability),
            )
        outright_win = win + tie * extras_selected
    win2 = float(joint[diff >= 2].sum())
    win4 = float(joint[diff >= 4].sum())
    win6 = float(joint[diff >= 6].sum())
    loss4 = float(joint[diff <= -4].sum())
    expected = float(np.sum(joint * diff))
    asymmetry = win4 - loss4
    return DominanceDiagnostic(
        regulation_win_probability=win,
        tie_after_9_probability=tie,
        extra_inning_selected_win_probability=extras_selected,
        outright_win_probability=outright_win,
        win_by_2_plus_probability=win2,
        win_by_4_plus_probability=win4,
        win_by_6_plus_probability=win6,
        expected_run_differential=expected,
        loss_by_4_plus_probability=loss4,
        blowout_asymmetry_score=asymmetry,
        probability_sum=float(joint.sum()),
    )
