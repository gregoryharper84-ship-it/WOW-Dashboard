"""Research-only NFL ML challenger: remove cumulative prior-game counters.

Class C experiment for #1342. This module never registers, promotes, publishes,
persists, or executes a sporting probability. It refits the incumbent
logistic-regression + Platt pipeline on the identical chronological split after
removing only home_prior_games and away_prior_games.

The production scorer remains nfl_event_model_v17.py.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

import numpy as np

from nfl_event_features_p2 import FEATURE_ORDER
from nfl_event_model_v17 import (
    CALIBRATION_SEASON,
    MIN_CALIBRATION_N,
    MIN_TRAIN_N,
    MIN_VALIDATION_N,
    TRAIN_SEASONS,
    VALIDATION_SEASON,
)

CHALLENGER_ID = "NFL_ML_REMOVE_CUMULATIVE_PRIOR_GAMES_V1"
ABLATION_FEATURES = ("home_prior_games", "away_prior_games")
CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def challenger_feature_order() -> tuple[str, ...]:
    retained = tuple(name for name in FEATURE_ORDER if name not in ABLATION_FEATURES)
    if len(retained) != len(FEATURE_ORDER) - len(ABLATION_FEATURES):
        raise ValueError("NFL_PRIOR_GAMES_ABLATION_FEATURE_ORDER_INVALID")
    return retained


def _project_row(row: dict[str, Any]) -> list[float]:
    order = list(row.get("feature_order") or [])
    vector = row.get("feature_vector")
    if order != list(FEATURE_ORDER):
        raise ValueError("NFL_PRIOR_GAMES_ABLATION_SOURCE_ORDER_MISMATCH")
    if not isinstance(vector, list) or len(vector) != len(FEATURE_ORDER):
        raise ValueError("NFL_PRIOR_GAMES_ABLATION_SOURCE_VECTOR_INVALID")
    by_name = dict(zip(order, vector))
    projected = [float(by_name[name]) for name in challenger_feature_order()]
    if not all(math.isfinite(value) for value in projected):
        raise ValueError("NFL_PRIOR_GAMES_ABLATION_NONFINITE_INPUT")
    return projected


def _split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    train = [row for row in rows if int(row["season"]) in TRAIN_SEASONS]
    calibration = [row for row in rows if int(row["season"]) == CALIBRATION_SEASON]
    validation = [row for row in rows if int(row["season"]) == VALIDATION_SEASON]
    return train, calibration, validation


def _xy(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([_project_row(row) for row in rows], dtype=float)
    y = np.asarray([1 if row["target_outcome"] == "HOME_WIN" else 0 for row in rows], dtype=int)
    return x, y


def _sigmoid(value: np.ndarray | float) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    arr = np.clip(arr, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-arr))


def _ece(y_true: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    total = len(y_true)
    if total == 0:
        return float("inf")
    error = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for idx in range(bins):
        lo, hi = edges[idx], edges[idx + 1]
        mask = (probabilities >= lo) & (
            probabilities <= hi if idx == bins - 1 else probabilities < hi
        )
        count = int(mask.sum())
        if count:
            error += (count / total) * abs(
                float(probabilities[mask].mean()) - float(y_true[mask].mean())
            )
    return float(error)


def fit_prior_games_ablation(
    rows: Iterable[dict[str, Any]],
    *,
    training_code_sha: str,
) -> dict[str, Any]:
    """Fit the research-only ablation on the incumbent temporal split.

    Returned gate results are retrospective experiment diagnostics only. They do
    not certify or promote the challenger.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    from sklearn.preprocessing import StandardScaler

    usable = [
        dict(row)
        for row in rows
        if str(row.get("target_outcome") or "") in {"HOME_WIN", "AWAY_WIN"}
    ]
    train, calibration, validation = _split(usable)
    if (
        len(train) < MIN_TRAIN_N
        or len(calibration) < MIN_CALIBRATION_N
        or len(validation) < MIN_VALIDATION_N
    ):
        raise ValueError(
            "NFL_PRIOR_GAMES_ABLATION_TEMPORAL_SPLIT_TOO_THIN:"
            f"{len(train)}:{len(calibration)}:{len(validation)}"
        )

    x_train, y_train = _xy(train)
    x_cal, y_cal = _xy(calibration)
    x_val, y_val = _xy(validation)
    for label, y in (("TRAIN", y_train), ("CAL", y_cal), ("VAL", y_val)):
        if len(set(y.tolist())) != 2:
            raise ValueError(f"NFL_PRIOR_GAMES_ABLATION_{label}_CLASS_IMBALANCE")

    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    base = LogisticRegression(solver="lbfgs", max_iter=4000, random_state=17)
    base.fit(x_train_scaled, y_train)

    cal_logits = base.decision_function(scaler.transform(x_cal)).reshape(-1, 1)
    platt = LogisticRegression(
        solver="lbfgs",
        max_iter=2000,
        random_state=17,
        penalty=None,
    )
    platt.fit(cal_logits, y_cal)
    platt_a = float(platt.coef_[0, 0])
    platt_b = float(platt.intercept_[0])

    val_logits = base.decision_function(scaler.transform(x_val))
    raw_val = _sigmoid(val_logits)
    calibrated_val = _sigmoid(platt_a * val_logits + platt_b)

    brier = float(brier_score_loss(y_val, calibrated_val))
    ll = float(
        log_loss(
            y_val,
            np.column_stack([1.0 - calibrated_val, calibrated_val]),
            labels=[0, 1],
        )
    )
    auc = float(roc_auc_score(y_val, calibrated_val))
    ece = _ece(y_val, calibrated_val)

    baseline_p = float(y_cal.mean())
    baseline_predictions = np.full(len(y_val), baseline_p, dtype=float)
    baseline_brier = float(brier_score_loss(y_val, baseline_predictions))
    baseline_ll = float(
        log_loss(
            y_val,
            np.column_stack([1.0 - baseline_predictions, baseline_predictions]),
            labels=[0, 1],
        )
    )

    retrospective_gate_checks = {
        "train_n": len(train) >= MIN_TRAIN_N,
        "calibration_n": len(calibration) >= MIN_CALIBRATION_N,
        "validation_n": len(validation) >= MIN_VALIDATION_N,
        "beats_baseline_brier": brier < baseline_brier,
        "beats_baseline_log_loss": ll < baseline_ll,
        "auc_not_below_chance": auc >= 0.50,
        "ece_within_incumbent_limit": ece <= 0.12,
        "platt_positive_slope": platt_a > 0.0,
    }

    metrics = {
        "train_n": len(train),
        "calibration_n": len(calibration),
        "validation_n": len(validation),
        "validation_brier_score": brier,
        "validation_log_loss": ll,
        "validation_auc": auc,
        "validation_ece_10": ece,
        "raw_validation_brier_score": float(brier_score_loss(y_val, raw_val)),
        "baseline_probability": baseline_p,
        "baseline_brier_score": baseline_brier,
        "baseline_log_loss": baseline_ll,
        "retrospective_gate_checks": retrospective_gate_checks,
        "retrospective_gate_pass": all(retrospective_gate_checks.values()),
    }

    payload = {
        "challenger_id": CHALLENGER_ID,
        "ablated_features": list(ABLATION_FEATURES),
        "feature_order": list(challenger_feature_order()),
        "feature_order_hash": _sha256_json(list(challenger_feature_order())),
        "scaler_mean": [float(v) for v in scaler.mean_.tolist()],
        "scaler_scale": [float(v) for v in scaler.scale_.tolist()],
        "coefficients": [float(v) for v in base.coef_[0].tolist()],
        "intercept": float(base.intercept_[0]),
        "platt_a": platt_a,
        "platt_b": platt_b,
        "temporal_split": {
            "train_seasons": list(TRAIN_SEASONS),
            "calibration_season": CALIBRATION_SEASON,
            "validation_season": VALIDATION_SEASON,
        },
        "training_code_sha": str(training_code_sha),
    }

    return {
        "status": "CHALLENGER_ONLY",
        "challenger_id": CHALLENGER_ID,
        "metrics": metrics,
        "artifact_payload": payload,
        "artifact_checksum": _sha256_json(payload),
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "promotion_authorized": PROMOTION_AUTHORIZED,
        "can_execute": CAN_EXECUTE,
    }
