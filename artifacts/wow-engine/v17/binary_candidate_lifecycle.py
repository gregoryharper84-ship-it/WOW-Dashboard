"""Reusable D1 lifecycle for binary head-to-head candidate models.

Sport-specific lanes own feature semantics, source provenance, identity, regime,
and artifacts. This module owns only the deterministic statistical lifecycle:
chronological train -> calibration -> untouched test, with no automatic
certification or probability publication.

Calibration V2 is deliberately challenger-only.  A calibrator is selected using
only the chronological calibration block; the untouched test block never
participates in calibrator choice.  Identity calibration is a valid candidate
when empirical binning degrades forward calibration evidence.  This prevents a
calibrator from being forced merely to satisfy a pipeline shape while preserving
the existing research screen on the untouched test set.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite, sqrt
from typing import Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
CALIBRATION_METHOD = "FORWARD_SELECTED_IDENTITY_OR_EMPIRICAL_WILSON_V2"
EMPIRICAL_CALIBRATION_METHOD = "EMPIRICAL_WILSON_BINS_V1"
IDENTITY_CALIBRATION_METHOD = "IDENTITY_RAW_PROBABILITY_V1"
CALIBRATOR_SELECTION_VERSION = "FORWARD_CALIBRATION_SELECTION_V2"


class BinaryCandidateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BinaryTrainingRow:
    event_id: str
    event_start_time: str
    feature_as_of: str
    positive_outcome: bool
    features: Mapping[str, float]
    source_manifest_sha256: str


@dataclass(frozen=True)
class BinaryCandidateMetrics:
    train_n: int
    calibration_n: int
    test_n: int
    raw_brier: float
    calibrated_brier: float
    baseline_brier: float
    raw_log_loss: float
    calibrated_log_loss: float
    baseline_log_loss: float
    ece: float


@dataclass(frozen=True)
class BinaryCandidate:
    model_family: str
    feature_names: tuple[str, ...]
    artifact_payload: Mapping[str, object]
    calibrator_payload: Mapping[str, object]
    metrics: BinaryCandidateMetrics
    dataset_hash: str
    calibration_start_event: str
    calibration_end_event: str
    test_start_event: str
    test_end_event: str
    research_screen_pass: bool
    automatic_certification: bool = False
    automatic_promotion: bool = False
    probability_publishable: bool = False
    can_execute: bool = False


def _time_key(value: str) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise BinaryCandidateError("BINARY_EVENT_TIME_INVALID", "event timestamps must be ISO-8601 strings")
    return value


def _matrix(rows: Sequence[BinaryTrainingRow], feature_names: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
    xs: list[list[float]] = []
    ys: list[int] = []
    seen_events: set[str] = set()
    for row in rows:
        event_id = str(row.event_id or "").strip()
        if not event_id or event_id in seen_events:
            raise BinaryCandidateError("BINARY_EVENT_ID_INVALID", "event IDs must be non-empty and unique")
        seen_events.add(event_id)
        if _time_key(row.feature_as_of) >= _time_key(row.event_start_time):
            raise BinaryCandidateError("BINARY_TEMPORAL_LEAKAGE", event_id)
        manifest_hash = str(row.source_manifest_sha256 or "").strip().lower()
        if len(manifest_hash) != 64 or any(ch not in "0123456789abcdef" for ch in manifest_hash):
            raise BinaryCandidateError("BINARY_SOURCE_MANIFEST_HASH_INVALID", event_id)
        values: list[float] = []
        for name in feature_names:
            value = row.features.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BinaryCandidateError("BINARY_FEATURE_INVALID", f"{event_id}:{name}")
            number = float(value)
            if not isfinite(number):
                raise BinaryCandidateError("BINARY_FEATURE_INVALID", f"{event_id}:{name}")
            values.append(number)
        xs.append(values)
        ys.append(1 if row.positive_outcome else 0)
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=int)


def _wilson(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("n must be positive")
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _fit_calibrator(probabilities: np.ndarray, outcomes: np.ndarray, *, max_bins: int = 10) -> dict[str, object]:
    if len(probabilities) < 50:
        raise BinaryCandidateError("BINARY_CALIBRATION_SAMPLE_INSUFFICIENT", str(len(probabilities)))
    order = np.argsort(probabilities, kind="stable")
    bins: list[dict[str, float | int]] = []
    for chunk in np.array_split(order, min(max_bins, len(order))):
        if len(chunk) == 0:
            continue
        ps = probabilities[chunk]
        ys = outcomes[chunk]
        wins = int(np.sum(ys))
        n = int(len(chunk))
        lower, upper = _wilson(wins, n)
        bins.append({
            "raw_min": float(np.min(ps)),
            "raw_max": float(np.max(ps)),
            "raw_mean": float(np.mean(ps)),
            "n": n,
            "wins": wins,
            "observed_rate": wins / n,
            "calibrated_probability": (wins + 1.0) / (n + 2.0),
            "wilson_lower": lower,
            "wilson_upper": upper,
        })
    return {
        "method": EMPIRICAL_CALIBRATION_METHOD,
        "training_n": int(len(probabilities)),
        "binning": "CHRONOLOGICAL_CALIBRATION_BLOCK_EQUAL_COUNT",
        "bins": bins,
    }


def _identity_calibrator(training_n: int, *, selection: Mapping[str, object]) -> dict[str, object]:
    return {
        "method": IDENTITY_CALIBRATION_METHOD,
        "training_n": int(training_n),
        "selection_version": CALIBRATOR_SELECTION_VERSION,
        "selection": dict(selection),
        "bins": [],
    }


def _map_calibrator(probabilities: np.ndarray, calibrator: Mapping[str, object]) -> np.ndarray:
    method = str(calibrator.get("method") or "")
    if method == IDENTITY_CALIBRATION_METHOD:
        return np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1.0 - 1e-6)
    bins = list(calibrator.get("bins") or [])
    if not bins:
        raise BinaryCandidateError("BINARY_CALIBRATOR_EMPTY", "no bins")
    centers = np.asarray([float(row["raw_mean"]) for row in bins], dtype=float)
    mapped = []
    for value in probabilities:
        idx = int(np.argmin(np.abs(centers - float(value))))
        mapped.append(float(bins[idx]["calibrated_probability"]))
    return np.clip(np.asarray(mapped, dtype=float), 1e-6, 1.0 - 1e-6)


def _calibration_metrics(probabilities: np.ndarray, outcomes: np.ndarray) -> tuple[float, float]:
    clipped = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1.0 - 1e-6)
    return (
        float(brier_score_loss(outcomes, clipped)),
        float(log_loss(outcomes, clipped, labels=[0, 1])),
    )


def _select_calibrator(probabilities: np.ndarray, outcomes: np.ndarray) -> dict[str, object]:
    """Select identity vs empirical bins without touching the held-out test block.

    The calibration block is itself split chronologically.  Empirical bins are
    fit on the earlier sub-block and compared with the raw identity mapping on
    the later selection sub-block.  Only when empirical calibration is no worse
    on both proper scores is it refit on the complete calibration block.
    """
    n = int(len(probabilities))
    if n < 50:
        raise BinaryCandidateError("BINARY_CALIBRATION_SAMPLE_INSUFFICIENT", str(n))

    selection_fit_n = max(50, int(n * 0.60))
    selection_eval_n = n - selection_fit_n
    if selection_eval_n < 20:
        selection_fit_n = n - 20
        selection_eval_n = 20
    if selection_fit_n < 50:
        selection = {
            "decision": "IDENTITY",
            "reason": "FORWARD_SELECTION_SAMPLE_INSUFFICIENT",
            "fit_n": max(0, selection_fit_n),
            "evaluation_n": max(0, selection_eval_n),
        }
        return _identity_calibrator(n, selection=selection)

    fit_p = probabilities[:selection_fit_n]
    fit_y = outcomes[:selection_fit_n]
    eval_p = probabilities[selection_fit_n:]
    eval_y = outcomes[selection_fit_n:]
    empirical_selection = _fit_calibrator(fit_p, fit_y)
    empirical_eval = _map_calibrator(eval_p, empirical_selection)
    identity_brier, identity_log_loss = _calibration_metrics(eval_p, eval_y)
    empirical_brier, empirical_log_loss = _calibration_metrics(empirical_eval, eval_y)
    empirical_selected = (
        empirical_brier <= identity_brier + 1e-12
        and empirical_log_loss <= identity_log_loss + 1e-12
    )
    selection = {
        "decision": "EMPIRICAL_WILSON" if empirical_selected else "IDENTITY",
        "reason": "FORWARD_PROPER_SCORE_COMPARISON",
        "fit_n": selection_fit_n,
        "evaluation_n": selection_eval_n,
        "identity_brier": identity_brier,
        "identity_log_loss": identity_log_loss,
        "empirical_brier": empirical_brier,
        "empirical_log_loss": empirical_log_loss,
        "test_block_used_for_selection": False,
    }
    if not empirical_selected:
        return _identity_calibrator(n, selection=selection)
    selected = _fit_calibrator(probabilities, outcomes)
    selected["selection_version"] = CALIBRATOR_SELECTION_VERSION
    selected["selection"] = selection
    return selected


def _ece(probabilities: np.ndarray, outcomes: np.ndarray, *, bins: int = 10) -> float:
    if len(probabilities) == 0:
        return 1.0
    error = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for i in range(bins):
        lower, upper = edges[i], edges[i + 1]
        mask = (probabilities >= lower) & (probabilities < upper if i < bins - 1 else probabilities <= upper)
        n = int(np.sum(mask))
        if n:
            error += (n / len(probabilities)) * abs(float(np.mean(probabilities[mask])) - float(np.mean(outcomes[mask])))
    return float(error)


def _dataset_hash(rows: Sequence[BinaryTrainingRow], feature_names: tuple[str, ...], model_family: str) -> str:
    payload = {
        "model_family": model_family,
        "feature_names": list(feature_names),
        "rows": [
            {
                "event_id": row.event_id,
                "event_start_time": row.event_start_time,
                "feature_as_of": row.feature_as_of,
                "positive_outcome": row.positive_outcome,
                "features": {name: float(row.features[name]) for name in feature_names},
                "source_manifest_sha256": row.source_manifest_sha256,
            }
            for row in rows
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def train_binary_candidate(
    rows: Sequence[BinaryTrainingRow],
    *,
    model_family: str,
    feature_names: Sequence[str],
    min_rows: int = 300,
    train_fraction: float = 0.60,
    calibration_fraction: float = 0.20,
) -> BinaryCandidate:
    names = tuple(str(name).strip() for name in feature_names)
    if not names or any(not name for name in names) or len(set(names)) != len(names):
        raise BinaryCandidateError("BINARY_FEATURE_SCHEMA_INVALID", "feature names must be unique and non-empty")
    family = str(model_family or "").strip()
    if not family:
        raise BinaryCandidateError("BINARY_MODEL_FAMILY_INVALID", "model_family required")
    if len(rows) < min_rows:
        raise BinaryCandidateError("BINARY_SAMPLE_INSUFFICIENT", f"rows={len(rows)};minimum={min_rows}")
    if not (0.50 <= train_fraction <= 0.75 and 0.15 <= calibration_fraction <= 0.25):
        raise ValueError("unsupported chronological split")
    if train_fraction + calibration_fraction >= 0.90:
        raise ValueError("at least 10% must remain untouched")

    ordered = sorted(rows, key=lambda row: row.event_start_time)
    if list(rows) != ordered:
        raise BinaryCandidateError("BINARY_ROWS_NOT_CHRONOLOGICAL", "rows must be ascending")
    X, y = _matrix(rows, names)
    train_end = int(len(rows) * train_fraction)
    cal_end = int(len(rows) * (train_fraction + calibration_fraction))
    X_train, y_train = X[:train_end], y[:train_end]
    X_cal, y_cal = X[train_end:cal_end], y[train_end:cal_end]
    X_test, y_test = X[cal_end:], y[cal_end:]
    if len(y_cal) < 50 or len(y_test) < 50:
        raise BinaryCandidateError("BINARY_PARTITION_INSUFFICIENT", "calibration and untouched test each require >=50")
    for label, partition in (("train", y_train), ("calibration", y_cal), ("test", y_test)):
        if len(np.unique(partition)) < 2:
            raise BinaryCandidateError("BINARY_CLASS_DEGENERATE", label)

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500, random_state=0)
    model.fit(scaler.transform(X_train), y_train)
    p_cal_raw = model.predict_proba(scaler.transform(X_cal))[:, 1]
    p_test_raw = model.predict_proba(scaler.transform(X_test))[:, 1]
    calibrator = _select_calibrator(p_cal_raw, y_cal)
    p_test_cal = _map_calibrator(p_test_raw, calibrator)
    prevalence = float(np.mean(y_train))
    baseline = np.full(len(y_test), prevalence, dtype=float)

    raw_brier = float(brier_score_loss(y_test, p_test_raw))
    cal_brier = float(brier_score_loss(y_test, p_test_cal))
    baseline_brier = float(brier_score_loss(y_test, baseline))
    raw_ll = float(log_loss(y_test, p_test_raw, labels=[0, 1]))
    cal_ll = float(log_loss(y_test, p_test_cal, labels=[0, 1]))
    baseline_ll = float(log_loss(y_test, baseline, labels=[0, 1]))
    metrics = BinaryCandidateMetrics(
        train_n=len(y_train),
        calibration_n=len(y_cal),
        test_n=len(y_test),
        raw_brier=raw_brier,
        calibrated_brier=cal_brier,
        baseline_brier=baseline_brier,
        raw_log_loss=raw_ll,
        calibrated_log_loss=cal_ll,
        baseline_log_loss=baseline_ll,
        ece=_ece(p_test_cal, y_test),
    )
    research_pass = (
        raw_brier < baseline_brier
        and raw_ll <= baseline_ll
        and cal_brier <= raw_brier + 1e-12
        and cal_ll <= raw_ll + 1e-12
    )
    artifact = {
        "model_family": family,
        "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1",
        "feature_names": list(names),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "intercept": float(model.intercept_[0]),
        "coefficients": model.coef_[0].tolist(),
        "train_end_index": train_end,
        "calibration_end_index": cal_end,
        "split_policy": "CHRONOLOGICAL_60_20_20",
        "calibrator_selection_version": CALIBRATOR_SELECTION_VERSION,
    }
    return BinaryCandidate(
        model_family=family,
        feature_names=names,
        artifact_payload=artifact,
        calibrator_payload=calibrator,
        metrics=metrics,
        dataset_hash=_dataset_hash(rows, names, family),
        calibration_start_event=rows[train_end].event_start_time,
        calibration_end_event=rows[cal_end - 1].event_start_time,
        test_start_event=rows[cal_end].event_start_time,
        test_end_event=rows[-1].event_start_time,
        research_screen_pass=research_pass,
    )


__all__ = [
    "BinaryCandidate",
    "BinaryCandidateError",
    "BinaryCandidateMetrics",
    "BinaryTrainingRow",
    "CALIBRATION_METHOD",
    "CALIBRATOR_SELECTION_VERSION",
    "EMPIRICAL_CALIBRATION_METHOD",
    "IDENTITY_CALIBRATION_METHOD",
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "train_binary_candidate",
]
