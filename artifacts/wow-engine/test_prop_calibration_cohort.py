from __future__ import annotations

from prop_discrete_engine import prop_calibration_parent_cohort
from prop_distribution_contract import CertifiedBundle, PropInferenceRequest
from prop_fitted_provider import ResolvedArtifact


def test_prop_calibration_parent_cohort_is_stable_and_artifact_specific():
    request = PropInferenceRequest(
        event_id="MLB:TEST:1",
        player_id="wow-name:test",
        sport="MLB",
        league_season="2026",
        stat_type="PITCHER_STRIKEOUTS",
        evidence_snapshot_id="22222222-2222-4222-8222-222222222222",
        market_identity_id="wow-market:test",
        as_of_timestamp="2026-08-29T12:00:00+00:00",
        request_id="req-1",
        feature_schema_version="PROP_FEATURES_V1",
    )
    bundle = CertifiedBundle(
        model_artifact_version="MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29",
        calibrator_version="MLB_PITCHER_SO_CAL_V1",
        feature_transform_version="MLB_PITCHER_SO_TRANSFORM_V1",
        specialist_version="wow.mlb-pitcher-failure-path-expert@1",
        certification_id="PROP-CERT-2026-08-29-MLB-PITCHER-SO-V1",
        feature_schema_version="PROP_FEATURES_V1",
        training_dataset_hash="a" * 64,
        training_code_sha="b" * 40,
        artifact_checksum="c" * 64,
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport="MLB",
        supported_stat_type="PITCHER_STRIKEOUTS",
        supported_line_min=0.5,
        supported_line_max=12.5,
    )
    artifact = ResolvedArtifact(
        artifact_id="11111111-1111-4111-8111-111111111111",
        model_family="MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
        artifact_format="JSON_FITTED_CONSTANTS_V1",
        artifact_payload={},
        training_rows=4489,
        validation_metrics={},
        bundle=bundle,
    )

    cohort = prop_calibration_parent_cohort(request, artifact)
    assert cohort == (
        "PROP_V1::MLB::PITCHER_STRIKEOUTS::"
        "MLB_PITCHER_SO_FAILURE_PATH_NB_V1::"
        "MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29"
    )
