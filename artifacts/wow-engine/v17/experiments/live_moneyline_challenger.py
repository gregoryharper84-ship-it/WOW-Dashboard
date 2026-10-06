"""Class-C research challengers for MLB/NFL in-play moneyline probability.

Research only. This module cannot publish, promote, rank, execute, or mutate a
production champion. Historical state must be point-in-time valid. Sportsbook
prices, external win probabilities, and pregame WOW probabilities are forbidden
features.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
MODEL_FAMILY = {
    "NFL": "NFL_LIVE_MONEYLINE_LOGREG_V1_CHALLENGER",
    "MLB": "MLB_LIVE_MONEYLINE_LOGREG_V1_CHALLENGER",
}
BANNED_FEATURE_TOKENS = (
    "wp", "win_probability", "vegas", "odds", "moneyline", "implied",
    "market", "spread", "favorite", "underdog", "pregame_probability",
)
MIN_TRAIN_ROWS = 1000
MIN_CAL_ROWS = 300
MIN_VAL_ROWS = 300


class LiveChallengerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResearchBundle:
    sport: str
    feature_names: tuple[str, ...]
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    platt_a: float
    platt_b: float
    validation_metrics: dict[str, Any]
    calibration_bins: tuple[dict[str, float], ...]
    artifact_checksum: str
    probability_publishable: bool = False
    promotion_authorized: bool = False
    can_execute: bool = False


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sigmoid(x: np.ndarray | float) -> np.ndarray:
    arr = np.clip(np.asarray(x, dtype=float), -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-arr))


def _ece(y: np.ndarray, p: np.ndarray, bins: int = 15) -> float:
    if len(y) == 0:
        return float("inf")
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = float(len(y))
    out = 0.0
    for idx in range(bins):
        lo, hi = edges[idx], edges[idx + 1]
        mask = (p >= lo) & (p <= hi if idx == bins - 1 else p < hi)
        n = int(mask.sum())
        if n:
            out += (n / total) * abs(float(p[mask].mean()) - float(y[mask].mean()))
    return float(out)


def _wilson_lower(successes: int, n: int, z: float = 1.6448536269514722) -> float:
    if n <= 0:
        return 0.0
    phat = successes / n
    denom = 1.0 + (z * z / n)
    center = phat + (z * z / (2.0 * n))
    radius = z * math.sqrt((phat * (1.0 - phat) / n) + (z * z / (4.0 * n * n)))
    return max(0.0, (center - radius) / denom)


def _feature_guard(feature_names: Sequence[str]) -> None:
    if not feature_names:
        raise LiveChallengerError("LIVE_CHALLENGER_FEATURES_EMPTY")
    lowered = [str(name).casefold() for name in feature_names]
    bad = [
        name for name in lowered
        if any(token in name for token in BANNED_FEATURE_TOKENS)
    ]
    if bad:
        raise LiveChallengerError("LIVE_CHALLENGER_FORBIDDEN_FEATURE:" + ",".join(sorted(set(bad))))


def _clean_frame(frame: pd.DataFrame, feature_names: Sequence[str]) -> pd.DataFrame:
    needed = [*feature_names, "home_win", "game_id"]
    missing = [name for name in needed if name not in frame.columns]
    if missing:
        raise LiveChallengerError("LIVE_CHALLENGER_COLUMNS_MISSING:" + ",".join(missing))
    out = frame[needed].copy()
    for name in feature_names:
        out[name] = pd.to_numeric(out[name], errors="coerce")
    out["home_win"] = pd.to_numeric(out["home_win"], errors="coerce")
    out = out.replace([np.inf, -np.inf], np.nan).dropna()
    out = out[out["home_win"].isin([0, 1])]
    return out


def _calibration_bins(y: np.ndarray, p: np.ndarray, *, bins: int = 12) -> tuple[dict[str, float], ...]:
    if len(y) < bins:
        raise LiveChallengerError("LIVE_CHALLENGER_CALIBRATION_SAMPLE_TOO_THIN")
    order = np.argsort(p)
    chunks = np.array_split(order, bins)
    result: list[dict[str, float]] = []
    for idx, chunk in enumerate(chunks):
        if len(chunk) == 0:
            continue
        yy = y[chunk]
        pp = p[chunk]
        successes = int(yy.sum())
        result.append({
            "bin": float(idx),
            "n": float(len(chunk)),
            "predicted_mean": float(pp.mean()),
            "observed_rate": float(yy.mean()),
            "wilson90_lower": float(_wilson_lower(successes, len(chunk))),
            "min_probability": float(pp.min()),
            "max_probability": float(pp.max()),
        })
    return tuple(result)


def fit_research_bundle(
    *,
    sport: str,
    train: pd.DataFrame,
    calibration: pd.DataFrame,
    validation: pd.DataFrame,
    feature_names: Sequence[str],
) -> ResearchBundle:
    sport = str(sport).upper()
    if sport not in MODEL_FAMILY:
        raise LiveChallengerError("LIVE_CHALLENGER_UNSUPPORTED_SPORT")
    _feature_guard(feature_names)
    feature_names = tuple(feature_names)
    train = _clean_frame(train, feature_names)
    calibration = _clean_frame(calibration, feature_names)
    validation = _clean_frame(validation, feature_names)
    if len(train) < MIN_TRAIN_ROWS or len(calibration) < MIN_CAL_ROWS or len(validation) < MIN_VAL_ROWS:
        raise LiveChallengerError(
            f"LIVE_CHALLENGER_TEMPORAL_SPLIT_TOO_THIN:{len(train)}:{len(calibration)}:{len(validation)}"
        )

    x_train = train.loc[:, feature_names].to_numpy(float)
    y_train = train["home_win"].to_numpy(int)
    x_cal = calibration.loc[:, feature_names].to_numpy(float)
    y_cal = calibration["home_win"].to_numpy(int)
    x_val = validation.loc[:, feature_names].to_numpy(float)
    y_val = validation["home_win"].to_numpy(int)
    if any(len(np.unique(y)) != 2 for y in (y_train, y_cal, y_val)):
        raise LiveChallengerError("LIVE_CHALLENGER_TEMPORAL_SPLIT_CLASS_IMBALANCE")

    scaler = StandardScaler()
    base = LogisticRegression(solver="lbfgs", max_iter=3000, random_state=17)
    base.fit(scaler.fit_transform(x_train), y_train)

    cal_logits = base.decision_function(scaler.transform(x_cal)).reshape(-1, 1)
    platt = LogisticRegression(solver="lbfgs", max_iter=2000, random_state=17, penalty=None)
    platt.fit(cal_logits, y_cal)

    val_logits = base.decision_function(scaler.transform(x_val))
    raw = _sigmoid(val_logits)
    calibrated = _sigmoid(float(platt.coef_[0, 0]) * val_logits + float(platt.intercept_[0]))
    base_rate = float(y_cal.mean())
    baseline = np.full(len(y_val), base_rate, dtype=float)

    metrics = {
        "train_n": int(len(train)),
        "calibration_n": int(len(calibration)),
        "validation_n": int(len(validation)),
        "validation_game_n": int(validation["game_id"].nunique()),
        "validation_brier": float(brier_score_loss(y_val, calibrated)),
        "validation_log_loss": float(log_loss(y_val, calibrated, labels=[0, 1])),
        "validation_auc": float(roc_auc_score(y_val, calibrated)),
        "validation_ece15": _ece(y_val, calibrated, 15),
        "raw_validation_brier": float(brier_score_loss(y_val, raw)),
        "baseline_home_rate": base_rate,
        "baseline_brier": float(brier_score_loss(y_val, baseline)),
        "baseline_log_loss": float(log_loss(y_val, baseline, labels=[0, 1])),
        "platt_positive_slope": bool(float(platt.coef_[0, 0]) > 0.0),
    }
    metrics["research_gate_pass"] = bool(
        metrics["validation_brier"] < metrics["baseline_brier"]
        and metrics["validation_log_loss"] < metrics["baseline_log_loss"]
        and metrics["validation_auc"] >= 0.50
        and metrics["validation_ece15"] <= 0.12
        and metrics["platt_positive_slope"]
    )

    payload = {
        "sport": sport,
        "model_family": MODEL_FAMILY[sport],
        "feature_names": list(feature_names),
        "scaler_mean": [float(v) for v in scaler.mean_],
        "scaler_scale": [float(v) for v in scaler.scale_],
        "coefficients": [float(v) for v in base.coef_[0]],
        "intercept": float(base.intercept_[0]),
        "platt_a": float(platt.coef_[0, 0]),
        "platt_b": float(platt.intercept_[0]),
        "validation_metrics": metrics,
        "calibration_bins": list(_calibration_bins(y_cal, _sigmoid(float(platt.coef_[0, 0]) * cal_logits.ravel() + float(platt.intercept_[0])))),
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
    }
    checksum = _sha(payload)
    return ResearchBundle(
        sport=sport,
        feature_names=feature_names,
        scaler_mean=tuple(payload["scaler_mean"]),
        scaler_scale=tuple(payload["scaler_scale"]),
        coefficients=tuple(payload["coefficients"]),
        intercept=payload["intercept"],
        platt_a=payload["platt_a"],
        platt_b=payload["platt_b"],
        validation_metrics=metrics,
        calibration_bins=tuple(payload["calibration_bins"]),
        artifact_checksum=checksum,
    )


def score_research_state(bundle: ResearchBundle, features: dict[str, float]) -> dict[str, Any]:
    if set(features) != set(bundle.feature_names):
        raise LiveChallengerError("LIVE_CHALLENGER_INFERENCE_FEATURE_SET_MISMATCH")
    x = np.asarray([float(features[name]) for name in bundle.feature_names], dtype=float)
    mean = np.asarray(bundle.scaler_mean, dtype=float)
    scale = np.asarray(bundle.scaler_scale, dtype=float)
    coef = np.asarray(bundle.coefficients, dtype=float)
    if not np.all(np.isfinite(x)):
        raise LiveChallengerError("LIVE_CHALLENGER_INFERENCE_NONFINITE")
    logit = float(np.dot(coef, (x - mean) / scale) + bundle.intercept)
    p_home = float(_sigmoid(bundle.platt_a * logit + bundle.platt_b))
    p_away = 1.0 - p_home
    if not 0.0 < p_home < 1.0 or abs((p_home + p_away) - 1.0) > 1e-12:
        raise LiveChallengerError("LIVE_CHALLENGER_OUTPUT_INVALID")
    return {
        "research_home_probability": p_home,
        "research_away_probability": p_away,
        "probability_publishable": False,
        "rank_eligible": False,
        "promotion_authorized": False,
        "can_execute": False,
    }


def research_receipt(bundle: ResearchBundle, *, source_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "EXPERIMENT_CREATED",
        "sport": bundle.sport,
        "model_family": MODEL_FAMILY[bundle.sport],
        "feature_names": list(bundle.feature_names),
        "artifact_checksum": bundle.artifact_checksum,
        "validation_metrics": bundle.validation_metrics,
        "calibration_bins": list(bundle.calibration_bins),
        "source_manifest": source_manifest,
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
    }
