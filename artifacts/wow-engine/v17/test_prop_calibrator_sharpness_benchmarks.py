"""Research-only skill benchmarks for a fitted, held-out prop calibrator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v17 import prop_calibrator_candidate_runtime as subject


def _artifact():
    return {
        "sport": "MLB",
        "stat_type": "PITCHER_STRIKEOUTS",
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
        "model_artifact_version": "test-no-publication",
        "artifact_checksum": "synthetic-sharpness",
        "can_execute": False,
    }


def _observations(n=300):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        subject.RawForwardObservation(
            prediction_id=f"pred-{i}",
            event_id=f"game-{i}",
            player=f"Pitcher {i}",
            sport="MLB",
            stat_type="PITCHER_STRIKEOUTS",
            line=5.5,
            direction="MORE",
            raw_model_probability=0.9,
            outcome=i % 2,
            model_timestamp=(start + timedelta(hours=i - 1)).isoformat(),
            event_start_time=(start + timedelta(hours=i)).isoformat(),
            feature_schema_version="PROP_FEATURES_V1",
            model_family="MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
            model_artifact_version="test-no-publication",
            artifact_checksum="synthetic-sharpness",
        )
        for i in range(n)
    ]


def test_benchmarks_use_frozen_training_base_rate_and_flag_weak_sharpness(monkeypatch):
    class Coeff:
        a = 1.0
        b = -0.1

        @staticmethod
        def apply(probability):
            return 0.8

    def fake_fit(probabilities, outcomes, folds, timestamps):
        assert len(probabilities) == len(outcomes) == len(folds) == len(timestamps)
        return SimpleNamespace(
            coefficients=Coeff(),
            metrics=SimpleNamespace(
                brier=0.32, log_loss=0.90, ece=0.30, calibration_bias=0.30
            ),
        )

    monkeypatch.setattr(subject, "phase_b_platt", fake_fit)
    monkeypatch.setattr(subject, "PHASE_C_MIN_N", 10_000)
    packet = subject.build_calibrator_candidate_packet(_artifact(), _observations())

    assert packet.status == "CALIBRATOR_CANDIDATE_REVIEW_PACKET_READY"
    assert packet.certification_review_packet_ready is True
    assert packet.probability_publishable is False
    assert packet.rank_eligible is False
    assert packet.can_execute is False
    assert packet.holdout_reference_metrics is not None
    neutral = packet.holdout_reference_metrics["neutral_50_percent"]
    training = packet.holdout_reference_metrics["training_only_event_rate"]
    assert neutral["brier"] == pytest.approx(0.25)
    assert neutral["log_loss"] == pytest.approx(0.6931471805599453)
    assert training["probability"] == pytest.approx(0.5)
    assert training["brier"] == pytest.approx(0.25)
    assert packet.calibrated_holdout_metrics["brier"] > neutral["brier"]
    assert packet.holdout_skill_scores["brier_skill_vs_neutral_50_percent"] < 0
    assert "CALIBRATED_WORSE_THAN_NEUTRAL_50_PERCENT_BRIER" in packet.holdout_sharpness_flags
    assert "CALIBRATED_WORSE_THAN_TRAINING_ONLY_EVENT_RATE_LOG_LOSS" in packet.holdout_sharpness_flags

    # Additional review diagnostics are hashed, preventing stale packet reuse.
    assert len(packet.evidence_hash) == 64


def test_unready_candidates_have_no_invented_benchmark_metrics():
    packet = subject.build_calibrator_candidate_packet(_artifact(), [])
    assert packet.certification_review_packet_ready is False
    assert packet.holdout_reference_metrics is None
    assert packet.holdout_skill_scores is None
    assert packet.holdout_sharpness_flags == ()
    assert packet.can_execute is False
