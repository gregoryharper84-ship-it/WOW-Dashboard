from ledger import PredictionRow, determine_publishability


def _valid_prop_row(*, calibrated_probability: float, lower_bound: float) -> PredictionRow:
    raw_probability = 0.63
    return PredictionRow(
        event_id="MLB-2026-08-30-CIN-CHC",
        event_start_time="2026-08-30T23:20:00+00:00",
        sport="MLB",
        market_type="PROP_DISCRETE_PMF",
        stat_type="PITCHER_STRIKEOUTS",
        line=6.5,
        direction="LESS",
        source_snapshot_id="11111111-1111-1111-1111-111111111111",
        model_timestamp="2026-08-30T21:00:00+00:00",
        player="Chase Burns",
        model_provider_identity="WOW_PROP_FITTED_MODEL_V1",
        model_family="MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
        model_artifact_version="MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29",
        model_artifact_checksum="checksum",
        model_bundle_fingerprint="bundle",
        model_artifact_lifecycle_state="PROSPECTIVE_CERTIFIED",
        feature_schema_version="PROP_FEATURES_V1",
        feature_transform_version="MLB_PITCHER_SO_TRANSFORM_V1",
        feature_snapshot_hash="feature-snapshot",
        training_dataset_hash="training-dataset",
        training_code_sha="training-code",
        specialist_version="wow.mlb-pitcher-failure-path-expert@1",
        certification_id="MLB-SO-CERT",
        distribution_type="DISCRETE_PMF",
        probability_more=0.37,
        probability_less=raw_probability,
        push_probability=0.0,
        raw_model_probability=raw_probability,
        effective_sample_size=10.0,
        calibration_status="PRECALIBRATION_SHRINKAGE",
        calibration_method="CONSERVATIVE_EMPIRICAL_BAYES_SHRINKAGE_V1",
        calibration_version="MLB_PITCHER_SO_CAL_V1",
        bounds_method_version="PRECALIBRATION_SHRINKAGE_EVIDENCE_BOOTSTRAP_V1",
        calibrated_probability=calibrated_probability,
        calibrated_probability_lower_bound=lower_bound,
        calibrated_probability_upper_bound=max(calibrated_probability, 0.70),
        money_lane_status="PAYOUT_UNRESOLVED",
    )


def test_phase_a_model_supported_row_persists_native_model_qualified_hold_ceiling():
    row = determine_publishability(
        _valid_prop_row(calibrated_probability=0.63, lower_bound=0.56)
    )
    assert row.probability_publishable is True
    assert row.probability_ceiling == "MODEL_QUALIFIED_HOLD"
    assert "money_lane_status != RESOLVED (payout unresolved)" in row.blockers
    assert "MONEY" not in row.probability_ceiling


def test_phase_a_burns_like_row_persists_real_low_probability_rejection():
    row = determine_publishability(
        _valid_prop_row(calibrated_probability=0.537, lower_bound=0.517)
    )
    assert row.probability_publishable is True
    assert row.probability_ceiling == "NO_LOW_PROBABILITY"
