from __future__ import annotations

import pytest

from prop_calibration_adapters_workload import (
    mlb_batter_pa_precalibration_adapter,
    mlb_pitcher_balls_thrown_precalibration_adapter,
    mlb_pitcher_outs_precalibration_adapter,
    mlb_pitcher_strikes_thrown_precalibration_adapter,
)
from prop_distribution_contract import (
    CertifiedBundle,
    PropInferenceRequest,
    derive_line_probabilities,
)
from prop_fitted_provider import CertifiedInference, ResolvedArtifact
from prop_model_adapters_pitching_outs import mlb_pitcher_outs_workload_nb_v1_adapter
from prop_model_adapters_pitch_composition import (
    mlb_pitcher_balls_thrown_workload_nb_v1_adapter,
    mlb_pitcher_strikes_thrown_workload_nb_v1_adapter,
)
from prop_model_adapters_plate_appearances import mlb_batter_plate_appearances_nb_v1_adapter


def _bundle(*, stat_type: str, calibrator_version: str, model_version: str) -> CertifiedBundle:
    return CertifiedBundle(
        model_artifact_version=model_version,
        calibrator_version=calibrator_version,
        feature_transform_version="transform-v1",
        specialist_version="specialist@1",
        certification_id=f"cert-{stat_type.lower()}",
        feature_schema_version="PROP_FEATURES_V1",
        training_dataset_hash="dataset-hash",
        training_code_sha="code-sha",
        artifact_checksum=f"checksum-{stat_type.lower()}",
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport="MLB",
        supported_stat_type=stat_type,
        supported_line_min=0.0,
        supported_line_max=150.0,
    )


def _artifact(*, stat_type: str, model_family: str, calibrator_version: str, payload: dict) -> ResolvedArtifact:
    return ResolvedArtifact(
        artifact_id=f"artifact-{stat_type.lower()}",
        model_family=model_family,
        artifact_format="PROP_NB_SHRINKAGE_V1",
        artifact_payload=payload,
        training_rows=1000,
        validation_metrics={"test_log_loss": 1.0},
        bundle=_bundle(
            stat_type=stat_type,
            calibrator_version=calibrator_version,
            model_version=f"model-{stat_type.lower()}-v1",
        ),
    )


def _request(stat_type: str) -> PropInferenceRequest:
    return PropInferenceRequest(
        event_id="game-1",
        player_id="player-1",
        sport="MLB",
        league_season="2026",
        stat_type=stat_type,
        evidence_snapshot_id=f"snapshot-{stat_type.lower()}",
        market_identity_id=f"market-{stat_type.lower()}",
        as_of_timestamp="2026-09-05T12:00:00+00:00",
        request_id=f"request-{stat_type.lower()}",
        feature_schema_version="PROP_FEATURES_V1",
    )


def _assert_calibration(calibration):
    calibration.validate()
    assert calibration.calibration_status == "PRECALIBRATION_SHRINKAGE"
    assert calibration.bounds_method_version == "PRECALIBRATION_SHRINKAGE_EVIDENCE_BOOTSTRAP_V1"
    assert calibration.effective_sample_size == 10.0


def test_pitching_outs_calibrator_bootstraps_same_fitted_model_family():
    artifact = _artifact(
        stat_type="PITCHING_OUTS",
        model_family="MLB_PITCHER_OUTS_WORKLOAD_NB_V1",
        calibrator_version="MLB_PITCHER_OUTS_CAL_V1",
        payload={
            "league_mean_out_normal": 17.0,
            "league_mean_out_short": 8.0,
            "league_shortened_rate": 0.20,
            "dispersion_r": 25.0,
            "shortened_outs_threshold": 12.0,
            "shrinkage_k_rate": 10.0,
            "shrinkage_k_regime": 10.0,
            "max_support_k": 27,
            "feature_transform_version": "transform-v1",
        },
    )
    request = _request("PITCHING_OUTS")
    outs = [18, 17, 16, 8, 15, 18, 17, 9, 16, 18]
    features = {
        "game_log": outs,
        "box_score_log": [{"outs": value} for value in outs],
    }
    distribution = mlb_pitcher_outs_workload_nb_v1_adapter(artifact, request, features)
    line_probs = derive_line_probabilities(distribution, 15.5)
    inference = CertifiedInference(artifact=artifact, distribution=distribution)

    calibration = mlb_pitcher_outs_precalibration_adapter(
        inference, line_probs.probability_more, line_probs, features, 17
    )
    _assert_calibration(calibration)


