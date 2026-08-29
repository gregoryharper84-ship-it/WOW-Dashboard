from __future__ import annotations

import pytest

from calibration import CalibrationStatus
from prop_discrete_engine import (
    PropCalibrationOutput,
    PropCalibrationUnavailable,
    clear_prop_calibration_adapters,
    register_prop_calibration_adapter,
    score_discrete_prop_end_to_end,
)
from prop_distribution_contract import (
    CertifiedBundle,
    CoverageDecision,
    PropInferenceRequest,
    RawDiscreteDistribution,
)
from prop_fitted_provider import CertifiedInference, ResolvedArtifact


def _request():
    return PropInferenceRequest(
        event_id="WNBA:TEST:1",
        player_id="wow-name:test",
        sport="WNBA",
        league_season="2026",
        stat_type="POINTS",
        evidence_snapshot_id="22222222-2222-4222-8222-222222222222",
        market_identity_id="wow-market:test",
        as_of_timestamp="2026-08-29T12:00:00+00:00",
        request_id="req-1",
        feature_schema_version="PROP_FEATURES_V1",
    )


def _inference(*, in_distribution=True):
    bundle = CertifiedBundle(
        model_artifact_version="WNBA_POINTS_MODEL_V1",
        calibrator_version="WNBA_POINTS_CAL_V1",
        feature_transform_version="PROP_TRANSFORM_V1",
        specialist_version="wow.wnba-player-prop-generative-expert@1",
        certification_id="CERT-WNBA-POINTS-1",
        feature_schema_version="PROP_FEATURES_V1",
        training_dataset_hash="a" * 64,
        training_code_sha="b" * 40,
        artifact_checksum="c" * 64,
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport="WNBA",
        supported_stat_type="POINTS",
        supported_line_min=0.0,
        supported_line_max=60.0,
    )
    artifact = ResolvedArtifact(
        artifact_id="11111111-1111-4111-8111-111111111111",
        model_family="TEST_DISCRETE_V1",
        artifact_format="TEST_ONLY",
        artifact_payload={},
        training_rows=1200,
        validation_metrics={"brier": 0.19},
        bundle=bundle,
    )
    distribution = RawDiscreteDistribution(
        support={20: 0.10, 21: 0.15, 22: 0.20, 23: 0.25, 24: 0.20, 25: 0.10},
        coverage=CoverageDecision(in_distribution, 0.1 if in_distribution else 0.95, () if in_distribution else ("ROLE_OOD",)),
        model_artifact_version=bundle.model_artifact_version,
        training_code_sha=bundle.training_code_sha,
        training_dataset_hash=bundle.training_dataset_hash,
        feature_schema_version=bundle.feature_schema_version,
        feature_transform_sha="d" * 64,
        feature_snapshot_hash="e" * 64,
        artifact_checksum=bundle.artifact_checksum,
        inference_timestamp="2026-08-29T12:00:01+00:00",
    )
    return CertifiedInference(artifact=artifact, distribution=distribution)


def _infer_factory(inference):
    def infer(_client, *, request, line, features):
        assert request.feature_schema_version == "PROP_FEATURES_V1"
        assert line == 22.0
        assert features["game_log"]
        return inference

    return infer


def _calibrator(_inference, raw_probability, line_probs, features, seed):
    assert raw_probability == pytest.approx(0.55)
    assert line_probs.push_probability == pytest.approx(0.20)
    assert seed == 7
    assert features["game_log"]
    return PropCalibrationOutput(
        calibration_status=CalibrationStatus.PLATT_TIME_SPLIT_V1,
        calibration_method=CalibrationStatus.PLATT_TIME_SPLIT_V1,
        calibrated_probability=0.58,
        lower_bound=0.54,
        upper_bound=0.63,
        bounds_method_version="PREDICTIVE_BOUNDS_V1",
        effective_sample_size=8.4,
    )


def teardown_function():
    clear_prop_calibration_adapters()


