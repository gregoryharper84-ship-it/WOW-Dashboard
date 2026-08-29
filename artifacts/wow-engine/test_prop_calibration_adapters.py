from __future__ import annotations

import pytest

from prop_calibration_adapters import mlb_pitcher_so_precalibration_shrinkage_adapter
from prop_discrete_engine import PropCalibrationUnavailable
from prop_distribution_contract import CertifiedBundle, LineProbabilities
from prop_fitted_provider import CertifiedInference, ResolvedArtifact
from prop_model_adapters import MLB_PITCHER_SO_MODEL_FAMILY


def _artifact_payload():
    return {
        "fitted_constants": {
            "league_so_per_out": 0.31,
            "league_k_per_pa": 0.224,
            "league_shortened_rate": 0.27,
            "outs_normal_scale": 17.8,
            "outs_short_scale": 10.7,
            "dispersion_r": 54.6,
        },
        "shrinkage_k_rate": 8.0,
        "shrinkage_k_regime": 8.0,
        "shortened_outs_threshold": 15,
        "max_support_k": 20,
        "opponent_factor_clip": [0.75, 1.30],
        "feature_transform_version": "MLB_PITCHER_SO_TRANSFORM_V1",
    }


def _inference():
    bundle = CertifiedBundle(
        model_artifact_version="MLB_PITCHER_SO_FAILURE_PATH_NB_V1_TEST",
        calibrator_version="MLB_PITCHER_SO_CAL_V1",
        feature_transform_version="MLB_PITCHER_SO_TRANSFORM_V1",
        specialist_version="wow.mlb-pitcher-failure-path-expert@1",
        certification_id="CERT-TEST-1",
        feature_schema_version="PROP_FEATURES_V1",
        training_dataset_hash="a" * 64,
        training_code_sha="b" * 64,
        artifact_checksum="c" * 64,
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport="MLB",
        supported_stat_type="PITCHER_STRIKEOUTS",
        supported_line_min=0.5,
        supported_line_max=12.5,
    )
    artifact = ResolvedArtifact(
        artifact_id="11111111-1111-4111-8111-111111111111",
        model_family=MLB_PITCHER_SO_MODEL_FAMILY,
        artifact_format="JSON_V1",
        artifact_payload=_artifact_payload(),
        training_rows=4489,
        validation_metrics={},
        bundle=bundle,
    )
    from prop_model_adapters import mlb_pitcher_so_failure_path_nb_v1_adapter
    from prop_distribution_contract import PropInferenceRequest, derive_line_probabilities

    request = PropInferenceRequest(
        event_id="MLB:TEST:1",
        player_id="wow-name:test-pitcher",
        sport="MLB",
        league_season="2026",
        stat_type="PITCHER_STRIKEOUTS",
        evidence_snapshot_id="22222222-2222-4222-8222-222222222222",
        market_identity_id="wow-market:test",
        as_of_timestamp="2026-08-29T12:00:00+00:00",
        request_id="req-1",
        feature_schema_version="PROP_FEATURES_V1",
    )
    features = _features()
    distribution = mlb_pitcher_so_failure_path_nb_v1_adapter(artifact, request, features)
    line_probs = derive_line_probabilities(distribution, 5.5)
    return CertifiedInference(artifact=artifact, distribution=distribution), line_probs


def _features():
    game_log = [5, 6, 4, 7, 5, 6, 8, 3, 5, 6]
    box_score_log = [{"outs": o} for o in [17, 18, 15, 19, 16, 18, 20, 12, 17, 18]]
    return {"game_log": game_log, "box_score_log": box_score_log, "opponent_context": None}


def test_precalibration_shrinkage_produces_valid_bounds_for_more():
    inference, line_probs = _inference()
    output = mlb_pitcher_so_precalibration_shrinkage_adapter(
        inference, line_probs.probability_more, line_probs, _features(), seed=7
    )
    output.validate()
    assert output.calibration_status == "PRECALIBRATION_SHRINKAGE"
    assert 0.0 < output.lower_bound <= output.calibrated_probability <= output.upper_bound < 1.0
    assert output.effective_sample_size == 10.0


def test_precalibration_shrinkage_produces_valid_bounds_for_less():
    inference, line_probs = _inference()
    output = mlb_pitcher_so_precalibration_shrinkage_adapter(
        inference, line_probs.probability_less, line_probs, _features(), seed=7
    )
    output.validate()
    assert 0.0 < output.lower_bound <= output.calibrated_probability <= output.upper_bound < 1.0


def test_missing_evidence_fails_closed():
    inference, line_probs = _inference()
    with pytest.raises(PropCalibrationUnavailable) as exc:
        mlb_pitcher_so_precalibration_shrinkage_adapter(
            inference, line_probs.probability_more, line_probs, {"game_log": None, "box_score_log": None}, seed=7
        )
    assert exc.value.code == "PROP_CALIBRATION_EVIDENCE_MISSING"


def test_deterministic_given_same_seed():
    inference, line_probs = _inference()
    a = mlb_pitcher_so_precalibration_shrinkage_adapter(
        inference, line_probs.probability_more, line_probs, _features(), seed=42
    )
    b = mlb_pitcher_so_precalibration_shrinkage_adapter(
        inference, line_probs.probability_more, line_probs, _features(), seed=42
    )
    assert a.calibrated_probability == b.calibrated_probability
    assert a.lower_bound == b.lower_bound
    assert a.upper_bound == b.upper_bound
