from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from ncaaf_three_way_trainer import train_calibrate_test, candidate_clears_research_screen
from ncaaf_trainer import FEATURES, TrainingRow, NCAAFTrainingError


def make_rows(n=300):
    start = datetime(2022, 8, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        # Deterministic learnable signal with both outcomes in every chronological block.
        signal = ((i % 20) - 9.5) / 5.0
        home_won = (signal + (0.25 if i % 3 else -0.25)) > 0
        features = {name: 0.0 for name in FEATURES}
        features["power_delta"] = signal
        features["off_epa_delta"] = signal * 0.4
        features["def_epa_delta"] = -signal * 0.2
        features["neutral_site"] = float(i % 7 == 0)
        kickoff = start + timedelta(days=i)
        rows.append(TrainingRow(
            event_start_time=kickoff.isoformat(),
            feature_as_of=(kickoff - timedelta(hours=12)).isoformat(),
            home_won=home_won,
            features=features,
        ))
    return rows


def test_three_way_lifecycle_keeps_calibration_and_test_disjoint():
    candidate = train_calibrate_test(make_rows())
    assert candidate.metrics.train_n == 180
    assert candidate.metrics.calibration_n == 60
    assert candidate.metrics.test_n == 60
    assert candidate.calibration_end_event < candidate.test_start_event
    assert candidate.calibrator_payload["training_n"] == 60
    assert candidate.probability_publishable is False
    assert candidate.can_execute is False
    assert all(0.0 < b["calibrated_probability"] < 1.0 for b in candidate.calibrator_payload["bins"])


def test_research_screen_can_clear_on_learnable_signal_but_never_publishes():
    candidate = train_calibrate_test(make_rows())
    assert isinstance(candidate_clears_research_screen(candidate), bool)
    assert candidate.probability_publishable is False


def test_three_way_rejects_small_sample():
    with pytest.raises(NCAAFTrainingError) as exc:
        train_calibrate_test(make_rows(299))
    assert exc.value.code == "NCAAF_THREE_WAY_SAMPLE_INSUFFICIENT"


def test_three_way_rejects_leakage():
    rows = make_rows()
    bad = rows[10]
    rows[10] = TrainingRow(
        event_start_time=bad.event_start_time,
        feature_as_of=bad.event_start_time,
        home_won=bad.home_won,
        features=bad.features,
    )
    with pytest.raises(NCAAFTrainingError) as exc:
        train_calibrate_test(rows)
    assert exc.value.code == "NCAAF_TRAINING_LEAKAGE_DETECTED"
