"""Control-plane tests for WNBA PRA candidate derivation."""
from __future__ import annotations

from v17.wnba_composite_candidate_registry import build_candidate_row


def _source():
    return {
        "artifact_id": "source-artifact-id",
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_family": "WNBA_FANTASY_SCORE_EMPIRICAL_RESIDUAL_V1",
        "model_artifact_version": "WNBA_FANTASY_SOURCE_V1",
        "artifact_checksum": "a" * 64,
        "training_dataset_hash": "b" * 64,
        "training_code_sha": "c" * 40,
        "feature_schema_version": "PROP_FEATURES_V1",
        "feature_transform_version": "WNBA_SOURCE_V1",
        "training_rows": 100,
        "validation_metrics": {
            "train_rows": 70,
            "calibration_rows": 15,
            "untouched_test_rows": 15,
        },
        "artifact_payload": {
            "lane": "WNBA",
            "fit_contract": {
                "whole_event_chronological_split": True,
                "strictly_prior_residuals": True,
                "untouched_test_required": True,
            },
            "player_means": {
                "Test Player": {
                    "points": 12.0,
                    "rebounds": 6.0,
                    "assists": 4.0,
                    "steals": 1.0,
                    "blocks": 0.5,
                    "turnovers": 2.0,
                }
            },
            "cohort_means": {
                "WNBA": {
                    "points": 10.0,
                    "rebounds": 5.0,
                    "assists": 3.0,
                    "steals": 1.0,
                    "blocks": 0.5,
                    "turnovers": 2.0,
                }
            },
            "residual_vectors": {
                "WNBA": [
                    {"points": 2.0, "rebounds": 1.0, "assists": 1.0},
                    {"points": -2.0, "rebounds": -1.0, "assists": -1.0},
                ]
            },
        },
    }


def test_derived_registry_row_is_candidate_only_and_nonpublishable():
    row = build_candidate_row(_source())
    assert row["sport"] == "WNBA"
    assert row["stat_type"] == "PRA"
    assert row["model_family"] == "WNBA_COMPOSITE_EMPIRICAL_RESIDUAL_JOINT_V1"
    assert row["specialist_version"] == "wow.wnba-composite-prop-expert@1"
    assert row["lifecycle_state"] == "CANDIDATE"
    assert row["promoted"] is False
    assert row["active"] is False
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False
    assert row["candidate_research_active"] is True
    assert row["supported_line_min"] is None
    assert row["supported_line_max"] is None
    assert row["validation_metrics"]["joint_residual_sampling"] is True
    assert row["validation_metrics"]["independent_component_multiplication_used"] is False
    assert "WNBA_COMPOSITE_EXACT_LINE_CALIBRATION_MISSING" in row["validation_metrics"]["blockers"]
    assert row["artifact_checksum"]
    assert row["certification_id"].startswith("CANDIDATE-NOT-CERTIFIED-")
