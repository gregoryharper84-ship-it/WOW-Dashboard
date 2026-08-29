"""Deterministic candidate trainer for WOW_NCAAF_FITTED_MODEL_V1.

This is an offline research component. It never writes a promoted artifact and
never makes a probability publishable. It requires chronologically ordered,
settled pregame feature rows and compares a simple regularized logistic model
against a train-prevalence baseline on a held-out future block.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
MODEL_FAMILY = "NCAAF_LOGISTIC_V1"

FEATURES = (
    "power_delta",
    "off_epa_delta",
    "def_epa_delta",
    "success_rate_delta",
    "explosiveness_delta",
    "qb_value_delta",
    "qb_certainty_delta",
    "ol_health_delta",
    "def_front_health_delta",
    "skill_availability_delta",
    "rest_days_delta",
    "tempo_delta",
    "turnover_volatility_delta",
    "special_teams_delta",
    "travel_distance_miles",
    "weather_wind_mph",
    "weather_precip_probability",
    "neutral_site",
)


class NCAAFTrainingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TrainingRow:
    event_start_time: str
    feature_as_of: str
    home_won: bool
    features: Mapping[str, float]


@dataclass(frozen=True)
class CandidateMetrics:
    train_n: int
    validation_n: int
    brier: float
    log_loss: float
    baseline_brier: float
    baseline_log_loss: float
    brier_improvement: float
    log_loss_improvement: float


@dataclass(frozen=True)
class CandidateArtifact:
    model_family: str
    artifact_format: str
    artifact_payload: Mapping[str, object]
    training_rows: int
    validation_metrics: CandidateMetrics
    dataset_hash: str
    probability_publishable: bool = False
    can_execute: bool = False


def _as_matrix(rows: Sequence[TrainingRow]) -> tuple[np.ndarray, np.ndarray]:
    matrix: list[list[float]] = []
    labels: list[int] = []
    for row in rows:
        if row.feature_as_of >= row.event_start_time:
            raise NCAAFTrainingError(
                "NCAAF_TRAINING_LEAKAGE_DETECTED",
                "feature_as_of must be strictly before event_start_time for every training row.",
            )
        values: list[float] = []
        for name in FEATURES:
            value = row.features.get(name)
            if value is None:
                raise NCAAFTrainingError(
                    "NCAAF_TRAINING_FEATURE_MISSING",
                    f"Required feature {name!r} is missing.",
                )
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise NCAAFTrainingError(
                    "NCAAF_TRAINING_FEATURE_INVALID",
                    f"Required feature {name!r} is not numeric.",
                ) from exc
            if not np.isfinite(number):
                raise NCAAFTrainingError(
                    "NCAAF_TRAINING_FEATURE_INVALID",
                    f"Required feature {name!r} is not finite.",
                )
            values.append(number)
        matrix.append(values)
        labels.append(1 if row.home_won else 0)
    return np.asarray(matrix, dtype=float), np.asarray(labels, dtype=int)


def _dataset_hash(rows: Sequence[TrainingRow]) -> str:
    serializable = [
        {
            "event_start_time": row.event_start_time,
            "feature_as_of": row.feature_as_of,
            "home_won": row.home_won,
            "features": {key: float(row.features[key]) for key in FEATURES},
        }
        for row in rows
    ]
    payload = json.dumps(serializable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(payload).hexdigest()


def train_candidate(
    rows: Sequence[TrainingRow],
    *,
    validation_fraction: float = 0.20,
    min_rows: int = 200,
    min_validation_rows: int = 40,
) -> CandidateArtifact:
    if len(rows) < min_rows:
        raise NCAAFTrainingError(
            "NCAAF_TRAINING_SAMPLE_INSUFFICIENT",
            f"At least {min_rows} settled pregame rows are required; got {len(rows)}.",
        )
    if not (0.10 <= validation_fraction <= 0.40):
        raise ValueError("validation_fraction must be between 0.10 and 0.40")

    ordered = sorted(rows, key=lambda row: row.event_start_time)
    if list(rows) != ordered:
        raise NCAAFTrainingError(
            "NCAAF_TRAINING_ROWS_NOT_CHRONOLOGICAL",
            "Training rows must be provided in ascending event_start_time order.",
        )
    X, y = _as_matrix(ordered)

    split = int(round(len(rows) * (1.0 - validation_fraction)))
    split = max(1, min(split, len(rows) - 1))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    if len(y_val) < min_validation_rows:
        raise NCAAFTrainingError(
            "NCAAF_VALIDATION_SAMPLE_INSUFFICIENT",
            f"At least {min_validation_rows} future validation rows are required; got {len(y_val)}.",
        )
    if len(np.unique(y_train)) < 2 or len(np.unique(y_val)) < 2:
        raise NCAAFTrainingError(
            "NCAAF_TRAINING_CLASS_DEGENERATE",
            "Both train and validation partitions must contain wins and losses.",
        )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    model = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=500, random_state=0)
    model.fit(X_train_scaled, y_train)
    p_val = model.predict_proba(X_val_scaled)[:, 1]

    prevalence = float(np.mean(y_train))
    baseline = np.full_like(p_val, prevalence, dtype=float)
    metrics = CandidateMetrics(
        train_n=len(y_train),
        validation_n=len(y_val),
        brier=float(brier_score_loss(y_val, p_val)),
        log_loss=float(log_loss(y_val, p_val, labels=[0, 1])),
        baseline_brier=float(brier_score_loss(y_val, baseline)),
        baseline_log_loss=float(log_loss(y_val, baseline, labels=[0, 1])),
        brier_improvement=float(brier_score_loss(y_val, baseline) - brier_score_loss(y_val, p_val)),
        log_loss_improvement=float(log_loss(y_val, baseline, labels=[0, 1]) - log_loss(y_val, p_val, labels=[0, 1])),
    )

    payload = {
        "feature_names": list(FEATURES),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "intercept": float(model.intercept_[0]),
        "coefficients": model.coef_[0].tolist(),
        "validation_split_index": split,
        "validation_first_event_start_time": ordered[split].event_start_time,
    }
    return CandidateArtifact(
        model_family=MODEL_FAMILY,
        artifact_format="STANDARDIZED_LOGISTIC_JSON_V1",
        artifact_payload=payload,
        training_rows=len(rows),
        validation_metrics=metrics,
        dataset_hash=_dataset_hash(ordered),
        probability_publishable=False,
        can_execute=False,
    )


def candidate_beats_baseline(artifact: CandidateArtifact) -> bool:
    """Research screen only; never equivalent to calibration/trust promotion."""
    m = artifact.validation_metrics
    return m.brier_improvement > 0.0 and m.log_loss_improvement >= 0.0
