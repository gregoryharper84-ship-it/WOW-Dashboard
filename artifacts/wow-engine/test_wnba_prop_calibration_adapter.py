import numpy as np

from prop_distribution_contract import (
    CertifiedBundle,
    CoverageDecision,
    LineProbabilities,
    RawDiscreteDistribution,
)
from prop_fitted_provider import CertifiedInference, ResolvedArtifact
from wnba_prop_calibration_adapter import (
    _ordered_history,
    _resample_history,
    effective_sample_size,
    wnba_precalibration_bootstrap_adapter,
)


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
            "model_kind": "OFFSET_POISSON_BLEND_V1",
            "stat_type": "POINTS",
            "feature_names": [
                "l10_stat_mean", "l5_stat_mean", "last_stat",
                "l10_minutes_mean", "l5_minutes_mean", "last_minutes",
            ],
            "feature_mean": [10, 10, 10, 30, 30, 30],
            "feature_scale": [5, 5, 5, 5, 5, 5],
            "correction_feature_names": [
                "log_l5_to_l10_stat", "log_last_to_l10_stat",
                "log_l5_to_l10_minutes", "log_last_to_l10_minutes",
            ],
            "coef": [0.15, 0.05, 0.10, 0.02],
            "intercept": 0.01,
            "blend_weight_glm": 0.4,
            "max_support_k": 50,
            "max_abs_z_for_coverage": 6.0,
            "feature_transform_version": "WNBA_PROP_ROLLING_FORM_V1",
        },
        training_rows=500,
        validation_metrics={"validation_status": "PASS"},
        bundle=bundle,
    )


def _inference():
    dist = RawDiscreteDistribution(
        support={0: 0.5, 1: 0.5},
        coverage=CoverageDecision(True, 0.1, ()),
        model_artifact_version="WNBA_PTS_TEST",
        training_code_sha="codesha",
        training_dataset_hash="datahash",
        feature_schema_version="PROP_FEATURES_V1",
        feature_transform_sha="transform",
        feature_snapshot_hash="snapshot",
        artifact_checksum="checksum",
        inference_timestamp="2026-08-30T00:00:00+00:00",
    )
    return CertifiedInference(artifact=_artifact(), distribution=dist)


def _features():
    return {
        "game_log": [8, 10, 9, 11, 12, 13, 12, 14, 13, 15],
        "box_score_log": [
            {"date": f"2026-06-{i:02d}", "minutes": 28.0 + (i % 4)}
            for i in range(1, 11)
        ],
    }


def test_history_bootstrap_preserves_length_and_assigns_unique_chronology():
    history = _ordered_history(_features())
    sample = _resample_history(np.random.default_rng(4), history)
    assert len(sample["game_log"]) == 10
    dates = [row["date"] for row in sample["box_score_log"]]
    assert len(set(dates)) == 10


def test_effective_sample_size_is_bounded_by_observed_history():
    history = _ordered_history(_features())
    ess = effective_sample_size(history)
    assert 1.0 <= ess <= 10.0


def test_phase_a_bootstrap_returns_ordered_nonexecution_bounds():
    line_probs = LineProbabilities(
        line=11.5,
        probability_more=0.58,
        probability_less=0.42,
        push_probability=0.0,
    )
    result = wnba_precalibration_bootstrap_adapter(
        _inference(),
        raw_probability=0.58,
        line_probs=line_probs,
        features=_features(),
        seed=42,
    )
    result.validate()
    assert result.calibration_status == "PRECALIBRATION_SHRINKAGE"
    assert 0 < result.lower_bound <= result.calibrated_probability <= result.upper_bound < 1
    assert result.effective_sample_size <= 10.0
