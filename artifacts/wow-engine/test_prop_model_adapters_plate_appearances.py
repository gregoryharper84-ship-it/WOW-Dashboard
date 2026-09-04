"""Contract tests for prop_model_adapters_plate_appearances.

Loads the real artifact produced by scripts/train_mlb_plate_appearances.py
and exercises the adapter against the governed PropDistributionContract.
"""
from __future__ import annotations

import json
import os

import pytest

from prop_distribution_contract import CertifiedBundle, PropDistributionContractError, PropInferenceRequest
from prop_fitted_provider import ResolvedArtifact
from prop_model_adapters_plate_appearances import mlb_batter_plate_appearances_nb_v1_adapter

ARTIFACT_PATH = os.path.join(os.path.dirname(__file__), "data", "wow_mlb_plate_appearances_artifact_v1.json")


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
        supported_stat_type="PLATE_APPEARANCES",
        supported_line_min=2.5,
        supported_line_max=5.5,
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
        event_id="e1", player_id="p1", sport="MLB", league_season="2026", stat_type="PLATE_APPEARANCES",
        evidence_snapshot_id="snap1", market_identity_id="m1", as_of_timestamp="2026-09-04T12:00:00+00:00",
        request_id="r1", feature_schema_version="PROP_FEATURES_V1",
    )
    base.update(overrides)
    return PropInferenceRequest(**base)


def test_pmf_sums_to_one_leadoff_hitter():
    resolved = _load_resolved_artifact()
    out = mlb_batter_plate_appearances_nb_v1_adapter(
        resolved, _request(),
        {"prior_pa_log": [4, 5, 4, 3, 4, 5], "batting_slot": 1, "team_alignment": 0},
    )
    assert abs(sum(out.support.values()) - 1.0) < 1e-6
    assert out.coverage.in_distribution is True


def test_leadoff_predicts_higher_pa_than_ninth_slot_same_history():
    """The whole point of using batting_slot: two players with an identical
    prior PA history should get different predictions if their confirmed
    slot for this game differs."""
    resolved = _load_resolved_artifact()
    history = {"prior_pa_log": [4, 4, 4, 4, 4]}
    leadoff = mlb_batter_plate_appearances_nb_v1_adapter(
        resolved, _request(), {**history, "batting_slot": 1, "team_alignment": 0}
    )
    ninth = mlb_batter_plate_appearances_nb_v1_adapter(
        resolved, _request(), {**history, "batting_slot": 9, "team_alignment": 0}
    )
    leadoff_mean = sum(k * p for k, p in leadoff.support.items())
    ninth_mean = sum(k * p for k, p in ninth.support.items())
    assert leadoff_mean > ninth_mean


def test_missing_batting_slot_is_coverage_failure_not_a_crash():
    resolved = _load_resolved_artifact()
    out = mlb_batter_plate_appearances_nb_v1_adapter(
        resolved, _request(), {"prior_pa_log": [4, 4, 4], "team_alignment": 0}  # no batting_slot
    )
    assert out.coverage.in_distribution is False
    assert "BATTING_SLOT_UNCONFIRMED" in out.coverage.coverage_failures
    # still returns a governed (abstained) distribution, not a crash or a silent guess
    assert abs(sum(out.support.values()) - 1.0) < 1e-6


def test_zero_prior_games_is_coverage_failure():
    resolved = _load_resolved_artifact()
    out = mlb_batter_plate_appearances_nb_v1_adapter(
        resolved, _request(), {"prior_pa_log": [], "batting_slot": 3, "team_alignment": 1}
    )
    assert out.coverage.in_distribution is False
    assert "ZERO_PRIOR_GAMES" in out.coverage.coverage_failures


def test_malformed_prior_pa_log_is_typed_rejection():
    resolved = _load_resolved_artifact()
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_batter_plate_appearances_nb_v1_adapter(
            resolved, _request(), {"prior_pa_log": [4, -1, 3], "batting_slot": 2, "team_alignment": 0}
        )
    assert exc.value.code == "PROP_PRIOR_PA_LOG_VALUE_INVALID"


def test_low_lineup_slot_ceiling_tag_fires_for_ninth_slot():
    resolved = _load_resolved_artifact()
    out = mlb_batter_plate_appearances_nb_v1_adapter(
        resolved, _request(), {"prior_pa_log": [3, 3, 4], "batting_slot": 9, "team_alignment": 1}
    )
    assert "pa-low-lineup-slot-ceiling" in out.failure_path_evidence["tags"]
