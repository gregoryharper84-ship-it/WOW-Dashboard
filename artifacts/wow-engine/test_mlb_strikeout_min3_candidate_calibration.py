from __future__ import annotations

from prop_calibration_adapters import mlb_pitcher_so_precalibration_shrinkage_adapter
from prop_distribution_contract import CertifiedBundle, PropInferenceRequest, derive_line_probabilities
from prop_fitted_provider import CertifiedInference, ResolvedArtifact
from prop_model_adapters import MLB_PITCHER_SO_MODEL_FAMILY, mlb_pitcher_so_failure_path_nb_v1_adapter


def _artifact() -> ResolvedArtifact:
    bundle = CertifiedBundle(
        model_artifact_version="MLB_PITCHER_SO_FAILURE_PATH_NB_V1_TEST",
        calibrator_version="MLB_PITCHER_SO_CAL_V1",
        feature_transform_version="MLB_PITCHER_SO_TRANSFORM_V1",
        specialist_version="wow.mlb-pitcher-failure-path-expert@1",
        certification_id="CERT-MIN3-TEST",
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
    return ResolvedArtifact(
        artifact_id="11111111-1111-4111-8111-111111111111",
        model_family=MLB_PITCHER_SO_MODEL_FAMILY,
        artifact_format="JSON_V1",
        artifact_payload={
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
        },
        training_rows=4489,
        validation_metrics={},
        bundle=bundle,
    )


def _request() -> PropInferenceRequest:
    return PropInferenceRequest(
        event_id="MLB:MIN3:1",
        player_id="wow-name:three-start-pitcher",
        sport="MLB",
        league_season="2026",
        stat_type="PITCHER_STRIKEOUTS",
        evidence_snapshot_id="22222222-2222-4222-8222-222222222222",
        market_identity_id="wow-market:min3",
        as_of_timestamp="2026-09-23T12:00:00+00:00",
        request_id="req-min3",
        feature_schema_version="PROP_FEATURES_V1",
    )


def test_three_start_history_produces_normalized_distribution_and_valid_calibrated_bounds():
    artifact = _artifact()
    request = _request()
    features = {
        "game_log": [5.0, 6.0, 4.0],
        "box_score_log": [{"outs": 17}, {"outs": 18}, {"outs": 15}],
        "opponent_context": None,
    }

    distribution = mlb_pitcher_so_failure_path_nb_v1_adapter(artifact, request, features)
    assert abs(sum(distribution.support.values()) - 1.0) < 1e-9
    line_probs = derive_line_probabilities(distribution, 4.5)
    inference = CertifiedInference(artifact=artifact, distribution=distribution)
    calibrated = mlb_pitcher_so_precalibration_shrinkage_adapter(
        inference,
        line_probs.probability_more,
        line_probs,
        features,
        seed=23,
    )

    calibrated.validate()
    assert calibrated.effective_sample_size == 3.0
    assert 0.0 < calibrated.lower_bound <= calibrated.calibrated_probability <= calibrated.upper_bound < 1.0
    assert calibrated.calibration_status == "PRECALIBRATION_SHRINKAGE"
