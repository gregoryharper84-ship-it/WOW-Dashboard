"""Chronological NCAAF candidate lifecycle: train -> calibration -> untouched test.

This module creates research artifacts only. It never promotes a model, never marks
calibration PASS, never publishes a probability, and never enables execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import sqrt
from typing import Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

from ncaaf_trainer import FEATURES, TrainingRow, NCAAFTrainingError

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
MODEL_FAMILY = "NCAAF_LOGISTIC_V1"
CALIBRATION_METHOD = "EMPIRICAL_WILSON_BINS_V1"


@dataclass(frozen=True)
class ThreeWayMetrics:
    train_n: int
    calibration_n: int
    test_n: int
    raw_test_brier: float
    calibrated_test_brier: float
    baseline_test_brier: float
    raw_test_log_loss: float
    calibrated_test_log_loss: float
    baseline_test_log_loss: float


@dataclass(frozen=True)
class ThreeWayCandidate:
    artifact_payload: Mapping[str, object]
    calibrator_payload: Mapping[str, object]
    metrics: ThreeWayMetrics
    dataset_hash: str
    calibration_start_event: str
    calibration_end_event: str
    test_start_event: str
    test_end_event: str
    probability_publishable: bool = False
    can_execute: bool = False


def _matrix(rows: Sequence[TrainingRow]) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for row in rows:
        if row.feature_as_of >= row.event_start_time:
            raise NCAAFTrainingError("NCAAF_TRAINING_LEAKAGE_DETECTED", "feature_as_of must be strictly pregame")
        values = []
        for name in FEATURES:
            value = row.features.get(name)
            if value is None:
                raise NCAAFTrainingError("NCAAF_TRAINING_FEATURE_MISSING", name)
            number = float(value)
            if not np.isfinite(number):
                raise NCAAFTrainingError("NCAAF_TRAINING_FEATURE_INVALID", name)
            values.append(number)
        xs.append(values)
        ys.append(1 if row.home_won else 0)
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=int)


def _wilson(wins: float, n: float, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("n must be positive")
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _fit_bins(probabilities: np.ndarray, outcomes: np.ndarray, *, bin_n: int = 10) -> dict[str, object]:
    if len(probabilities) < 50:
        raise NCAAFTrainingError("NCAAF_CALIBRATION_SAMPLE_INSUFFICIENT", "At least 50 calibration rows are required")
    order = np.argsort(probabilities, kind="stable")
    chunks = np.array_split(order, min(bin_n, len(order)))
    bins = []
    for chunk in chunks:
        if len(chunk) == 0:
            continue
        ps = probabilities[chunk]
        ys = outcomes[chunk]
        wins = int(np.sum(ys))
        n = int(len(chunk))
        observed = wins / n
        point = (wins + 1.0) / (n + 2.0)
        lower, upper = _wilson(float(wins), float(n))
        bins.append({
            "raw_min": float(np.min(ps)),
            "raw_max": float(np.max(ps)),
            "raw_mean": float(np.mean(ps)),
            "n": n,
            "wins": wins,
            "observed_rate": observed,
            "calibrated_probability": point,
            "wilson_lower": lower,
            "wilson_upper": upper,
        })
    return {"method": CALIBRATION_METHOD, "bins": bins, "training_n": int(len(probabilities)), "binning": "CHRONOLOGICAL_CALIBRATION_BLOCK_EQUAL_COUNT"}


def _apply_bins(probabilities: np.ndarray, payload: Mapping[str, object]) -> np.ndarray:
    bins = list(payload["bins"])
    if not bins:
        raise NCAAFTrainingError("NCAAF_CALIBRATOR_EMPTY", "calibrator contains no bins")
    centers = np.asarray([float(b["raw_mean"]) for b in bins])
    mapped = []
    for p in probabilities:
        idx = int(np.argmin(np.abs(centers - float(p))))
        mapped.append(float(bins[idx]["calibrated_probability"]))
    return np.clip(np.asarray(mapped, dtype=float), 1e-6, 1.0 - 1e-6)


def _hash_rows(rows: Sequence[TrainingRow]) -> str:
    payload = [{
        "event_start_time": r.event_start_time,
        "feature_as_of": r.feature_as_of,
        "home_won": r.home_won,
        "features": {k: float(r.features[k]) for k in FEATURES},
    } for r in rows]
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def train_calibrate_test(rows: Sequence[TrainingRow], *, min_rows: int = 300,
                         train_fraction: float = 0.60, calibration_fraction: float = 0.20) -> ThreeWayCandidate:
    if len(rows) < min_rows:
        raise NCAAFTrainingError("NCAAF_THREE_WAY_SAMPLE_INSUFFICIENT", f"At least {min_rows} complete settled rows are required; got {len(rows)}")
    if not (0.50 <= train_fraction <= 0.75) or not (0.15 <= calibration_fraction <= 0.25):
        raise ValueError("unsupported split")
    if train_fraction + calibration_fraction >= 0.90:
        raise ValueError("at least 10% must remain untouched for test")
    ordered = sorted(rows, key=lambda r: r.event_start_time)
    if list(rows) != ordered:
        raise NCAAFTrainingError("NCAAF_TRAINING_ROWS_NOT_CHRONOLOGICAL", "Rows must be ascending by event_start_time")

    X, y = _matrix(rows)
    train_end = int(len(rows) * train_fraction)
    cal_end = int(len(rows) * (train_fraction + calibration_fraction))
    X_train, y_train = X[:train_end], y[:train_end]
    X_cal, y_cal = X[train_end:cal_end], y[train_end:cal_end]
    X_test, y_test = X[cal_end:], y[cal_end:]
    if len(y_cal) < 50 or len(y_test) < 50:
        raise NCAAFTrainingError("NCAAF_THREE_WAY_PARTITION_INSUFFICIENT", "Calibration and untouched test must each contain at least 50 rows")
    for name, partition in (("train", y_train), ("calibration", y_cal), ("test", y_test)):
        if len(np.unique(partition)) < 2:
            raise NCAAFTrainingError("NCAAF_TRAINING_CLASS_DEGENERATE", f"{name} partition lacks both outcomes")

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=500, random_state=0)
    model.fit(scaler.transform(X_train), y_train)
    p_cal_raw = model.predict_proba(scaler.transform(X_cal))[:, 1]
    p_test_raw = model.predict_proba(scaler.transform(X_test))[:, 1]
    calibrator = _fit_bins(p_cal_raw, y_cal)
    p_test_cal = _apply_bins(p_test_raw, calibrator)
    prevalence = float(np.mean(y_train))
    baseline = np.full(len(y_test), prevalence, dtype=float)

    metrics = ThreeWayMetrics(
        train_n=len(y_train), calibration_n=len(y_cal), test_n=len(y_test),
        raw_test_brier=float(brier_score_loss(y_test, p_test_raw)),
        calibrated_test_brier=float(brier_score_loss(y_test, p_test_cal)),
        baseline_test_brier=float(brier_score_loss(y_test, baseline)),
        raw_test_log_loss=float(log_loss(y_test, p_test_raw, labels=[0, 1])),
        calibrated_test_log_loss=float(log_loss(y_test, p_test_cal, labels=[0, 1])),
        baseline_test_log_loss=float(log_loss(y_test, baseline, labels=[0, 1])),
    )
    artifact = {
        "feature_names": list(FEATURES),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "intercept": float(model.intercept_[0]),
        "coefficients": model.coef_[0].tolist(),
        "train_end_index": train_end,
        "calibration_end_index": cal_end,
        "split_policy": "CHRONOLOGICAL_60_20_20",
    }
    return ThreeWayCandidate(
        artifact_payload=artifact,
        calibrator_payload=calibrator,
        metrics=metrics,
        dataset_hash=_hash_rows(rows),
        calibration_start_event=rows[train_end].event_start_time,
        calibration_end_event=rows[cal_end - 1].event_start_time,
        test_start_event=rows[cal_end].event_start_time,
        test_end_event=rows[-1].event_start_time,
    )


def candidate_clears_research_screen(candidate: ThreeWayCandidate) -> bool:
    """Necessary research screen, never equivalent to calibration-health/trust promotion."""
    m = candidate.metrics
    return (
        m.raw_test_brier < m.baseline_test_brier
        and m.raw_test_log_loss <= m.baseline_test_log_loss
        and m.calibrated_test_brier <= m.raw_test_brier
        and m.calibrated_test_log_loss <= m.raw_test_log_loss
    )
