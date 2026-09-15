from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.binary_candidate_lifecycle import BinaryCandidateError, BinaryTrainingRow, train_binary_candidate


BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)
FEATURES = ("strength_delta", "rest_delta")


def rows(n=400):
    out = []
    for i in range(n):
        start = BASE + timedelta(days=i)
        # Strong but not perfectly deterministic signal, with both classes in
        # every chronological partition.
        strength = 1.5 if i % 4 in (0, 1, 2) else -1.5
        outcome = strength > 0
        if i % 11 == 0:
            outcome = not outcome
        out.append(BinaryTrainingRow(
            event_id=f"evt-{i}",
            event_start_time=start.isoformat(),
            feature_as_of=(start - timedelta(hours=6)).isoformat(),
            positive_outcome=outcome,
            features={"strength_delta": strength, "rest_delta": float((i % 5) - 2)},
            source_manifest_sha256=f"{i + 1:064x}"[-64:],
        ))
    return out


def test_chronological_train_calibration_test_produces_candidate_only():
    candidate = train_binary_candidate(rows(), model_family="TEST_BINARY_V1", feature_names=FEATURES)
    assert candidate.model_family == "TEST_BINARY_V1"
    assert candidate.metrics.train_n == 240
    assert candidate.metrics.calibration_n == 80
    assert candidate.metrics.test_n == 80
    assert candidate.research_screen_pass is True
    assert candidate.automatic_certification is False
    assert candidate.automatic_promotion is False
    assert candidate.probability_publishable is False
    assert candidate.can_execute is False
    assert candidate.calibrator_payload["method"] == "EMPIRICAL_WILSON_BINS_V1"
    assert all("wilson_lower" in row for row in candidate.calibrator_payload["bins"])


def test_unsorted_rows_fail_closed():
    data = rows()
    data[0], data[1] = data[1], data[0]
    with pytest.raises(BinaryCandidateError, match="rows must be ascending") as caught:
        train_binary_candidate(data, model_family="TEST_BINARY_V1", feature_names=FEATURES)
    assert caught.value.code == "BINARY_ROWS_NOT_CHRONOLOGICAL"


def test_post_start_feature_timestamp_fails_closed():
    data = rows()
    bad = data[10]
    data[10] = BinaryTrainingRow(
        event_id=bad.event_id,
        event_start_time=bad.event_start_time,
        feature_as_of=bad.event_start_time,
        positive_outcome=bad.positive_outcome,
        features=bad.features,
        source_manifest_sha256=bad.source_manifest_sha256,
    )
    with pytest.raises(BinaryCandidateError) as caught:
        train_binary_candidate(data, model_family="TEST_BINARY_V1", feature_names=FEATURES)
    assert caught.value.code == "BINARY_TEMPORAL_LEAKAGE"


def test_missing_or_nonfinite_feature_fails_closed():
    data = rows()
    bad = data[20]
    data[20] = BinaryTrainingRow(
        event_id=bad.event_id,
        event_start_time=bad.event_start_time,
        feature_as_of=bad.feature_as_of,
        positive_outcome=bad.positive_outcome,
        features={"strength_delta": float("nan"), "rest_delta": 1.0},
        source_manifest_sha256=bad.source_manifest_sha256,
    )
    with pytest.raises(BinaryCandidateError) as caught:
        train_binary_candidate(data, model_family="TEST_BINARY_V1", feature_names=FEATURES)
    assert caught.value.code == "BINARY_FEATURE_INVALID"


def test_source_manifest_hash_is_mandatory():
    data = rows()
    bad = data[30]
    data[30] = BinaryTrainingRow(
        event_id=bad.event_id,
        event_start_time=bad.event_start_time,
        feature_as_of=bad.feature_as_of,
        positive_outcome=bad.positive_outcome,
        features=bad.features,
        source_manifest_sha256="not-a-hash",
    )
    with pytest.raises(BinaryCandidateError) as caught:
        train_binary_candidate(data, model_family="TEST_BINARY_V1", feature_names=FEATURES)
    assert caught.value.code == "BINARY_SOURCE_MANIFEST_HASH_INVALID"


def test_sample_minimum_is_not_bypassed():
    with pytest.raises(BinaryCandidateError) as caught:
        train_binary_candidate(rows(299), model_family="TEST_BINARY_V1", feature_names=FEATURES)
    assert caught.value.code == "BINARY_SAMPLE_INSUFFICIENT"