@pytest.mark.parametrize(
    ("target", "model_family", "calibrator_version", "adapter", "calibrator", "line"),
    [
        (
            "STRIKES_THROWN",
            "MLB_PITCHER_STRIKES_THROWN_WORKLOAD_NB_V1",
            "MLB_PITCHER_STRIKES_THROWN_CAL_V1",
            mlb_pitcher_strikes_thrown_workload_nb_v1_adapter,
            mlb_pitcher_strikes_thrown_precalibration_adapter,
            55.5,
        ),
        (
            "BALLS_THROWN",
            "MLB_PITCHER_BALLS_THROWN_WORKLOAD_NB_V1",
            "MLB_PITCHER_BALLS_THROWN_CAL_V1",
            mlb_pitcher_balls_thrown_workload_nb_v1_adapter,
            mlb_pitcher_balls_thrown_precalibration_adapter,
            30.5,
        ),
    ],
)
def test_pitch_composition_calibrators_bootstrap_aligned_official_history(
    target, model_family, calibrator_version, adapter, calibrator, line
):
    if target == "STRIKES_THROWN":
        league_normal, league_short = 60.0, 35.0
    else:
        league_normal, league_short = 32.0, 20.0
    artifact = _artifact(
        stat_type=target,
        model_family=model_family,
        calibrator_version=calibrator_version,
        payload={
            "league_mean_normal": league_normal,
            "league_mean_short": league_short,
            "league_shortened_rate": 0.20,
            "dispersion_r": 30.0,
            "shortened_outs_threshold": 12.0,
            "shrinkage_k_rate": 10.0,
            "shrinkage_k_regime": 10.0,
            "max_support_k": 120,
            "feature_transform_version": "transform-v1",
        },
    )
    request = _request(target)
    rows = [
        {"outs": 18, "pitches": 92, "strikes": 61},
        {"outs": 17, "pitches": 88, "strikes": 58},
        {"outs": 16, "pitches": 84, "strikes": 56},
        {"outs": 8, "pitches": 55, "strikes": 34},
        {"outs": 15, "pitches": 80, "strikes": 53},
        {"outs": 18, "pitches": 95, "strikes": 62},
        {"outs": 17, "pitches": 90, "strikes": 59},
        {"outs": 9, "pitches": 60, "strikes": 37},
        {"outs": 16, "pitches": 86, "strikes": 57},
        {"outs": 18, "pitches": 94, "strikes": 63},
    ]
    if target == "STRIKES_THROWN":
        game_log = [row["strikes"] for row in rows]
    else:
        game_log = [row["pitches"] - row["strikes"] for row in rows]
    features = {"game_log": game_log, "box_score_log": rows}
    distribution = adapter(artifact, request, features)
    line_probs = derive_line_probabilities(distribution, line)
    inference = CertifiedInference(artifact=artifact, distribution=distribution)

    calibration = calibrator(
        inference, line_probs.probability_more, line_probs, features, 23
    )
    _assert_calibration(calibration)


def test_plate_appearances_calibrator_resamples_history_but_keeps_current_lineup_context_fixed():
    artifact = _artifact(
        stat_type="PLATE_APPEARANCES",
        model_family="MLB_BATTER_PLATE_APPEARANCES_NB_V1",
        calibrator_version="MLB_BATTER_PA_CAL_V1",
        payload={
            "league_mean_pa_by_cell": {"3_0": 4.0, "3_1": 3.8},
            "league_mean_pa_overall": 3.6,
            "dispersion_r": 20.0,
            "shrinkage_k_rate": 10.0,
            "max_support_k": 8,
            "feature_transform_version": "transform-v1",
        },
    )
    request = _request("PLATE_APPEARANCES")
    game_log = [4, 5, 4, 4, 5, 4, 4, 5, 4, 4]
    features = {
        "game_log": game_log,
        "box_score_log": [{"pa": value} for value in game_log],
        "opportunity_ledger": {"batting_slot": 3, "team_alignment": 0},
    }
    distribution = mlb_batter_plate_appearances_nb_v1_adapter(artifact, request, features)
    line_probs = derive_line_probabilities(distribution, 4.5)
    inference = CertifiedInference(artifact=artifact, distribution=distribution)

    calibration = mlb_batter_pa_precalibration_adapter(
        inference, line_probs.probability_less, line_probs, features, 29
    )
    _assert_calibration(calibration)
