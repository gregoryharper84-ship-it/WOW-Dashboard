from __future__ import annotations

import numpy as np

from v17.experiments import ncaaf_publication_bound_challenger as v2
from v17.experiments import ncaaf_publication_bound_challenger_v3 as v3


def _candidate():
    return {
        "candidate_id": "candidate-v3",
        "model_family": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "model_artifact_version": "artifact-v3",
        "training_dataset_hash": "a" * 64,
        "artifact_checksum": "b" * 64,
        "training_rows": 50,
        "calibration_rows": 50,
        "test_rows": 50,
    }


def _rows(total):
    return [
        {
            "event_start_time": f"2026-01-{1 + i // 24:02d}T{i % 24:02d}:00:00+00:00",
            "outcome_json": {"home_win": True},
            "market_features_used": False,
            "can_execute": False,
        }
        for i in range(total)
    ]


def _patch(monkeypatch, *, forward_n=0):
    candidate = _candidate()
    total = 150 + forward_n
    rows = _rows(total)
    probabilities = np.linspace(0.56, 0.84, total)
    logits = np.log(probabilities / (1.0 - probabilities))
    leverage = np.linspace(0.2, 1.2, total)
    design = np.column_stack([np.ones(total), leverage])
    outcomes = np.ones(total, dtype=int)

    monkeypatch.setattr(v3, "_exact_candidate", lambda db, candidate_id: candidate)
    monkeypatch.setattr(
        v3,
        "_verified_receipt",
        lambda db, candidate: {"receipt_id": "receipt-v3", "evidence_sha256": "c" * 64},
    )
    monkeypatch.setattr(v3, "_paginate_rows", lambda db, candidate: rows)
    monkeypatch.setattr(v3, "_artifact_design", lambda candidate, rows: (probabilities, logits, design))
    monkeypatch.setattr(v3, "_outcomes", lambda rows: outcomes)
    monkeypatch.setattr(v3, "_parameter_covariance", lambda design, probabilities: (np.diag([0.005, 0.005]), 2))
    monkeypatch.setattr(
        v3,
        "_point_replay",
        lambda candidate, probabilities, outcomes: {"passed": True, "comparable": True},
    )


def test_v3_uses_principled_one_sided_scale_without_relaxing_width_gate():
    assert v3.ONE_SIDED_Z < v2.LOGIT_Z
    assert v3.MAX_MEDIAN_HALF_WIDTH == v2.MAX_MEDIAN_HALF_WIDTH
    assert v3.OVERALL_RELIABILITY_TOLERANCE == v2.OVERALL_RELIABILITY_TOLERANCE
    assert v3.BIN_RELIABILITY_TOLERANCE == v2.BIN_RELIABILITY_TOLERANCE
    assert v3.THRESHOLD_RELIABILITY_TOLERANCE == v2.THRESHOLD_RELIABILITY_TOLERANCE


def test_v3_historical_replay_cannot_promote_without_new_forward_cohort(monkeypatch):
    _patch(monkeypatch, forward_n=0)
    result = v3.run_ncaaf_publication_bound_challenger_v3(object(), "candidate-v3")

    assert result["status"] == "EXPERIMENT_COMPLETE"
    assert result["historical_test_replay"]["prior_exposure"] is True
    assert result["historical_test_replay"]["promotion_evidence_allowed"] is False
    assert result["forward_validation"]["n"] == 0
    assert result["forward_validation"]["forward_validation_pass"] is False
    assert result["recommendation_state"] == "FORWARD_VALIDATION_REQUIRED"
    assert result["review_policy"]["v2_gate_relaxation_allowed"] is False
    assert result["review_policy"]["historical_test_retuning_allowed"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["database_mutated"] is False
    assert result["production_registry_mutated"] is False
    assert result["can_execute"] is False


def test_v3_requires_genuinely_later_forward_sample_before_review(monkeypatch):
    _patch(monkeypatch, forward_n=v3.MIN_FORWARD_N)
    result = v3.run_ncaaf_publication_bound_challenger_v3(object(), "candidate-v3")

    assert result["forward_validation"]["n"] == v3.MIN_FORWARD_N
    assert result["forward_validation"]["genuinely_later_than_frozen_test"] is True
    assert result["forward_validation"]["sample_size_pass"] is True
    assert result["forward_validation"]["forward_validation_pass"] is True
    assert result["recommendation_state"] == "GOVERNED_REVIEW_REQUIRED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
