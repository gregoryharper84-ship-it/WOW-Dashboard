"""Contract tests for prop_model_adapters_pitching_outs.

Loads the real artifact produced by scripts/train_mlb_pitching_outs.py
(data/wow_mlb_pitching_outs_artifact_v1.json) -- these are the actual fitted
constants from real Retrosheet-derived data, not synthetic literals -- and
exercises the adapter against the governed PropDistributionContract exactly
as the runtime router would.

NOTE: this test does not require the artifact to be registered in Supabase.
It builds a ResolvedArtifact directly from the training script's JSON output
so the adapter's math and contract compliance can be verified independently
of the (separate, governance-gated) registration decision.
"""
from __future__ import annotations

import json
import os

import pytest

from prop_distribution_contract import CertifiedBundle, PropDistributionContractError, PropInferenceRequest
from prop_fitted_provider import ResolvedArtifact
from prop_model_adapters_pitching_outs import mlb_pitcher_outs_workload_nb_v1_adapter

ARTIFACT_PATH = os.path.join(os.path.dirname(__file__), "data", "wow_mlb_pitching_outs_artifact_v1.json")


def _load_resolved_artifact() -> ResolvedArtifact:
    with open(ARTIFACT_PATH) as f:
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
        supported_stat_type="PITCHING_OUTS",
        supported_line_min=3.5,
        supported_line_max=24.5,
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
        event_id="e1", player_id="p1", sport="MLB", league_season="2026", stat_type="PITCHING_OUTS",
        evidence_snapshot_id="snap1", market_identity_id="m1", as_of_timestamp="2026-09-04T12:00:00+00:00",
        request_id="r1", feature_schema_version="PROP_FEATURES_V1",
    )
    base.update(overrides)
    return PropInferenceRequest(**base)


def test_pmf_sums_to_one_and_is_in_distribution():
    resolved = _load_resolved_artifact()
    box_score_log = [{"outs": o} for o in [18, 15, 21, 9, 17, 19, 16, 20]]
    out = mlb_pitcher_outs_workload_nb_v1_adapter(resolved, _request(), {"box_score_log": box_score_log})
    total = sum(out.support.values())
    assert abs(total - 1.0) < 1e-6
    assert out.coverage.in_distribution is True
    assert out.can_execute is False
    assert out.publication_status == "NOT_EVALUATED"


def test_predicted_mean_tracks_prior_history():
    resolved = _load_resolved_artifact()
    box_score_log = [{"outs": o} for o in [18, 15, 21, 9, 17, 19, 16, 20]]
    out = mlb_pitcher_outs_workload_nb_v1_adapter(resolved, _request(), {"box_score_log": box_score_log})
    mean = sum(k * p for k, p in out.support.items())
    # 8-start prior average is 16.875; shrunk estimate should land in a
    # sane neighborhood, not collapse to the unconditional league mean.
    assert 13.0 < mean < 20.0


def test_empty_box_score_log_is_coverage_failure_not_a_crash():
    resolved = _load_resolved_artifact()
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_outs_workload_nb_v1_adapter(resolved, _request(), {"box_score_log": []})
    assert exc.value.code == "PROP_EVIDENCE_FEATURE_MISALIGNED"


def test_malformed_box_score_entry_is_typed_rejection():
    resolved = _load_resolved_artifact()
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_outs_workload_nb_v1_adapter(resolved, _request(), {"box_score_log": [{"not_outs": 5}]})
    assert exc.value.code == "PROP_BOX_SCORE_LOG_MISSING_OUTS"


def test_early_hook_risk_tag_fires_on_heavy_shortened_history():
    resolved = _load_resolved_artifact()
    box_score_log = [{"outs": o} for o in [9, 10, 8, 12, 9, 11]]  # all shortened outings
    out = mlb_pitcher_outs_workload_nb_v1_adapter(resolved, _request(), {"box_score_log": box_score_log})
    assert "outs-early-hook-risk" in out.failure_path_evidence["tags"]
