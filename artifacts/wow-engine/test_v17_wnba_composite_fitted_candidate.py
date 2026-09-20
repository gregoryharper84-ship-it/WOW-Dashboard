"""Fail-closed tests for the V17 WNBA PRA/composite fitted candidate."""
from __future__ import annotations

import pytest

from v17 import wnba_composite_fitted_candidate as composite


SOURCE = {
    "lane": "WNBA",
    "fit_contract": {
        "whole_event_chronological_split": True,
        "strictly_prior_residuals": True,
        "untouched_test_required": True,
        "train_fraction": 0.70,
        "calibration_fraction": 0.15,
    },
    "player_means": {
        "Test Player": {
            "points": 10.0,
            "rebounds": 5.0,
            "assists": 5.0,
            "steals": 1.0,
            "blocks": 0.5,
            "turnovers": 2.0,
        }
    },
    "cohort_means": {
        "WNBA": {
            "points": 8.0,
            "rebounds": 4.0,
            "assists": 3.0,
            "steals": 1.0,
            "blocks": 0.5,
            "turnovers": 2.0,
        }
    },
    # P/R/A move together. The candidate must sample each vector jointly rather
    # than independently multiplying component probabilities.
    "residual_vectors": {
        "WNBA": [
            {"points": 5.0, "rebounds": 3.0, "assists": 2.0, "steals": 0.0, "blocks": 0.0, "turnovers": 0.0},
            {"points": -5.0, "rebounds": -3.0, "assists": -2.0, "steals": 0.0, "blocks": 0.0, "turnovers": 0.0},
        ]
    },
}


def _candidate():
    return composite.derive_candidate_payload(
        SOURCE,
        source_model_artifact_version="WNBA_FANTASY_SOURCE_V1",
        source_artifact_checksum="a" * 64,
        source_training_dataset_hash="b" * 64,
        source_training_code_sha="c" * 40,
    )


def test_derive_candidate_preserves_joint_component_source_and_governance():
    candidate = _candidate()
    assert candidate["model_family"] == composite.MODEL_FAMILY
    assert candidate["components"] == ["points", "rebounds", "assists"]
    assert candidate["independent_component_multiplication_permitted"] is False
    assert candidate["calibration_status"] == "BLOCKED_NO_PRA_EXACT_LINE_CALIBRATION_ARTIFACT"
    assert candidate["certification_status"] == "CANDIDATE_ONLY"
    assert candidate["probability_publishable"] is False
    assert candidate["rank_eligible"] is False
    assert candidate["can_execute"] is False
    assert candidate["source_artifact"]["model_artifact_version"] == "WNBA_FANTASY_SOURCE_V1"
    assert len(candidate["residual_vectors"]["WNBA"]) == 2


def test_pra_candidate_scores_both_directions_but_never_publishes(monkeypatch):
    monkeypatch.setattr(composite, "MIN_STANDARD_SIMULATIONS", 1000)
    candidate = _candidate()
    more = composite.simulate_exact_line(
        candidate,
        player="Test Player",
        stat_type="PTS+REB+AST",
        exact_line=20.5,
        side="MORE",
        simulation_count=1000,
        seed=17,
    )
    less = composite.simulate_exact_line(
        candidate,
        player="Test Player",
        stat_type="PRA",
        exact_line=20.5,
        side="LESS",
        simulation_count=1000,
        seed=17,
    )
    assert more["stat_type"] == "PRA"
    assert more["joint_residual_sampling"] is True
    assert more["independent_component_multiplication_used"] is False
    assert more["P(MORE)"] == pytest.approx(less["P(MORE)"])
    assert more["P(LESS)"] == pytest.approx(less["P(LESS)"])
    assert more["P(MORE)"] + more["P(LESS)"] + more["P(PUSH)"] == pytest.approx(1.0)
    assert more["raw_candidate_probability"] == pytest.approx(more["P(MORE)"])
    assert less["raw_candidate_probability"] == pytest.approx(less["P(LESS)"])
    assert more["calibrated_probability"] is None
    assert more["calibrated_lower_bound"] is None
    assert more["probability_publishable"] is False
    assert more["rank_eligible"] is False
    assert more["can_execute"] is False
    assert "WNBA_COMPOSITE_CALIBRATION_NOT_CERTIFIED" in more["blockers"]
    assert "WNBA_COMPOSITE_CANDIDATE_NOT_CERTIFIED" in more["blockers"]


def test_candidate_requires_minimum_simulation_floor(monkeypatch):
    monkeypatch.setattr(composite, "MIN_STANDARD_SIMULATIONS", 1000)
    with pytest.raises(composite.WNBACompositeCandidateError, match="at least 1000"):
        composite.simulate_exact_line(
            _candidate(),
            player="Test Player",
            stat_type="PRA",
            exact_line=20.5,
            side="MORE",
            simulation_count=999,
        )


def test_independence_guard_cannot_be_removed():
    candidate = _candidate()
    candidate["independent_component_multiplication_permitted"] = True
    with pytest.raises(composite.WNBACompositeCandidateError, match="independence guard"):
        composite.simulate_exact_line(
            candidate,
            player="Test Player",
            stat_type="PRA",
            exact_line=20.5,
            side="MORE",
        )


def test_invalid_source_without_strictly_prior_residuals_fails_closed():
    broken = dict(SOURCE)
    broken["fit_contract"] = {
        "whole_event_chronological_split": True,
        "strictly_prior_residuals": False,
    }
    with pytest.raises(composite.WNBACompositeCandidateError, match="strictly prior"):
        composite.derive_candidate_payload(
            broken,
            source_model_artifact_version="bad",
            source_artifact_checksum="a" * 64,
            source_training_dataset_hash="b" * 64,
            source_training_code_sha="c" * 40,
        )
