"""Reusable V17 lifecycle for multi-outcome team/event research candidates.

Designed for sporting outcome spaces such as soccer 1X2. It enforces temporal
separation, immutable source manifests, chronological train/calibration/test
partitions, deterministic multinomial logistic fitting and temperature
calibration. It never certifies, promotes, publishes, or executes.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite, sqrt
from typing import Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
CALIBRATION_METHOD = "TEMPERATURE_SCALE_CHRONOLOGICAL_V1"


class MulticlassCandidateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MulticlassTrainingRow:
    event_id: str
    event_start_time: str
    feature_as_of: str
    outcome: str
    features: Mapping[str, float]
    source_manifest_sha256: str


@dataclass(frozen=True)
class MulticlassMetrics:
    train_n: int
    calibration_n: int
    test_n: int
    raw_log_loss: float
    calibrated_log_loss: float
    baseline_log_loss: float
    raw_brier: float
    calibrated_brier: float
    baseline_brier: float
    raw_ece: float
    calibrated_ece: float


@dataclass(frozen=True)
class MulticlassCandidate:
    model_family: str
    feature_names: tuple[str, ...]
    classes: tuple[str, ...]
    artifact_payload: Mapping[str, object]
    calibrator_payload: Mapping[str, object]
    metrics: MulticlassMetrics
    dataset_hash: str
    research_screen_pass: bool
    automatic_certification: bool = False
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False


def _validate_rows(rows: Sequence[MulticlassTrainingRow], names: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
    xs: list[list[float]] = []
    ys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        event_id = str(row.event_id or "").strip()
        if not event_id or event_id in seen:
            raise MulticlassCandidateError("MULTICLASS_EVENT_ID_INVALID", event_id)
        seen.add(event_id)
        if not isinstance(row.event_start_time, str) or not isinstance(row.feature_as_of, str):
            raise MulticlassCandidateError("MULTICLASS_EVENT_TIME_INVALID", event_id)
        if row.feature_as_of >= row.event_start_time:
            raise MulticlassCandidateError("MULTICLASS_TEMPORAL_LEAKAGE", event_id)
        manifest = str(row.source_manifest_sha256 or "").lower()
        if len(manifest) != 64 or any(ch not in "0123456789abcdef" for ch in manifest):
            raise MulticlassCandidateError("MULTICLASS_SOURCE_MANIFEST_HASH_INVALID", event_id)
        values: list[float] = []
        for name in names:
            value = row.features.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise MulticlassCandidateError("MULTICLASS_FEATURE_INVALID", f"{event_id}:{name}")
            values.append(float(value))
        outcome = str(row.outcome or "").strip().upper()
        if not outcome:
            raise MulticlassCandidateError("MULTICLASS_OUTCOME_INVALID", event_id)
        xs.append(values)
        ys.append(outcome)
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=object)


def _multiclass_brier(y: np.ndarray, probs: np.ndarray, classes: np.ndarray) -> float:
    lookup = {str(label): idx for idx, label in enumerate(classes)}
    onehot = np.zeros_like(probs, dtype=float)
    for i, label in enumerate(y):
        onehot[i, lookup[str(label)]] = 1.0
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def _ece(y: np.ndarray, probs: np.ndarray, classes: np.ndarray, bins: int = 10) -> float:
    pred = np.argmax(probs, axis=1)
    conf = np.max(probs, axis=1)
    actual = np.asarray([str(classes[i]) == str(label) for i, label in zip(pred, y)], dtype=float)
    error = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for i in range(bins):
        mask = (conf >= edges[i]) & (conf < edges[i + 1] if i < bins - 1 else conf <= edges[i + 1])
        n = int(mask.sum())
        if n:
            error += (n / len(conf)) * abs(float(conf[mask].mean()) - float(actual[mask].mean()))
    return float(error)


def _temperature_scale(probs: np.ndarray, temperature: float) -> np.ndarray:
    clipped = np.clip(probs, 1e-12, 1.0)
    logits = np.log(clipped) / float(temperature)
    logits -= logits.max(axis=1, keepdims=True)
    exps = np.exp(logits)
    return exps / exps.sum(axis=1, keepdims=True)


def _fit_temperature(probs: np.ndarray, y: np.ndarray, classes: np.ndarray) -> tuple[float, float]:
    best_t, best_loss = 1.0, float(log_loss(y, probs, labels=list(classes)))
    for t in np.linspace(0.50, 2.50, 161):
        calibrated = _temperature_scale(probs, float(t))
        loss = float(log_loss(y, calibrated, labels=list(classes)))
        if loss < best_loss - 1e-12:
            best_t, best_loss = float(t), loss
    return best_t, best_loss


def _wilson(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _class_reliability(y: np.ndarray, probs: np.ndarray, classes: np.ndarray) -> dict[str, list[dict[str, float | int]]]:
    output: dict[str, list[dict[str, float | int]]] = {}
    for class_idx, label in enumerate(classes):
        p = probs[:, class_idx]
        order = np.argsort(p, kind="stable")
        bins: list[dict[str, float | int]] = []
        for chunk in np.array_split(order, min(10, len(order))):
            if len(chunk) == 0:
                continue
            truth = np.asarray([1 if str(y[i]) == str(label) else 0 for i in chunk], dtype=int)
            wins, n = int(truth.sum()), int(len(chunk))
            lower, upper = _wilson(wins, n)
            bins.append({
                "probability_mean": float(p[chunk].mean()), "n": n, "wins": wins,
                "observed_rate": wins / n, "wilson_lower": lower, "wilson_upper": upper,
            })
        output[str(label)] = bins
    return output


def _dataset_hash(rows: Sequence[MulticlassTrainingRow], names: tuple[str, ...], family: str) -> str:
    payload = {
        "model_family": family, "feature_names": list(names),
        "rows": [{
            "event_id": row.event_id, "event_start_time": row.event_start_time,
            "feature_as_of": row.feature_as_of, "outcome": row.outcome,
            "features": {name: float(row.features[name]) for name in names},
            "source_manifest_sha256": row.source_manifest_sha256,
        } for row in rows],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def train_multiclass_candidate(
    rows: Sequence[MulticlassTrainingRow], *, model_family: str,
    feature_names: Sequence[str], expected_classes: Sequence[str], min_rows: int = 500,
) -> MulticlassCandidate:
    names = tuple(str(name).strip() for name in feature_names)
    classes_expected = tuple(str(label).strip().upper() for label in expected_classes)
    if not names or len(names) != len(set(names)):
        raise MulticlassCandidateError("MULTICLASS_FEATURE_SCHEMA_INVALID", "features must be unique")
    if len(classes_expected) < 3 or len(classes_expected) != len(set(classes_expected)):
        raise MulticlassCandidateError("MULTICLASS_OUTCOME_SPACE_INVALID", str(classes_expected))
    if len(rows) < min_rows:
        raise MulticlassCandidateError("MULTICLASS_SAMPLE_INSUFFICIENT", f"rows={len(rows)};minimum={min_rows}")
    ordered = sorted(rows, key=lambda row: (row.event_start_time, row.event_id))
    if list(rows) != ordered:
        raise MulticlassCandidateError("MULTICLASS_ROWS_NOT_CHRONOLOGICAL", "rows must be ascending")
    X, y = _validate_rows(rows, names)
    observed = tuple(sorted({str(v) for v in y}))
    if set(observed) != set(classes_expected):
        raise MulticlassCandidateError("MULTICLASS_CLASS_COVERAGE_INVALID", str(observed))
    train_end, cal_end = int(len(rows) * 0.60), int(len(rows) * 0.80)
    X_train, y_train = X[:train_end], y[:train_end]
    X_cal, y_cal = X[train_end:cal_end], y[train_end:cal_end]
    X_test, y_test = X[cal_end:], y[cal_end:]
    if len(y_cal) < 75 or len(y_test) < 75:
        raise MulticlassCandidateError("MULTICLASS_PARTITION_INSUFFICIENT", "calibration/test each require >=75")
    for label, partition in (("train", y_train), ("calibration", y_cal), ("test", y_test)):
        if set(str(v) for v in partition) != set(classes_expected):
            raise MulticlassCandidateError("MULTICLASS_CLASS_DEGENERATE", label)

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, random_state=0)
    model.fit(scaler.transform(X_train), y_train)
    classes = np.asarray(model.classes_, dtype=object)
    p_cal_raw = model.predict_proba(scaler.transform(X_cal))
    p_test_raw = model.predict_proba(scaler.transform(X_test))
    temperature, _ = _fit_temperature(p_cal_raw, y_cal, classes)
    p_test_cal = _temperature_scale(p_test_raw, temperature)

    train_counts = {str(label): int(np.sum(y_train == label)) for label in classes}
    prior = np.asarray([train_counts[str(label)] / len(y_train) for label in classes], dtype=float)
    baseline = np.tile(prior, (len(y_test), 1))
    raw_ll = float(log_loss(y_test, p_test_raw, labels=list(classes)))
    cal_ll = float(log_loss(y_test, p_test_cal, labels=list(classes)))
    base_ll = float(log_loss(y_test, baseline, labels=list(classes)))
    raw_brier = _multiclass_brier(y_test, p_test_raw, classes)
    cal_brier = _multiclass_brier(y_test, p_test_cal, classes)
    base_brier = _multiclass_brier(y_test, baseline, classes)
    raw_ece, cal_ece = _ece(y_test, p_test_raw, classes), _ece(y_test, p_test_cal, classes)
    metrics = MulticlassMetrics(
        train_n=len(y_train), calibration_n=len(y_cal), test_n=len(y_test),
        raw_log_loss=raw_ll, calibrated_log_loss=cal_ll, baseline_log_loss=base_ll,
        raw_brier=raw_brier, calibrated_brier=cal_brier, baseline_brier=base_brier,
        raw_ece=raw_ece, calibrated_ece=cal_ece,
    )
    research_pass = (
        raw_ll < base_ll and raw_brier < base_brier
        and cal_ll <= raw_ll + 1e-10 and cal_brier <= raw_brier + 0.01
    )
    artifact = {
        "model_family": str(model_family), "artifact_format": "STANDARDIZED_MULTINOMIAL_LOGISTIC_JSON_V1",
        "feature_names": list(names), "classes": [str(v) for v in classes],
        "scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist(),
        "intercepts": model.intercept_.tolist(), "coefficients": model.coef_.tolist(),
        "split_policy": "CHRONOLOGICAL_60_20_20",
    }
    calibrator = {
        "method": CALIBRATION_METHOD, "temperature": temperature,
        "calibration_n": len(y_cal), "test_reliability": _class_reliability(y_test, p_test_cal, classes),
    }
    return MulticlassCandidate(
        model_family=str(model_family), feature_names=names, classes=tuple(str(v) for v in classes),
        artifact_payload=artifact, calibrator_payload=calibrator, metrics=metrics,
        dataset_hash=_dataset_hash(rows, names, str(model_family)), research_screen_pass=research_pass,
    )


__all__ = [
    "CALIBRATION_METHOD", "CAN_EXECUTE", "MulticlassCandidate", "MulticlassCandidateError",
    "MulticlassMetrics", "MulticlassTrainingRow", "PROBABILITY_PUBLISHABLE", "train_multiclass_candidate",
]
