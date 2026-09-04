"""Contract tests for prop_model_adapters_pitch_composition.

Loads the real artifacts produced by scripts/train_mlb_pitch_composition.py
and exercises both adapters against the governed PropDistributionContract.
"""
from __future__ import annotations

import json
import os

import pytest

from prop_distribution_contract import CertifiedBundle, PropDistributionContractError, PropInferenceRequest
from prop_fitted_provider import ResolvedArtifact
from prop_model_adapters_pitch_composition import (
    mlb_pitcher_balls_thrown_workload_nb_v1_adapter,
    mlb_pitcher_strikes_thrown_workload_nb_v1_adapter,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
STRIKES_ARTIFACT_PATH = os.path.join(DATA_DIR, "wow_mlb_strikes_thrown_artifact_v1.json")
BALLS_ARTIFACT_PATH = os.path.join(DATA_DIR, "wow_mlb_balls_thrown_artifact_v1.json")


def _load_resolved_artifact(path: str, stat_type: str, line_min: float, line_max: float) -> ResolvedArtifact:
    with open(path) as f:
        art = json.load(f)
    bundle = CertifiedBundle(
        model_artifact_version=art["model_artifact_version"],
        calibrator_version=art["calibrator_version"],
        feature_transform_version=art["feature_transform_version"],
        specialist_version=art["specialist_version"],
        certification_id="TEST-DRYRUN",
        feature_schema_version=art["feature_schema_version"],
        training_dataset_hash=art["training_dataset_hash"],
        training_code_sha=art["training_code_sha"],
        artifact_checksum="TEST-CHECKSUM",
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport="MLB",
        supported_stat_type=stat_type,
        supported_line_min=line_min,
        supported_line_max=line_max,
    )
    return ResolvedArtifact(
        artifact_id="test",
        model_family=art["model_family"],
        artifact_format=art["artifact_format"],
        artifact_payload=art["artifact_payload"],
        training_rows=art["training_rows"],
        validation_metrics=art["validation_metrics"],
        bundle=bundle,
    )


def _request(**overrides) -> PropInferenceRequest:
    base = dict(
        event_id="e1", player_id="p1", sport="MLB", league_season="2026", stat_type="STRIKES_THROWN",
        evidence_snapshot_id="snap1", market_identity_id="m1", as_of_timestamp="2026-09-04T12:00:00+00:00",
        request_id="r1", feature_schema_version="PROP_FEATURES_V1",
    )
    base.update(overrides)
    return PropInferenceRequest(**base)


SAMPLE_LOG = [
    {"outs": 18, "strikes": 62, "pitches": 94},
    {"outs": 15, "strikes": 55, "pitches": 88},
    {"outs": 21, "strikes": 71, "pitches": 102},
    {"outs": 9, "strikes": 34, "pitches": 58},
    {"outs": 17, "strikes": 60, "pitches": 91},
]


def test_strikes_pmf_sums_to_one_and_is_in_distribution():
    resolved = _load_resolved_artifact(STRIKES_ARTIFACT_PATH, "STRIKES_THROWN", 15.5, 85.5)
    out = mlb_pitcher_strikes_thrown_workload_nb_v1_adapter(resolved, _request(), {"box_score_log": SAMPLE_LOG})
    assert abs(sum(out.support.values()) - 1.0) < 1e-6
    assert out.coverage.in_distribution is True
    assert out.can_execute is False


def test_balls_pmf_sums_to_one_and_is_in_distribution():
    resolved = _load_resolved_artifact(BALLS_ARTIFACT_PATH, "BALLS_THROWN", 10.5, 55.5)
    out = mlb_pitcher_balls_thrown_workload_nb_v1_adapter(
        resolved, _request(stat_type="BALLS_THROWN"), {"box_score_log": SAMPLE_LOG}
    )
    assert abs(sum(out.support.values()) - 1.0) < 1e-6
    assert out.coverage.in_distribution is True


def test_strikes_plus_balls_predicted_means_track_total_pitches():
    """Sanity check, not an identity: the two targets are fit independently,
    but for a consistent prior history their predicted means should land
    close to the observed mean pitch count, not wildly off in either
    direction."""
    strikes_art = _load_resolved_artifact(STRIKES_ARTIFACT_PATH, "STRIKES_THROWN", 15.5, 85.5)
    balls_art = _load_resolved_artifact(BALLS_ARTIFACT_PATH, "BALLS_THROWN", 10.5, 55.5)
    strikes_out = mlb_pitcher_strikes_thrown_workload_nb_v1_adapter(
        strikes_art, _request(), {"box_score_log": SAMPLE_LOG}
    )
    balls_out = mlb_pitcher_balls_thrown_workload_nb_v1_adapter(
        balls_art, _request(stat_type="BALLS_THROWN"), {"box_score_log": SAMPLE_LOG}
    )
    strikes_mean = sum(k * p for k, p in strikes_out.support.items())
    balls_mean = sum(k * p for k, p in balls_out.support.items())
    observed_mean_pitches = sum(e["pitches"] for e in SAMPLE_LOG) / len(SAMPLE_LOG)
    assert abs((strikes_mean + balls_mean) - observed_mean_pitches) < 15.0


def test_missing_composition_field_is_typed_rejection():
    resolved = _load_resolved_artifact(STRIKES_ARTIFACT_PATH, "STRIKES_THROWN", 15.5, 85.5)
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_strikes_thrown_workload_nb_v1_adapter(
            resolved, _request(), {"box_score_log": [{"outs": 18, "strikes": 62}]}  # missing "pitches"
        )
    assert exc.value.code == "PROP_BOX_SCORE_LOG_MISSING_COMPOSITION"


def test_pitches_less_than_strikes_is_typed_rejection():
    resolved = _load_resolved_artifact(STRIKES_ARTIFACT_PATH, "STRIKES_THROWN", 15.5, 85.5)
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_strikes_thrown_workload_nb_v1_adapter(
            resolved, _request(), {"box_score_log": [{"outs": 18, "strikes": 90, "pitches": 80}]}
        )
    assert exc.value.code == "PROP_BOX_SCORE_LOG_COMPOSITION_INVALID"


def test_empty_log_is_coverage_failure_not_a_crash():
    resolved = _load_resolved_artifact(STRIKES_ARTIFACT_PATH, "STRIKES_THROWN", 15.5, 85.5)
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_strikes_thrown_workload_nb_v1_adapter(resolved, _request(), {"box_score_log": []})
    assert exc.value.code == "PROP_EVIDENCE_FEATURE_MISALIGNED"
