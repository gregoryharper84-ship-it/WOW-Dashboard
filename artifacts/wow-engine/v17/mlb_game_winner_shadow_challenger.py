"""V17 MLB Game Winner probability-sharpness shadow challenger.

This module is deliberately research/shadow only. It improves the sporting
probability layer without changing any downstream Game Winner admission,
payout, market-value, portfolio, final-refresh, or terminal-reducer rule.

Design invariants
-----------------
* one shared home-win model; away probability is exactly 1-home;
* no sportsbook/market/payout feature may enter the sporting model;
* no postgame/outcome feature may enter the pregame feature vector;
* missing feature values are imputed instead of becoming a new candidate gate;
* uncertainty is candidate-specific through bootstrap model dispersion;
* champion/challenger promotion is calibration-first and never automatic;
* can_execute is always false.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MODEL_FAMILY = "MLB_GAME_WIN_SHADOW_SHARPNESS_V17"
MODEL_VERSION = "MLB_GAME_WIN_SHADOW_SHARPNESS_V17_R1"
FEATURE_SCHEMA_VERSION = "MLB_GAME_WIN_SHADOW_FEATURES_V1"
CALIBRATION_VERSION = "MLB_GAME_WIN_SHADOW_PLATT_V1"
SERVING_MODE = "SHADOW_ONLY"
CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
ADMISSION_POLICY_MUTATION_ALLOWED = False
MARKET_PRIOR_WEIGHT = 0.0

# Existing structural signal is retained; tail/failure-path signal is additive.
BASELINE_FEATURES = (
    "run_num_diff_20",
    "run_num_diff_40",
    "rd_diff_20",
    "rd_diff_40",
    "off_ops_diff_20",
    "starter_era_diff_6",
    "starter_kbb_diff_6",
    "bullpen_era_diff_20",
    "bullpen_kbb_diff_20",
    "hfa_indicator",
)
TAIL_FEATURES = (
    "starter_run_variance_diff",
    "starter_catastrophe_rate_diff",
    "starter_early_hook_rate_diff",
    "starter_third_time_through_rate_diff",
    "offense_cluster_rate_diff",
    "offense_scoreless5_rate_diff",
    "bullpen_run_variance_diff",
    "bullpen_3plus_rate_diff",
    "leverage_availability_diff",
    "handoff_risk_diff",
    "lineup_platoon_run_value_diff",
    "park_weather_run_environment",
)
DEFAULT_FEATURES = BASELINE_FEATURES + TAIL_FEATURES

FORBIDDEN_MARKET_TOKENS = (
    "sportsbook",
    "book_price",
    "market_price",
    "implied_probability",
    "market_probability",
    "no_vig",
    "odds",
    "moneyline",
    "prizepicks_multiplier",
    "payout",
    "break_even",
    "edge",
    "clv",
)
FORBIDDEN_POSTGAME_TOKENS = (
    "home_win",
    "away_win",
    "team_win",
    "final_score",
    "final_runs",
    "actual_outcome",
    "realized_outcome",
    "postgame",
    "settled_result",
)


class ShadowChallengerError(ValueError):
    pass


@dataclass(frozen=True)
class ProbabilityMetrics:
    n: int
    brier_score: float
    log_loss: float
    calibration_slope: float
    calibration_intercept: float
    expected_calibration_error: float
    mean_probability: float
    observed_rate: float


@dataclass(frozen=True)
class ShadowPrediction:
    home_probability_raw: float
    home_probability_calibrated: float
    home_lower_bound: float
    home_upper_bound: float
    away_probability_raw: float
    away_probability_calibrated: float
    away_lower_bound: float
    away_upper_bound: float
    bootstrap_models_used: int
    market_prior_weight: float = MARKET_PRIOR_WEIGHT
    serving_mode: str = SERVING_MODE
    automatic_promotion_allowed: bool = AUTOMATIC_PROMOTION_ALLOWED
    admission_policy_mutated: bool = False
    can_execute: bool = CAN_EXECUTE


@dataclass
class ShadowArtifact:
    feature_names: tuple[str, ...]
    base_model: Pipeline
    calibrator: LogisticRegression
    bootstrap_models: tuple[Pipeline, ...]
    seed: int
    model_family: str = MODEL_FAMILY
    model_version: str = MODEL_VERSION
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    calibration_version: str = CALIBRATION_VERSION
    serving_mode: str = SERVING_MODE
    can_execute: bool = CAN_EXECUTE


def _clip_probability(value: np.ndarray | float, eps: float = 1e-6):
    return np.clip(value, eps, 1.0 - eps)


def _logit(values: np.ndarray) -> np.ndarray:
    p = _clip_probability(np.asarray(values, dtype=float))
    return np.log(p / (1.0 - p))


def audit_feature_names(feature_names: Sequence[str]) -> None:
    names = [str(name).strip() for name in feature_names]
    if not names or any(not name for name in names):
        raise ShadowChallengerError("MODEL_INPUTS_INSUFFICIENT: empty_feature_name")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ShadowChallengerError(f"MODEL_INPUTS_INSUFFICIENT: duplicate_features={','.join(duplicates)}")
    leaked_market = sorted(
        name for name in names if any(token in name.lower() for token in FORBIDDEN_MARKET_TOKENS)
    )
    if leaked_market:
        raise ShadowChallengerError(
            f"GOVERNANCE_MARKET_LEAKAGE: {','.join(leaked_market)}"
        )
    leaked_postgame = sorted(
        name for name in names if any(token in name.lower() for token in FORBIDDEN_POSTGAME_TOKENS)
    )
    if leaked_postgame:
        raise ShadowChallengerError(
            f"GOVERNANCE_POSTGAME_LEAKAGE: {','.join(leaked_postgame)}"
        )


def feature_coverage(rows: Sequence[Mapping[str, Any]], feature_names: Sequence[str]) -> dict[str, float]:
    """Return observational coverage only; low coverage does not reject a game row."""
    audit_feature_names(feature_names)
    n = max(len(rows), 1)
    return {
        name: sum(_finite_or_none(row.get(name)) is not None for row in rows) / n
        for name in feature_names
    }


def _finite_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _matrix(rows: Sequence[Mapping[str, Any]], feature_names: Sequence[str]) -> np.ndarray:
    audit_feature_names(feature_names)
    if not rows:
        raise ShadowChallengerError("MODEL_INPUTS_INSUFFICIENT: no_rows")
    return np.asarray(
        [[np.nan if _finite_or_none(row.get(name)) is None else float(row[name]) for name in feature_names] for row in rows],
        dtype=float,
    )


def _labels(values: Sequence[Any]) -> np.ndarray:
    if not values:
        raise ShadowChallengerError("MODEL_INPUTS_INSUFFICIENT: no_labels")
    y = np.asarray([int(bool(v)) for v in values], dtype=int)
    if len(np.unique(y)) < 2:
        raise ShadowChallengerError("MODEL_INPUTS_INSUFFICIENT: one_class_labels")
    return y


def _new_model() -> Pipeline:
    return Pipeline(
        steps=(
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
            ("logit", LogisticRegression(C=1.0, max_iter=4000, solver="lbfgs")),
        )
    )


def fit_shadow_challenger(
    train_rows: Sequence[Mapping[str, Any]],
    train_home_wins: Sequence[Any],
    calibration_rows: Sequence[Mapping[str, Any]],
    calibration_home_wins: Sequence[Any],
    *,
    feature_names: Sequence[str] = DEFAULT_FEATURES,
    bootstrap_models: int = 48,
    seed: int = 1706,
) -> ShadowArtifact:
    """Fit a research-only probability challenger.

    The calibration cohort must be chronologically after the fitting cohort at
    the caller boundary. This function intentionally does not know slate/pick
    thresholds and therefore cannot make the Game Winner lane stricter.
    """
    names = tuple(feature_names)
    x_train = _matrix(train_rows, names)
    y_train = _labels(train_home_wins)
    x_cal = _matrix(calibration_rows, names)
    y_cal = _labels(calibration_home_wins)
    if len(x_train) != len(y_train) or len(x_cal) != len(y_cal):
        raise ShadowChallengerError("MODEL_INPUTS_INSUFFICIENT: row_label_length_mismatch")

    base = _new_model()
    base.fit(x_train, y_train)

    raw_cal = _clip_probability(base.predict_proba(x_cal)[:, 1])
    calibrator = LogisticRegression(C=1000.0, max_iter=4000, solver="lbfgs")
    calibrator.fit(_logit(raw_cal).reshape(-1, 1), y_cal)

    rng = np.random.default_rng(seed)
    boot: list[Pipeline] = []
    for _ in range(max(0, int(bootstrap_models))):
        idx = rng.integers(0, len(y_train), size=len(y_train))
        y_sample = y_train[idx]
        if len(np.unique(y_sample)) < 2:
            continue
        model = _new_model()
        model.fit(x_train[idx], y_sample)
        boot.append(model)

    return ShadowArtifact(
        feature_names=names,
        base_model=base,
        calibrator=calibrator,
        bootstrap_models=tuple(boot),
        seed=seed,
    )


def _apply_calibration(artifact: ShadowArtifact, raw_home_probability: np.ndarray) -> np.ndarray:
    raw = _clip_probability(np.asarray(raw_home_probability, dtype=float))
    return artifact.calibrator.predict_proba(_logit(raw).reshape(-1, 1))[:, 1]


def predict_shadow(
    artifact: ShadowArtifact,
    rows: Sequence[Mapping[str, Any]],
    *,
    lower_quantile: float = 0.10,
    upper_quantile: float = 0.90,
) -> list[ShadowPrediction]:
    if not (0.0 < lower_quantile < upper_quantile < 1.0):
        raise ShadowChallengerError("MODEL_INPUTS_INSUFFICIENT: invalid_interval_quantiles")
    x = _matrix(rows, artifact.feature_names)
    raw = _clip_probability(artifact.base_model.predict_proba(x)[:, 1])
    calibrated = _apply_calibration(artifact, raw)

    if artifact.bootstrap_models:
        boot_raw = np.vstack([model.predict_proba(x)[:, 1] for model in artifact.bootstrap_models])
        boot_cal = np.vstack([_apply_calibration(artifact, line) for line in boot_raw])
        lower = np.quantile(boot_cal, lower_quantile, axis=0)
        upper = np.quantile(boot_cal, upper_quantile, axis=0)
    else:
        # No bootstrap means no certified uncertainty claim; preserve the point
        # probability exactly instead of inventing a universal haircut.
        lower = calibrated.copy()
        upper = calibrated.copy()

    out: list[ShadowPrediction] = []
    for i in range(len(rows)):
        hp_raw = float(raw[i])
        hp = float(calibrated[i])
        lo = float(min(lower[i], hp))
        hi = float(max(upper[i], hp))
        out.append(
            ShadowPrediction(
                home_probability_raw=hp_raw,
                home_probability_calibrated=hp,
                home_lower_bound=lo,
                home_upper_bound=hi,
                away_probability_raw=1.0 - hp_raw,
                away_probability_calibrated=1.0 - hp,
                away_lower_bound=1.0 - hi,
                away_upper_bound=1.0 - lo,
                bootstrap_models_used=len(artifact.bootstrap_models),
            )
        )
    return out


def probability_metrics(actual_home_wins: Sequence[Any], probabilities: Sequence[float], *, bins: int = 10) -> ProbabilityMetrics:
    y = np.asarray([int(bool(v)) for v in actual_home_wins], dtype=float)
    p = _clip_probability(np.asarray(probabilities, dtype=float))
    if len(y) == 0 or len(y) != len(p):
        raise ShadowChallengerError("MODEL_INPUTS_INSUFFICIENT: metric_length_mismatch")
    brier = float(np.mean((p - y) ** 2))
    ll = float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))

    slope = float("nan")
    intercept = float("nan")
    if len(np.unique(y)) >= 2:
        audit = LogisticRegression(C=1_000_000.0, max_iter=4000, solver="lbfgs")
        audit.fit(_logit(p).reshape(-1, 1), y.astype(int))
        slope = float(audit.coef_[0][0])
        intercept = float(audit.intercept_[0])

    edges = np.linspace(0.0, 1.0, max(2, int(bins)) + 1)
    ece = 0.0
    for i in range(len(edges) - 1):
        if i == len(edges) - 2:
            mask = (p >= edges[i]) & (p <= edges[i + 1])
        else:
            mask = (p >= edges[i]) & (p < edges[i + 1])
        if np.any(mask):
            ece += float(np.mean(mask)) * abs(float(np.mean(p[mask])) - float(np.mean(y[mask])))

    return ProbabilityMetrics(
        n=len(y),
        brier_score=brier,
        log_loss=ll,
        calibration_slope=slope,
        calibration_intercept=intercept,
        expected_calibration_error=float(ece),
        mean_probability=float(np.mean(p)),
        observed_rate=float(np.mean(y)),
    )


def calibration_first_comparison(
    actual_home_wins: Sequence[Any],
    champion_probabilities: Sequence[float],
    challenger_probabilities: Sequence[float],
) -> dict[str, Any]:
    """Compare sporting probabilities only; never auto-promote or filter picks."""
    champion = probability_metrics(actual_home_wins, champion_probabilities)
    challenger = probability_metrics(actual_home_wins, challenger_probabilities)
    metrics_pass = (
        challenger.brier_score <= champion.brier_score
        and challenger.log_loss <= champion.log_loss
        and abs(challenger.calibration_slope - 1.0) <= abs(champion.calibration_slope - 1.0)
        and abs(challenger.calibration_intercept) <= abs(champion.calibration_intercept)
    )
    return {
        "champion": champion,
        "challenger": challenger,
        "calibration_first_metrics_pass": bool(metrics_pass),
        "promotion_state": "SHADOW_REVIEW_REQUIRED" if metrics_pass else "SHADOW_CONTINUE",
        "automatic_promotion": False,
        "admission_policy_mutated": False,
        "cash_single_gate_mutated": False,
        "market_prior_weight": MARKET_PRIOR_WEIGHT,
        "can_execute": False,
    }


def serialize_prediction(prediction: ShadowPrediction) -> dict[str, Any]:
    return {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "calibration_version": CALIBRATION_VERSION,
        "serving_mode": SERVING_MODE,
        **prediction.__dict__,
    }


__all__ = [
    "ADMISSION_POLICY_MUTATION_ALLOWED",
    "AUTOMATIC_PROMOTION_ALLOWED",
    "BASELINE_FEATURES",
    "CALIBRATION_VERSION",
    "CAN_EXECUTE",
    "DEFAULT_FEATURES",
    "FEATURE_SCHEMA_VERSION",
    "MARKET_PRIOR_WEIGHT",
    "MODEL_FAMILY",
    "MODEL_VERSION",
    "SERVING_MODE",
    "ShadowArtifact",
    "ShadowChallengerError",
    "ShadowPrediction",
    "TAIL_FEATURES",
    "audit_feature_names",
    "calibration_first_comparison",
    "feature_coverage",
    "fit_shadow_challenger",
    "predict_shadow",
    "probability_metrics",
    "serialize_prediction",
]
