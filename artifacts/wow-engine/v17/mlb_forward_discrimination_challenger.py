"""Research-only MLB forward discrimination challengers for WOW V17.

This module contains no production registration or publication authority. It
provides two narrowly-scoped research utilities:

1. Reconstruct the historical 36-feature direct-win vector exactly from the
   forward-native HOME/AWAY 38-feature run snapshots.
2. Fit a small two-logit stack over the governed run probability and the
   research Direct36 probability.

Any promotion decision remains outside this module and requires governed Class C
review. Execution and automatic promotion are permanently disabled here.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
EPS = 1e-9

RUN38_FEATURE_NAMES = (
    "is_home",
    "off_runs_pg",
    "off_hits_pg",
    "off_hr_pg",
    "off_bb_pg",
    "off_so_pg",
    "off_tb_pg",
    "off_run_diff_pg",
    "off_win_rate",
    "off_sb_pg",
    "off_cs_pg",
    "off_days_rest",
    "opp_runs_allowed_pg",
    "opp_errors_pg",
    "opp_win_rate",
    "opp_bp_era",
    "opp_bp_k_rate",
    "opp_bp_bb_rate",
    "opp_bp_hr_rate",
    "opp_bp_pitches_3d",
    "opp_bp_outs_3d",
    "opp_bp_apps_3d",
    "opp_starter_prior_starts",
    "opp_starter_era",
    "opp_starter_k_rate",
    "opp_starter_bb_rate",
    "opp_starter_h_rate",
    "opp_starter_hr_rate",
    "opp_starter_outs_per_start",
    "opp_starter_tbf_per_start",
    "opp_starter_pitches_per_start",
    "opp_starter_strike_rate",
    "opp_starter_days_rest",
    "opp_starter_pitches_last3",
    "park_total_runs_prior",
    "park_prior_games",
    "opp_days_rest",
    "min_team_prior_games",
)

DIRECT36_FEATURE_NAMES = (
    "runs_pg_diff",
    "hits_pg_diff",
    "hr_pg_diff",
    "bb_pg_diff",
    "so_pg_diff",
    "tb_pg_diff",
    "runs_allowed_pg_diff",
    "run_diff_pg_diff",
    "errors_pg_diff",
    "sb_pg_diff",
    "cs_pg_diff",
    "win_rate_diff",
    "bp_era_diff",
    "bp_k_rate_diff",
    "bp_bb_rate_diff",
    "bp_hr_rate_diff",
    "bp_pitches_3d_diff",
    "bp_outs_3d_diff",
    "bp_apps_3d_diff",
    "starter_prior_starts_diff",
    "starter_era_diff",
    "starter_k_rate_diff",
    "starter_bb_rate_diff",
    "starter_h_rate_diff",
    "starter_hr_rate_diff",
    "starter_outs_per_start_diff",
    "starter_tbf_per_start_diff",
    "starter_pitches_per_start_diff",
    "starter_strike_rate_diff",
    "starter_days_rest_diff",
    "starter_pitches_last3_diff",
    "park_total_runs_prior",
    "park_prior_games",
    "team_rest_diff",
    "starter_min_prior_starts",
    "team_min_prior_games",
)


@dataclass(frozen=True)
class TwoLogitStack:
    intercept: float
    run_logit_weight: float
    direct_logit_weight: float
    fit_n: int
    ridge: float
    method: str = "RUN_DIRECT_TWO_LOGIT_STACK_V1"
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False

    def predict(self, run_probabilities, direct_probabilities):
        run_p = _probabilities(run_probabilities)
        direct_p = _probabilities(direct_probabilities)
        if len(run_p) != len(direct_p):
            raise ValueError("MLB_FORWARD_STACK_LENGTH_MISMATCH")
        eta = (
            self.intercept
            + self.run_logit_weight * _logit(run_p)
            + self.direct_logit_weight * _logit(direct_p)
        )
        eta = np.clip(eta, -40.0, 40.0)
        return np.clip(1.0 / (1.0 + np.exp(-eta)), EPS, 1.0 - EPS)


@dataclass(frozen=True)
class ProbabilityMetrics:
    n: int
    brier: float
    log_loss: float
    selected_side_hit_rate: float
    ece: float
    max_bin_gap: float
    roc_auc: float


def _vector(values, expected_length: int, code: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or len(arr) != expected_length or not np.all(np.isfinite(arr)):
        raise ValueError(code)
    return arr


def _probabilities(values) -> np.ndarray:
    p = np.asarray(values, dtype=float)
    if p.ndim != 1 or len(p) == 0 or not np.all(np.isfinite(p)):
        raise ValueError("MLB_FORWARD_STACK_PROBABILITIES_INVALID")
    if np.any((p <= 0.0) | (p >= 1.0)):
        raise ValueError("MLB_FORWARD_STACK_PROBABILITY_DOMAIN_INVALID")
    return np.clip(p, EPS, 1.0 - EPS)


def _outcomes(values) -> np.ndarray:
    y = np.asarray(values, dtype=int)
    if y.ndim != 1 or len(y) == 0 or not np.all(np.isin(y, [0, 1])):
        raise ValueError("MLB_FORWARD_STACK_OUTCOMES_INVALID")
    if len(np.unique(y)) < 2:
        raise ValueError("MLB_FORWARD_STACK_OUTCOME_CLASS_DEGENERATE")
    return y


def _logit(p: np.ndarray) -> np.ndarray:
    return np.log(p / (1.0 - p))


def validate_run38_feature_names(feature_names) -> None:
    if tuple(feature_names) != RUN38_FEATURE_NAMES:
        raise ValueError("MLB_FORWARD_RUN38_FEATURE_ORDER_INVALID")


def direct36_from_run_pair(home_values, away_values) -> np.ndarray:
    """Rebuild the governed historical Direct36 vector from run snapshots.

    The mapping is deliberately explicit because opponent defensive/bullpen/
    starter fields are stored from the scoring side's perspective. For those
    features the HOME-minus-AWAY team difference therefore uses
    ``away.opp_* - home.opp_*``.
    """
    h = _vector(home_values, 38, "MLB_FORWARD_HOME_RUN38_INVALID")
    a = _vector(away_values, 38, "MLB_FORWARD_AWAY_RUN38_INVALID")
    if not np.isclose(h[0], 1.0) or not np.isclose(a[0], 0.0):
        raise ValueError("MLB_FORWARD_RUN_PAIR_ORIENTATION_INVALID")
    if not np.isclose(h[34], a[34]) or not np.isclose(h[35], a[35]):
        raise ValueError("MLB_FORWARD_PARK_CONTEXT_MISMATCH")

    out = [
        *(h[1:7] - a[1:7]),
        a[12] - h[12],
        h[7] - a[7],
        a[13] - h[13],
        h[9] - a[9],
        h[10] - a[10],
        h[8] - a[8],
        *(a[15:22] - h[15:22]),
        *(a[22:34] - h[22:34]),
        h[34],
        h[35],
        h[11] - a[11],
        min(h[22], a[22]),
        min(h[37], a[37]),
    ]
    result = np.asarray(out, dtype=float)
    if len(result) != 36 or not np.all(np.isfinite(result)):
        raise ValueError("MLB_FORWARD_DIRECT36_RECONSTRUCTION_INVALID")
    return result


def fit_two_logit_stack(
    run_probabilities,
    direct_probabilities,
    outcomes,
    *,
    ridge: float = 0.001,
    max_iter: int = 50,
    tol: float = 1e-8,
) -> TwoLogitStack:
    """Fit intercept + run-logit + Direct36-logit using deterministic Newton steps."""
    run_p = _probabilities(run_probabilities)
    direct_p = _probabilities(direct_probabilities)
    y = _outcomes(outcomes).astype(float)
    if len(run_p) != len(direct_p) or len(run_p) != len(y):
        raise ValueError("MLB_FORWARD_STACK_LENGTH_MISMATCH")
    if ridge < 0 or not np.isfinite(ridge):
        raise ValueError("MLB_FORWARD_STACK_RIDGE_INVALID")

    x = np.column_stack([np.ones(len(y)), _logit(run_p), _logit(direct_p)])
    rate = float(np.mean(y))
    beta = np.array([np.log(rate / (1.0 - rate)), 0.5, 0.5], dtype=float)

    for _ in range(max_iter):
        eta = np.clip(x @ beta, -40.0, 40.0)
        p = 1.0 / (1.0 + np.exp(-eta))
        gradient = x.T @ (p - y)
        gradient[1:] += ridge * beta[1:]
        weights = p * (1.0 - p)
        hessian = x.T @ (x * weights[:, None])
        hessian[1, 1] += ridge
        hessian[2, 2] += ridge
        try:
            delta = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError as exc:
            raise ValueError("MLB_FORWARD_STACK_HESSIAN_SINGULAR") from exc
        beta -= delta
        if float(np.linalg.norm(gradient)) < tol:
            break
    if not np.all(np.isfinite(beta)):
        raise ValueError("MLB_FORWARD_STACK_FIT_INVALID")

    return TwoLogitStack(
        intercept=float(beta[0]),
        run_logit_weight=float(beta[1]),
        direct_logit_weight=float(beta[2]),
        fit_n=len(y),
        ridge=float(ridge),
    )


def evaluate_probabilities(probabilities, outcomes, *, bins: int = 10) -> ProbabilityMetrics:
    p = _probabilities(probabilities)
    y = _outcomes(outcomes)
    if len(p) != len(y):
        raise ValueError("MLB_FORWARD_STACK_LENGTH_MISMATCH")
    if bins < 2:
        raise ValueError("MLB_FORWARD_STACK_BIN_COUNT_INVALID")

    selected_home = p >= 0.5
    hit = np.where(selected_home, y == 1, y == 0)
    order = np.argsort(p, kind="mergesort")
    groups = np.array_split(order, min(bins, len(order)))
    weighted_gap = 0.0
    max_gap = 0.0
    for group in groups:
        if len(group) == 0:
            continue
        gap = abs(float(np.mean(p[group])) - float(np.mean(y[group])))
        weighted_gap += len(group) * gap
        max_gap = max(max_gap, gap)

    return ProbabilityMetrics(
        n=len(p),
        brier=float(brier_score_loss(y, p)),
        log_loss=float(log_loss(y, p, labels=[0, 1])),
        selected_side_hit_rate=float(np.mean(hit)),
        ece=float(weighted_gap / len(p)),
        max_bin_gap=float(max_gap),
        roc_auc=float(roc_auc_score(y, p)),
    )


def promotion_blockers(incumbent: ProbabilityMetrics, challenger: ProbabilityMetrics) -> tuple[str, ...]:
    """Return the full metric gates a Class C challenger has not cleared."""
    blockers: list[str] = []
    if challenger.brier >= incumbent.brier:
        blockers.append("BRIER_NOT_IMPROVED")
    if challenger.log_loss >= incumbent.log_loss:
        blockers.append("LOG_LOSS_NOT_IMPROVED")
    if challenger.ece >= incumbent.ece:
        blockers.append("ECE_NOT_IMPROVED")
    if challenger.max_bin_gap >= incumbent.max_bin_gap:
        blockers.append("MAX_BIN_GAP_NOT_IMPROVED")
    if challenger.roc_auc <= incumbent.roc_auc:
        blockers.append("DISCRIMINATION_NOT_IMPROVED")
    if challenger.selected_side_hit_rate <= incumbent.selected_side_hit_rate:
        blockers.append("HIT_RATE_NOT_IMPROVED")
    return tuple(blockers)