def test_discrete_prop_path_publishes_without_fake_pitcher_simulation_fields():
    register_prop_calibration_adapter("WNBA_POINTS_CAL_V1", _calibrator)
    result = score_discrete_prop_end_to_end(
        client=object(),
        request=_request(),
        event_start_time="2026-08-30T00:00:00+00:00",
        player="Test Player",
        line=22.0,
        direction="MORE",
        source_snapshot_id="22222222-2222-4222-8222-222222222222",
        features={"game_log": [20] * 10},
        seed=7,
        infer_fn=_infer_factory(_inference()),
    )

    row = result.row
    assert row.probability_publishable is True
    assert row.market_type == "PROP_DISCRETE_PMF"
    assert row.simulation_draws is None
    assert row.regime_probability_sum is None
    assert row.model_provider_identity == "WOW_PROP_FITTED_MODEL_V1"
    assert row.model_artifact_version == "WNBA_POINTS_MODEL_V1"
    assert row.calibration_version == "WNBA_POINTS_CAL_V1"
    assert row.distribution_type == "DISCRETE_PMF"
    assert row.probability_more == pytest.approx(0.55)
    assert row.probability_less == pytest.approx(0.25)
    assert row.push_probability == pytest.approx(0.20)
    assert row.probability_more + row.probability_less + row.push_probability == pytest.approx(1.0)
    assert row.raw_model_probability == pytest.approx(row.probability_more)
    assert row.calibrated_probability_lower_bound == pytest.approx(0.54)
    assert row.market_prior_weight == 0.0
    assert not hasattr(row, "can_execute")


def test_missing_calibration_adapter_abstains_without_legacy_fallback():
    with pytest.raises(PropCalibrationUnavailable) as exc:
        score_discrete_prop_end_to_end(
            client=object(),
            request=_request(),
            event_start_time="2026-08-30T00:00:00+00:00",
            player="Test Player",
            line=22.0,
            direction="MORE",
            source_snapshot_id="22222222-2222-4222-8222-222222222222",
            features={"game_log": [20] * 10},
            seed=7,
            infer_fn=_infer_factory(_inference()),
        )
    assert exc.value.code == "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE"


def test_out_of_distribution_provider_abstention_blocks_before_calibration():
    register_prop_calibration_adapter("WNBA_POINTS_CAL_V1", _calibrator)
    with pytest.raises(PropCalibrationUnavailable) as exc:
        score_discrete_prop_end_to_end(
            client=object(),
            request=_request(),
            event_start_time="2026-08-30T00:00:00+00:00",
            player="Test Player",
            line=22.0,
            direction="MORE",
            source_snapshot_id="22222222-2222-4222-8222-222222222222",
            features={"game_log": [20] * 10},
            seed=7,
            infer_fn=_infer_factory(_inference(in_distribution=False)),
        )
    assert exc.value.code == "PROP_MODEL_OUT_OF_DISTRIBUTION"


def test_whole_line_push_is_preserved_and_less_scores_from_same_pmf():
    def less_calibrator(_inference, raw_probability, line_probs, features, seed):
        assert raw_probability == pytest.approx(0.25)
        assert line_probs.probability_more == pytest.approx(0.55)
        assert line_probs.push_probability == pytest.approx(0.20)
        return PropCalibrationOutput(
            calibration_status=CalibrationStatus.PLATT_TIME_SPLIT_V1,
            calibration_method=CalibrationStatus.PLATT_TIME_SPLIT_V1,
            calibrated_probability=0.28,
            lower_bound=0.21,
            upper_bound=0.35,
            bounds_method_version="PREDICTIVE_BOUNDS_V1",
            effective_sample_size=8.4,
        )

    register_prop_calibration_adapter("WNBA_POINTS_CAL_V1", less_calibrator)
    result = score_discrete_prop_end_to_end(
        client=object(),
        request=_request(),
        event_start_time="2026-08-30T00:00:00+00:00",
        player="Test Player",
        line=22.0,
        direction="LESS",
        source_snapshot_id="22222222-2222-4222-8222-222222222222",
        features={"game_log": [20] * 10},
        seed=7,
        infer_fn=_infer_factory(_inference()),
    )
    assert result.row.raw_model_probability == pytest.approx(0.25)
    assert result.row.push_probability == pytest.approx(0.20)
