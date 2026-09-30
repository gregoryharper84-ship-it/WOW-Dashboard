from calibration import CalibrationStatus
from ledger import PredictionRow, determine_publishability, prediction_id_for


def _valid_discrete_row(*, controlling_specialist: str | None) -> PredictionRow:
    return PredictionRow(
        event_id="NFL-2026-10-04-TEST",
        event_start_time="2026-10-04T17:00:00+00:00",
        sport="NFL",
        market_type="PROP_DISCRETE_PMF",
        stat_type="RECEIVING_YARDS",
        line=54.5,
        direction="MORE",
        source_snapshot_id="snapshot-1",
        model_timestamp="2026-09-30T16:00:00+00:00",
        controlling_specialist=controlling_specialist,
        model_provider_identity="WOW_PROP_FITTED_MODEL_V1",
        model_family="NFL_PROP_ROLLING_FITTED_V1",
        model_artifact_version="artifact-v1",
        model_artifact_checksum="a" * 64,
        model_bundle_fingerprint="b" * 64,
        model_artifact_lifecycle_state="PROSPECTIVE_CERTIFIED",
        feature_schema_version="PROP_FEATURES_V1",
        feature_transform_version="transform-v1",
        feature_snapshot_hash="c" * 64,
        training_dataset_hash="d" * 64,
        training_code_sha="e" * 40,
        specialist_version="specialist-v1",
        certification_id="cert-1",
        distribution_type="DISCRETE_PMF",
        probability_more=0.60,
        probability_less=0.40,
        push_probability=0.0,
        raw_model_probability=0.60,
        independent_model_probability=0.60,
        effective_sample_size=100.0,
        calibration_status=CalibrationStatus.PRECALIBRATION_SHRINKAGE,
        calibration_method="PRECALIBRATION_SHRINKAGE",
        calibration_version="cal-v1",
        calibration_training_n=100,
        calibration_parent_cohort="PROP_V1::NFL::RECEIVING_YARDS",
        bounds_method_version="bounds-v1",
        calibrated_probability=0.60,
        calibrated_probability_lower_bound=0.55,
        calibrated_probability_upper_bound=0.65,
        money_lane_status="RESOLVED",
    )


def test_discrete_prop_missing_controlling_specialist_fails_closed():
    row = determine_publishability(_valid_discrete_row(controlling_specialist=None))

    assert row.probability_publishable is False
    assert "controlling_specialist missing or empty" in row.data_gaps


def test_discrete_prop_with_controlling_specialist_can_publish():
    row = determine_publishability(
        _valid_discrete_row(controlling_specialist="wow.nfl-player-prop-probability-expert")
    )

    assert row.probability_publishable is True
    assert "controlling_specialist missing or empty" not in row.data_gaps


def test_prediction_identity_binds_controlling_specialist():
    first = _valid_discrete_row(controlling_specialist="wow.nfl-player-prop-probability-expert")
    second = _valid_discrete_row(controlling_specialist="wow.nfl-player-prop-probability-expert-v2")

    assert prediction_id_for(first) != prediction_id_for(second)
