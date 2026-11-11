"""Fitted NHL full-game total-goals *regime mixture* research challenger.

Model the REG / OT / SO event-ending regime explicitly. OT and SO official
final totals are structurally odd (tied after regulation plus one awarded
goal); REG totals use a zero-truncated Poisson count. This avoids pretending
NHL final scores have one unconstrained Poisson count distribution.

Requirements: exact first-party historical NHL outcome + score identities,
prior-game-only feature reconstruction, disjoint chronological cohorts.
No goalie pregame evidence or governed calibration/lower-bound in this V1.
NOT A REGISTERED V17 SPECIALIST. No actionable sporting probability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import exp, isfinite, log
import json
from typing import Any, Sequence

import numpy as np
from scipy.stats import poisson
from sklearn.linear_model import LogisticRegression, PoissonRegressor
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
MODEL_FAMILY = "NHL_FULL_GAME_REGIME_MIXTURE_RESEARCH_V1"
REGIMES = ("REG", "OT", "SO")
TRAIN_YEARS = (2021, 2022)
CALIBRATION_YEAR = 2023
DEVELOPMENT_YEAR = 2024
FORWARD_RESERVE_YEAR = 2025
FEATURE_COUNT = 6
FIXED_ALPHA = 1.0


class NHLRegimeMixtureResearchError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class RegimeResearchRow:
    event_id: str
    season_start_year: int
    start_utc: str
    feature_as_of: str
    features: tuple[float, ...]
    total_goals: int
    terminal_regime: str
    official_source_sha256: str
    outcome_regime_sha256: str
    historical_reconstruction: bool = True
    archived_pregame_snapshot: bool = False


@dataclass(frozen=True)
class RegimeMixtureArtifact:
    model_family: str
    feature_count: int
    train_n: int
    calibration_n: int
    development_n: int
    training_hash: str
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    logistic_class_order: tuple[str, ...]
    logistic_coef: tuple[tuple[float, ...], ...]
    logistic_intercept: tuple[float, ...]
    class_calibration_weights: tuple[float, ...]
    poisson_coef: tuple[tuple[float, ...], ...]
    poisson_intercept: tuple[float, ...]
    poisson_mean_multipliers: tuple[float, ...]
    goal_count_definition: str = "NHL_OFFICIAL_FULL_GAME_FINAL_SCORE_OT_SO_INCLUDED"
    probability_publishable: bool = False
    can_execute: bool = False


def _date(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            raise ValueError("naive time")
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise NHLRegimeMixtureResearchError("NHL_REGIME_TIME_INVALID", str(value)) from exc


def _validate_rows(rows: Sequence[RegimeResearchRow]) -> None:
    ids = set()
    if not rows:
        raise NHLRegimeMixtureResearchError("NHL_REGIME_ROWS_EMPTY", "no source data")
    for r in rows:
        if not isinstance(r, RegimeResearchRow):
            raise NHLRegimeMixtureResearchError("NHL_REGIME_ROW_TYPE_INVALID", "typed evidence required")
        if r.event_id in ids:
            raise NHLRegimeMixtureResearchError("NHL_REGIME_DUPLICATE_EVENT", r.event_id)
        ids.add(r.event_id)
        if (_date(r.feature_as_of) >= _date(r.start_utc)
            or not r.historical_reconstruction or r.archived_pregame_snapshot):
            raise NHLRegimeMixtureResearchError("NHL_REGIME_FEATURE_CHRONOLOGY_INVALID", r.event_id)
        if (r.terminal_regime not in REGIMES or type(r.total_goals) is not int
            or r.total_goals < 1 or (r.terminal_regime != "REG" and r.total_goals % 2 != 1)):
            raise NHLRegimeMixtureResearchError("NHL_REGIME_OUTCOME_INVALID", r.event_id)
        if (len(r.features) != FEATURE_COUNT
            or any(type(x) not in (float,int) or not isfinite(x) for x in r.features)):
            raise NHLRegimeMixtureResearchError("NHL_REGIME_FEATURES_INVALID", r.event_id)
        for name, digest in (("D1", r.official_source_sha256),
                             ("REGIME", r.outcome_regime_sha256)):
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                raise NHLRegimeMixtureResearchError("NHL_REGIME_SOURCE_HASH_INVALID", f"{r.event_id}:{name}")
        if r.season_start_year not in (*TRAIN_YEARS, CALIBRATION_YEAR, DEVELOPMENT_YEAR, FORWARD_RESERVE_YEAR):
            raise NHLRegimeMixtureResearchError("NHL_REGIME_SEASON_UNSUPPORTED", r.event_id)


def _softmax(values: np.ndarray) -> np.ndarray:
    centered = values - np.max(values, axis=-1, keepdims=True)
    z = np.exp(centered)
    return z / np.sum(z, axis=-1, keepdims=True)


def _components(artifact: RegimeMixtureArtifact, features: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    if len(features) != FEATURE_COUNT or not all(isfinite(x) for x in features):
        raise NHLRegimeMixtureResearchError("NHL_REGIME_SCORING_FEATURES_INVALID", "invalid research inputs")
    x = (np.asarray(features, dtype=float) - np.asarray(artifact.scaler_mean)) / np.asarray(artifact.scaler_scale)
    logits = np.asarray(artifact.logistic_coef) @ x + np.asarray(artifact.logistic_intercept)
    p = _softmax(logits) * np.asarray(artifact.class_calibration_weights)
    p /= float(np.sum(p))
    mu = np.exp(np.asarray(artifact.poisson_coef) @ x + np.asarray(artifact.poisson_intercept))
    mu *= np.asarray(artifact.poisson_mean_multipliers)
    if not np.isfinite(p).all() or not np.isfinite(mu).all() or np.any(mu <= 0) or np.any(mu > 40):
        raise NHLRegimeMixtureResearchError("NHL_REGIME_DISTRIBUTION_INVALID", "invalid model components")
    return p, mu


def research_pmf_at(artifact: RegimeMixtureArtifact, features: Sequence[float], goals: int) -> float:
    if type(goals) is not int or goals < 0:
        raise NHLRegimeMixtureResearchError("NHL_REGIME_GOAL_COUNT_INVALID", str(goals))
    probs, mean_goals = _components(artifact, features)
    weight = dict(zip(artifact.logistic_class_order, probs))
    lam = dict(zip(artifact.logistic_class_order, mean_goals))
    value = 0.0
    if goals > 0:
        value += float(weight["REG"] * poisson.pmf(goals, lam["REG"]) / (-np.expm1(-lam["REG"])))
        if goals % 2:
            z = (goals-1)//2
            value += float(weight["OT"] * poisson.pmf(z, lam["OT"]))
            value += float(weight["SO"] * poisson.pmf(z, lam["SO"]))
    return value


def research_exact_line(artifact: RegimeMixtureArtifact, features: Sequence[float],
                        *, line: float, side: str) -> dict[str, Any]:
    if type(line) not in (float,int) or not isfinite(line) or line < 0 or line > 30 or line*2 != round(line*2):
        raise NHLRegimeMixtureResearchError("NHL_REGIME_LINE_UNSUPPORTED", str(line))
    if side not in ("UNDER", "OVER"):
        raise NHLRegimeMixtureResearchError("NHL_REGIME_DIRECTION_INVALID", str(side))
    # This is *exact* CDF evaluation at the selected threshold. Complement
    # captures the infinite count tail; there is no truncated/reweighted PMF.
    win_under = sum(research_pmf_at(artifact, features, k) for k in range(int(np.ceil(line))))
    push = research_pmf_at(artifact, features, int(line)) if float(line).is_integer() else 0.0
    win_over = 1.0 - win_under - push
    if win_over < -1e-8 or abs(win_under + push + win_over - 1.0) > 1e-8:
        raise NHLRegimeMixtureResearchError("NHL_REGIME_MASS_INVALID", "distribution failed")
    win, loss = (win_under, win_over) if side == "UNDER" else (win_over, win_under)
    return {
        "model_family": MODEL_FAMILY,
        "research_win_probability": win, "research_push_probability": push,
        "research_loss_probability": loss,
        "certified_model_probability": None, "calibrated_probability": None,
        "calibrated_lower_bound": None, "probability_publishable": False,
        "rank_eligible": False, "can_execute": False,
    }


def fit_regime_mixture_research(
    rows: Sequence[RegimeResearchRow], *,
    min_train: int = 300, min_calibration: int = 120, min_development: int = 120,
) -> tuple[RegimeMixtureArtifact, dict[str, Any]]:
    _validate_rows(rows)
    train = [r for r in rows if r.season_start_year in TRAIN_YEARS]
    cal = [r for r in rows if r.season_start_year == CALIBRATION_YEAR]
    dev = [r for r in rows if r.season_start_year == DEVELOPMENT_YEAR]
    reserve = [r for r in rows if r.season_start_year == FORWARD_RESERVE_YEAR]
    if min(len(train)/min_train, len(cal)/min_calibration, len(dev)/min_development) < 1:
        raise NHLRegimeMixtureResearchError("NHL_REGIME_HOLDOUT_INSUFFICIENT",
                                           f"train={len(train)}, cal={len(cal)}, dev={len(dev)}")
    if any(sum(r.terminal_regime == cls for r in train) < 30 or
           sum(r.terminal_regime == cls for r in cal) < 15 for cls in REGIMES):
        raise NHLRegimeMixtureResearchError("NHL_REGIME_CLASS_HISTORY_INSUFFICIENT",
                                           "all three terminal regimes need historical support")
    xtrain = np.asarray([r.features for r in train], dtype=float)
    scaler = StandardScaler().fit(xtrain)
    xt = scaler.transform(xtrain)
    ycls = np.asarray([r.terminal_regime for r in train])
    model = LogisticRegression(C=1.0, max_iter=1000, solver="lbfgs").fit(xt, ycls)
    classes = tuple(str(v) for v in model.classes_)
    if classes != REGIMES:
        # sklearn classes sorted alphabetically -> OT, REG, SO.
        if set(classes) != set(REGIMES):
            raise NHLRegimeMixtureResearchError("NHL_REGIME_CLASSES_MISSING", str(classes))
    xcal = scaler.transform([r.features for r in cal])
    base_class = model.predict_proba(xcal)
    class_weights = []
    all_poisson_coef = []
    all_poisson_intercept = []
    mean_factors = []
    for cls in classes:
        index = classes.index(cls)
        observed = sum(r.terminal_regime == cls for r in cal)/len(cal)
        forecast = float(np.mean(base_class[:, index]))
        factor = observed/forecast
        if not isfinite(factor) or not 0.20 < factor < 5:
            raise NHLRegimeMixtureResearchError("NHL_REGIME_CLASS_CALIBRATION_INVALID", cls)
        class_weights.append(factor)
        rows_train = [r for r in train if r.terminal_regime == cls]
        rows_cal = [r for r in cal if r.terminal_regime == cls]
        yh = np.array([r.total_goals if cls=="REG" else (r.total_goals-1)//2 for r in rows_train])
        yh_cal = np.array([r.total_goals if cls=="REG" else (r.total_goals-1)//2 for r in rows_cal])
        component = PoissonRegressor(alpha=FIXED_ALPHA, max_iter=1000).fit(
            scaler.transform([r.features for r in rows_train]), yh
        )
        pred_cal = component.predict(scaler.transform([r.features for r in rows_cal]))
        mean_factor = float(np.mean(yh_cal)/np.mean(pred_cal))
        if not isfinite(mean_factor) or not 0.20 < mean_factor < 5:
            raise NHLRegimeMixtureResearchError("NHL_REGIME_COUNT_CALIBRATION_INVALID", cls)
        all_poisson_coef.append(tuple(float(v) for v in component.coef_))
        all_poisson_intercept.append(float(component.intercept_))
        mean_factors.append(mean_factor)
    data_hash = sha256(json.dumps([asdict(r) for r in train], sort_keys=True,
                                  separators=(",", ":")).encode()).hexdigest()
    artifact = RegimeMixtureArtifact(
        model_family=MODEL_FAMILY, feature_count=FEATURE_COUNT,
        train_n=len(train), calibration_n=len(cal), development_n=len(dev),
        training_hash=data_hash,
        scaler_mean=tuple(float(v) for v in scaler.mean_),
        scaler_scale=tuple(float(v) for v in scaler.scale_),
        logistic_class_order=classes,
        logistic_coef=tuple(tuple(float(v) for v in row) for row in model.coef_),
        logistic_intercept=tuple(float(v) for v in model.intercept_),
        class_calibration_weights=tuple(class_weights),
        poisson_coef=tuple(all_poisson_coef),
        poisson_intercept=tuple(all_poisson_intercept),
        poisson_mean_multipliers=tuple(mean_factors),
    )
    dev_under = []
    dev_push = []
    log_losses = []
    for row in dev:
        prob = research_exact_line(artifact, row.features, line=5.5, side="UNDER")
        push = research_exact_line(artifact, row.features, line=6.0, side="UNDER")
        dev_under.append((prob["research_win_probability"] - float(row.total_goals < 5.5))**2)
        dev_push.append((push["research_push_probability"] - float(row.total_goals == 6))**2)
        likelihood = research_pmf_at(artifact, row.features, row.total_goals)
        if likelihood <= 0:
            raise NHLRegimeMixtureResearchError("NHL_REGIME_DEV_DENSITY_INVALID", row.event_id)
        log_losses.append(-log(likelihood))
    return artifact, {
        "status": "RESEARCH_REGIME_MIXTURE_FITTED_HOLD",
        "model_family": MODEL_FAMILY,
        "train_n": len(train), "calibration_n": len(cal), "development_n": len(dev),
        "forward_reserve_not_used": len(reserve),
        "training_hash": data_hash,
        "research_development_under55_brier": float(np.mean(dev_under)),
        "research_development_exact_six_brier": float(np.mean(dev_push)),
        "research_development_count_nll": float(np.mean(log_losses)),
        "selection_cohort_already_seen_in_prior_research": True,
        "archived_pregame_snapshots": False,
        "goalie_fitted": False,
        "governed_probability_calibration": False,
        "calibrated_lower_bound": None,
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "rank_eligible": False, "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
