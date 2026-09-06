from __future__ import annotations

import json
import os

import pytest

from prop_distribution_contract import CertifiedBundle, PropInferenceRequest, derive_line_probabilities
from prop_fitted_provider import CertifiedInference, ResolvedArtifact
from prop_calibration_adapters_mlb_workload import (
    BOUNDS_METHOD_VERSION,
    mlb_batter_pa_precalibration_adapter,
    mlb_pitcher_balls_precalibration_adapter,
    mlb_pitcher_outs_precalibration_adapter,
    mlb_pitcher_strikes_precalibration_adapter,
)
from prop_model_adapters_pitching_outs import mlb_pitcher_outs_workload_nb_v1_adapter
from prop_model_adapters_pitch_composition import (
    mlb_pitcher_balls_thrown_workload_nb_v1_adapter,
    mlb_pitcher_strikes_thrown_workload_nb_v1_adapter,
)
from prop_model_adapters_plate_appearances import mlb_batter_plate_appearances_nb_v1_adapter

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _artifact(filename: str) -> ResolvedArtifact:
    with open(os.path.join(DATA_DIR, filename)) as f:
        art = json.load(f)
    bundle = CertifiedBundle(
        model_artifact_version=art["model_artifact_version"],
        calibrator_version=art["calibrator_version"],
        feature_transform_version=art["feature_transform_version"],
        specialist_version=art["specialist_version"],
        certification_id="TEST-CERT",
        feature_schema_version=art["feature_schema_version"],
        training_dataset_hash=art["training_dataset_hash"],
        training_code_sha=art["training_code_sha"],
        artifact_checksum="TEST-CHECKSUM",
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport=art["supported_sport"],
        supported_stat_type=art["supported_stat_type"],
        supported_line_min=float(art["supported_line_min"]),
        supported_line_max=float(art["supported_line_max"]),
    )
    return ResolvedArtifact(
        artifact_id="test-artifact",
        model_family=art["model_family"],
        artifact_format=art["artifact_format"],
        artifact_payload=art["artifact_payload"],
        training_rows=int(art["training_rows"]),
        validation_metrics=art["validation_metrics"],
        bundle=bundle,
    )


def _request(stat_type: str) -> PropInferenceRequest:
    return PropInferenceRequest(
        event_id="e1",
        player_id="p1",
        sport="MLB",
        league_season="2026",
        stat_type=stat_type,
        evidence_snapshot_id="snap1",
        market_identity_id="m1",
        as_of_timestamp="2026-09-05T12:00:00+00:00",
        request_id="r1",
        feature_schema_version="PROP_FEATURES_V1",
    )


def _assert_valid_phase_a(result) -> None:
    result.validate()
    assert result.calibration_status == "PRECALIBRATION_SHRINKAGE"
    assert result.calibration_method == "CONSERVATIVE_EMPIRICAL_BAYES_SHRINKAGE_V1"
    assert result.bounds_method_version == BOUNDS_METHOD_VERSION
    assert 0.0 < result.lower_bound <= result.calibrated_probability <= result.upper_bound < 1.0
    assert result.effective_sample_size > 0


def test_pitching_outs_phase_a_reuses_candidate_evidence_and_model_math():
    artifact = _artifact("wow_mlb_pitching_outs_artifact_v1.json")
    features = {"box_score_log": [{"outs": o} for o in [18, 15, 21, 9, 17, 19, 16, 20, 14, 18]]}
    distribution = mlb_pitcher_outs_workload_nb_v1_adapter(artifact, _request("PITCHING_OUTS"), features)
    probs = derive_line_probabilities(distribution, 16.5)
    result = mlb_pitcher_outs_precalibration_adapter(
        CertifiedInference(artifact=artifact, distribution=distribution),
        probs.probability_more,
        probs,
        features,
        1701,
    )
    _assert_valid_phase_a(result)
    assert result.effective_sample_size == 10.0


COMPOSITION_LOG = [
    {"outs": 18, "strikes": 62, "pitches": 94},
    {"outs": 15, "strikes": 55, "pitches": 88},
    {"outs": 21, "strikes": 71, "pitches": 102},
    {"outs": 9, "strikes": 34, "pitches": 58},
    {"outs": 17, "strikes": 60, "pitches": 91},
    {"outs": 19, "strikes": 64, "pitches": 97},
    {"outs": 12, "strikes": 42, "pitches": 67},
    {"outs": 18, "strikes": 59, "pitches": 90},
]


@pytest.mark.parametrize(
    "filename,stat_type,line,model_adapter,cal_adapter",
    [
        (
            "wow_mlb_strikes_thrown_artifact_v1.json",
            "STRIKES_THROWN",
            55.5,
            mlb_pitcher_strikes_thrown_workload_nb_v1_adapter,
            mlb_pitcher_strikes_precalibration_adapter,
        ),
        (
            "wow_mlb_balls_thrown_artifact_v1.json",
            "BALLS_THROWN",
            30.5,
            mlb_pitcher_balls_thrown_workload_nb_v1_adapter,
            mlb_pitcher_balls_precalibration_adapter,
        ),
    ],
)
def test_pitch_composition_phase_a_reuses_same_prior_start_composition(
    filename, stat_type, line, model_adapter, cal_adapter
):
    artifact = _artifact(filename)
    features = {"box_score_log": COMPOSITION_LOG}
    distribution = model_adapter(artifact, _request(stat_type), features)
    probs = derive_line_probabilities(distribution, line)
    result = cal_adapter(
        CertifiedInference(artifact=artifact, distribution=distribution),
        probs.probability_more,
        probs,
        features,
        1702,
    )
    _assert_valid_phase_a(result)
    assert result.effective_sample_size == float(len(COMPOSITION_LOG))


def test_plate_appearances_phase_a_resamples_history_but_keeps_current_lineup_context_fixed():
    artifact = _artifact("wow_mlb_plate_appearances_artifact_v1.json")
    features = {
        "prior_pa_log": [4, 5, 4, 3, 4, 5, 4, 4, 5, 3],
        "batting_slot": 1,
        "team_alignment": 0,
    }
    distribution = mlb_batter_plate_appearances_nb_v1_adapter(
        artifact, _request("PLATE_APPEARANCES"), features
    )
    probs = derive_line_probabilities(distribution, 3.5)
    result = mlb_batter_pa_precalibration_adapter(
        CertifiedInference(artifact=artifact, distribution=distribution),
        probs.probability_more,
        probs,
        features,
        1703,
    )
    _assert_valid_phase_a(result)
    assert result.effective_sample_size == 10.0


def test_plate_appearances_calibration_fails_closed_without_current_lineup_context():
    artifact = _artifact("wow_mlb_plate_appearances_artifact_v1.json")
    features = {"prior_pa_log": [4, 4, 5, 3], "team_alignment": 0}
    distribution = mlb_batter_plate_appearances_nb_v1_adapter(
        artifact, _request("PLATE_APPEARANCES"), features
    )
    probs = derive_line_probabilities(distribution, 3.5)
    with pytest.raises(Exception) as exc:
        mlb_batter_pa_precalibration_adapter(
            CertifiedInference(artifact=artifact, distribution=distribution),
            probs.probability_more,
            probs,
            features,
            1704,
        )
    assert getattr(exc.value, "code", None) == "PROP_CALIBRATION_EVIDENCE_MISSING"
