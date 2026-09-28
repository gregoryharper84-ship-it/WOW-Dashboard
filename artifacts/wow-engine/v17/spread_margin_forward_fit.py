"""Artifact-only fitter for latency-bounded spread forward shadow scoring.

This module intentionally preserves the exact fitted artifact semantics from the
full historical replay trainer while omitting replay-only test-grid evaluation.
It is serving infrastructure, not a new sporting model: chronological split,
scaling, Ridge coefficients, calibration residuals, dataset hash, model family,
and governance flags remain identical to the governed challenger.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from v17.spread_margin_challenger import (
    SPORT_CONFIG,
    MarginDistributionArtifact,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    _chronological_split,
    _dataset_hash,
    _dt,
    _matrix,
)


def fit_margin_distribution_artifact(
    rows: Sequence[MarginTrainingRow],
    *,
    sport: str,
    min_rows: int = 300,
    ridge_alpha: float = 4.0,
) -> MarginDistributionArtifact:
    """Fit the exact governed challenger artifact without replay diagnostics."""
    sport = str(sport).upper()
    if sport not in SPORT_CONFIG:
        raise SpreadChallengerUnavailable("SPREAD_SPORT_UNSUPPORTED", f"unsupported spread sport: {sport}")
    if ridge_alpha <= 0:
        raise SpreadChallengerUnavailable("SPREAD_RIDGE_ALPHA_INVALID", "ridge alpha must be positive")
    if not rows:
        raise SpreadChallengerUnavailable("SPREAD_MARGIN_ROWS_EMPTY", "no spread rows supplied")

    names = tuple(sorted(rows[0].features))
    if not names:
        raise SpreadChallengerUnavailable("SPREAD_FEATURES_EMPTY", "spread rows have no features")
    for row in rows:
        if tuple(sorted(row.features)) != names:
            raise SpreadChallengerUnavailable("SPREAD_FEATURE_SCHEMA_MISMATCH", "spread feature keys differ across rows")
        if _dt(row.feature_as_of) >= _dt(row.event_start_time):
            raise SpreadChallengerUnavailable("SPREAD_FEATURE_LEAKAGE", f"feature_as_of must precede event start: {row.event_id}")

    train, calibration, test = _chronological_split(rows, min_rows=min_rows)
    x_train, y_train = _matrix(train, names)
    x_cal, y_cal = _matrix(calibration, names)
    scaler = StandardScaler().fit(x_train)
    model = Ridge(alpha=float(ridge_alpha)).fit(scaler.transform(x_train), y_train)
    calibration_pred = model.predict(scaler.transform(x_cal))
    residuals = tuple(float(actual - pred) for actual, pred in zip(y_cal, calibration_pred))
    if len(residuals) < 2 or float(np.std(np.asarray(residuals, dtype=float))) <= 1e-9:
        raise SpreadChallengerUnavailable(
            "SPREAD_RESIDUAL_DISTRIBUTION_DEGENERATE",
            "calibration residual distribution is degenerate",
        )

    return MarginDistributionArtifact(
        sport=sport,
        model_family=f"{sport}_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version=f"{sport}_SPREAD_MARGIN_TEAM_STATE_V1",
        feature_names=names,
        scaler_mean=tuple(float(v) for v in scaler.mean_),
        scaler_scale=tuple(float(v if abs(v) > 1e-12 else 1.0) for v in scaler.scale_),
        coefficients=tuple(float(v) for v in model.coef_),
        intercept=float(model.intercept_),
        calibration_residuals=residuals,
        train_rows=len(train),
        calibration_rows=len(calibration),
        test_rows=len(test),
        training_dataset_hash=_dataset_hash(rows, names),
        ridge_alpha=float(ridge_alpha),
    )


__all__ = ["fit_margin_distribution_artifact"]
