"""Regression: NFL scorer hashes the vector it actually numerically consumes.

Synthetic fitted-test artifact only. This cannot certify a live NFL specialist.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from nfl_event_model_v17 import (
    CertifiedNFLModel,
    NFLModelInputsInsufficient,
    score_feature_row,
)


def _model():
    return CertifiedNFLModel(
        artifact_id="synthetic-nfl-artifact",
        model_artifact_version="synthetic-fitted-v1",
        model_family="NFL_OUTRIGHT_WIN_LOGREG_V1",
        feature_schema_version="NFL_SYNTHETIC_TWO_FEATURES_V1",
        feature_order=("home_recent", "away_recent"),
        feature_order_hash="synthetic-order",
        scaler_mean=np.zeros(2, dtype=float),
        scaler_scale=np.ones(2, dtype=float),
        coefficients=np.array([0.1, -0.2]),
        intercept=0.0,
        platt_a=1.0,
        platt_b=0.0,
        calibration_version="synthetic-platt-v1",
        calibration_training_n=500,
        uncertainty_margin=0.05,
        bounds_method_version="synthetic-test-only",
        artifact_checksum="synthetic-artifact-sha",
        training_dataset_hash="synthetic-training-sha",
        validation_metrics={},
    )


def _row(vector=None):
    return {
        "feature_schema_version": "NFL_SYNTHETIC_TWO_FEATURES_V1",
        "feature_order": ["home_recent", "away_recent"],
        "feature_vector": [0.2, 0.7] if vector is None else vector,
        "row_inputs_hash": "synthetic-input-row-hash",
        "training_eligible": True,
    }


def test_exact_frozen_and_consumed_numeric_vector_sha256():
    result = score_feature_row(_model(), _row())
    assert result["feature_snapshot_hash"] == result["model_consumed_feature_hash"]
    assert len(result["feature_snapshot_hash"]) == 64
    assert result["feature_vector_hash_policy"] == "NFL_CANONICAL_NUMERIC_VECTOR_SCHEMA_ORDER_V1"
    assert result["can_execute"] is False
    # The fitted logit/calibration remain exactly the original logistic math.
    logit = (0.1 * 0.2) + (-0.2 * 0.7)
    expected = 1.0 / (1.0 + math.exp(-logit))
    assert result["calibrated_home_probability"] == pytest.approx(expected)
    assert result["calibrated_home_lower_bound"] == pytest.approx(expected - 0.05)


def test_vector_digest_changes_when_a_consumed_number_changes():
    first = score_feature_row(_model(), _row())
    second = score_feature_row(_model(), _row([0.21, 0.7]))
    assert first["model_consumed_feature_hash"] != second["model_consumed_feature_hash"]
    assert first["model_consumed_feature_hash"] == first["feature_snapshot_hash"]
    assert second["model_consumed_feature_hash"] == second["feature_snapshot_hash"]


def test_nonfinite_numeric_vector_cannot_get_a_valid_digest():
    with pytest.raises(NFLModelInputsInsufficient, match="NFL_FEATURE_VECTOR_NONFINITE"):
        score_feature_row(_model(), _row([float("nan"), 0.7]))


def test_schema_order_mismatch_still_fails_closed_before_inference():
    row = _row()
    row["feature_order"] = ["away_recent", "home_recent"]
    with pytest.raises(NFLModelInputsInsufficient, match="NFL_FEATURE_ORDER_INPUT_MISMATCH"):
        score_feature_row(_model(), row)
