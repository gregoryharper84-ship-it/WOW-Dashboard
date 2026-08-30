from prop_distribution_contract import CertifiedBundle, PropInferenceRequest
from prop_fitted_provider import ResolvedArtifact
from wnba_prop_model_adapter import feature_vector, wnba_prop_poisson_logglm_v1_adapter


def _artifact():
    bundle = CertifiedBundle(
        model_artifact_version="WNBA_PTS_TEST",
        calibrator_version="WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1",
        feature_transform_version="WNBA_PROP_ROLLING_FORM_V1",
        specialist_version="wow.wnba-player-prop-probability-expert@1",
        certification_id="TEST",
        feature_schema_version="PROP_FEATURES_V1",
        training_dataset_hash="datahash",
        training_code_sha="codesha",
        artifact_checksum="checksum",
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport="WNBA",
        supported_stat_type="POINTS",
        supported_line_min=0.0,
        supported_line_max=60.0,
    )
    return ResolvedArtifact(
        artifact_id="a1",
        model_family="WNBA_PROP_POISSON_LOGGLM_V1",
        artifact_format="JSON_POISSON_LOGGLM_V1",
        artifact_payload={
            "stat_type": "POINTS",
            "feature_names": [
                "l10_stat_mean", "l5_stat_mean", "last_stat",
                "l10_minutes_mean", "l5_minutes_mean", "last_minutes",
            ],
            "feature_mean": [10, 10, 10, 30, 30, 30],
            "feature_scale": [5, 5, 5, 5, 5, 5],
            "coef": [0.2, 0.1, 0.05, 0.1, 0.05, 0.01],
            "intercept": 2.2,
            "max_support_k": 50,
            "max_abs_z_for_coverage": 6.0,
            "feature_transform_version": "WNBA_PROP_ROLLING_FORM_V1",
        },
        training_rows=500,
        validation_metrics={"validation_status": "PASS"},
        bundle=bundle,
    )


def _request():
    return PropInferenceRequest(
        event_id="event",
        player_id="player",
        sport="WNBA",
        league_season="2026",
        stat_type="POINTS",
        evidence_snapshot_id="snap",
        market_identity_id="market",
        as_of_timestamp="2026-08-30T00:00:00+00:00",
        request_id="req",
        feature_schema_version="PROP_FEATURES_V1",
    )


def _features(reverse=False):
    game_log = [float(i) for i in range(1, 11)]
    box = [{"date": f"2026-06-{i:02d}", "minutes": 20.0 + i} for i in range(1, 11)]
    if reverse:
        game_log = list(reversed(game_log))
        box = list(reversed(box))
    return {"game_log": game_log, "box_score_log": box}


def test_feature_vector_sorts_chronology_and_is_order_invariant():
    assert feature_vector(_features()) == feature_vector(_features(reverse=True))
    vector = feature_vector(_features())
    assert vector[:3] == (5.5, 8.0, 10.0)
    assert vector[3:] == (25.5, 28.0, 30.0)


def test_adapter_returns_normalized_direction_free_pmf():
    result = wnba_prop_poisson_logglm_v1_adapter(_artifact(), _request(), _features())
    assert result.coverage.in_distribution is True
    assert abs(sum(result.support.values()) - 1.0) < 1e-9
    assert result.publication_status == "NOT_EVALUATED"
    assert result.can_execute is False
    assert set(result.support) == set(range(51))
