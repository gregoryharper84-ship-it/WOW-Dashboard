from __future__ import annotations

import math

import numpy as np

from v17.nfl_spread_scale_calibrated_challenger import (
    AUTOMATIC_CERTIFICATION,
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    CALIBRATION_METHOD,
    DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
    FEATURE_SCHEMA_VERSION,
    GLOBAL_TERMINAL_REDUCER,
    MODEL_FAMILY,
    PROBABILITY_PUBLISHABLE,
    RANK_ELIGIBLE,
    SCALE_GRID,
    _empirical_crps,
    _scaled_artifact,
)
from v17.spread_certification_replay_route import SpreadCertificationReplayRequest, execute_spread_certification_replay
from v17.spread_margin_challenger import MarginDistributionArtifact


def _artifact() -> MarginDistributionArtifact:
    return MarginDistributionArtifact(
        sport="NFL",
        model_family="NFL_SPREAD_CONTEXT_RIDGE_EMPIRICAL_V2",
        feature_schema_version="NFL_SPREAD_CONTEXT_FEATURES_V2",
        feature_names=("x",),
        scaler_mean=(0.0,),
        scaler_scale=(1.0,),
        coefficients=(1.0,),
        intercept=0.0,
        calibration_residuals=(-10.0, 0.0, 10.0),
        train_rows=790,
        calibration_rows=285,
        test_rows=285,
        training_dataset_hash="base-hash",
        ridge_alpha=4.0,
    )


def test_v3_is_research_only_and_preserves_market_independence_contract():
    assert MODEL_FAMILY == "NFL_SPREAD_CONTEXT_RIDGE_SCALE_CALIBRATED_V3"
    assert FEATURE_SCHEMA_VERSION == "NFL_SPREAD_CONTEXT_FEATURES_V2"
    assert CALIBRATION_METHOD == "SPORTING_MARGIN_CRPS_RESIDUAL_SCALE_2024_CHRONO_SPLIT"
    assert 1.0 in SCALE_GRID
    assert CAN_EXECUTE is False
    assert DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS is True
    assert AUTOMATIC_CERTIFICATION is False
    assert AUTOMATIC_PROMOTION is False
    assert PROBABILITY_PUBLISHABLE is False
    assert RANK_ELIGIBLE is False
    assert GLOBAL_TERMINAL_REDUCER == "V17_TERMINAL_REDUCER"


def test_residual_scale_changes_dispersion_not_mean_and_changes_artifact_identity():
    base = _artifact()
    scaled = _scaled_artifact(base, 2.0)
    assert scaled.model_family == MODEL_FAMILY
    assert scaled.feature_schema_version == base.feature_schema_version
    assert math.isclose(sum(scaled.calibration_residuals) / 3.0, 0.0, abs_tol=1e-12)
    assert scaled.calibration_residuals == (-20.0, 0.0, 20.0)
    assert scaled.training_dataset_hash != base.training_dataset_hash
    assert scaled.coefficients == base.coefficients
    assert scaled.intercept == base.intercept


def test_empirical_crps_rewards_distribution_closer_to_observed_margin():
    actual = 10.0
    close = np.asarray([8.0, 10.0, 12.0])
    wide = np.asarray([-10.0, 10.0, 30.0])
    assert _empirical_crps(close, actual) < _empirical_crps(wide, actual)


def test_v3_variant_fails_closed_outside_nfl():
    result = execute_spread_certification_replay(
        object(),
        SpreadCertificationReplayRequest(sport="WNBA", variant="NFL_CONTEXT_V3_SCALE_CAL"),
    )
    assert result["status"] == "BLOCKED"
    assert result["code"] == "SPREAD_CERTIFICATION_VARIANT_SPORT_MISMATCH"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
    assert result["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"
