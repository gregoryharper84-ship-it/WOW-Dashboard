"""Adversarial tests for #1649 research-only tennis calibration shadow."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from v17.binary_candidate_lifecycle import BinaryTrainingRow, train_binary_candidate
from v17.tennis_calibration_shadow import (
    ALPHAS,
    CAN_EXECUTE,
    CONTRACT,
    TennisCalibrationShadowError,
    replay_tennis_calibration_shadow,
)

BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)
NAMES = ("strength_delta", "rest_delta")
FAMILY = "TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1_ATP"


def _rows(n=500, tour="ATP"):
    data = []
    for i in range(n):
        start = BASE + timedelta(hours=6 * i)
        strength = 1.6 if i % 4 in (0, 1, 2) else -1.6
        result = strength > 0
        if i % 11 == 0 or i % 23 == 0:
            result = not result
        data.append(BinaryTrainingRow(
            event_id=f"TENNIS:{tour}:fixture-{i}",
            event_start_time=start.isoformat(),
            feature_as_of=(start - timedelta(seconds=1)).isoformat(),
            positive_outcome=result,
            features={"strength_delta":strength, "rest_delta":float(i % 6) - 3.0},
            source_manifest_sha256=f"{i + 1:064x}"[-64:],
        ))
    return data


def _candidate(rows, tour="ATP"):
    return train_binary_candidate(
        rows, model_family=f"TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1_{tour}",
        feature_names=NAMES, min_rows=300
    )


def test_shadow_is_deterministic_and_never_admits_bets_or_certification():
    rows = _rows()
    candidate = _candidate(rows)
    first = replay_tennis_calibration_shadow(rows, candidate)
    second = replay_tennis_calibration_shadow(rows, candidate)
    assert first == second
    assert first["contract"] == CONTRACT
    assert CAN_EXECUTE is False
    assert first["selected_alpha"] in ALPHAS
    assert first["selection_fit_n"] + first["selection_eval_n"] == candidate.metrics.calibration_n
    assert first["selection_used_untouched_test"] is False
    assert first["raw_test_brier"] == pytest.approx(candidate.metrics.raw_brier, abs=1e-9)
    assert first["raw_test_log_loss"] == pytest.approx(candidate.metrics.raw_log_loss, abs=1e-9)
    assert len(first["selection_trace"]) == len(ALPHAS)
    assert all("eligible" in row for row in first["selection_trace"])
    assert first["historical_reconstruction_only"] is True
    assert first["archived_pregame_receipt_verified"] is False
    for key in ("certification_eligible", "automatic_certification",
                "automatic_promotion", "probability_publishable", "can_execute"):
        assert first[key] is False


def test_wrong_tour_cannot_share_atp_fitted_artifact():
    rows = _rows()
    candidate = _candidate(rows)
    changed = _rows(tour="WTA")
    with pytest.raises(TennisCalibrationShadowError, match="TENNIS_EVENT_TOUR_MISMATCH"):
        replay_tennis_calibration_shadow(changed, candidate)


def test_other_sport_family_cannot_use_tennis_shadow():
    rows = _rows()
    candidate = replace(_candidate(rows), model_family="NFL_DYNAMIC_TEAM_STATE_LOGIT_V2")
    with pytest.raises(TennisCalibrationShadowError, match="TENNIS_MODEL_FAMILY_UNSUPPORTED"):
        replay_tennis_calibration_shadow(rows, candidate)


def test_mutated_historical_outcome_cannot_replay_unchanged_model():
    rows = _rows()
    candidate = _candidate(rows)
    tampered = list(rows)
    tampered[-1] = replace(tampered[-1], positive_outcome=not tampered[-1].positive_outcome)
    with pytest.raises(TennisCalibrationShadowError, match="TENNIS_FROZEN_DATASET_HASH_MISMATCH"):
        replay_tennis_calibration_shadow(tampered, candidate)


def test_frozen_logistic_artifact_corruption_fails_closed():
    rows = _rows()
    candidate = _candidate(rows)
    artifact = dict(candidate.artifact_payload)
    artifact["intercept"] = float(artifact["intercept"]) + 0.25
    corrupted = replace(candidate, artifact_payload=artifact)
    with pytest.raises(TennisCalibrationShadowError, match="TENNIS_FROZEN_RAW_REPLAY_MISMATCH"):
        replay_tennis_calibration_shadow(rows, corrupted)


def test_frozen_calibration_partition_identity_mismatch_fails_closed():
    rows = _rows()
    candidate = _candidate(rows)
    artifact = dict(candidate.artifact_payload)
    artifact["calibration_end_index"] = int(artifact["calibration_end_index"]) + 1
    corrupted = replace(candidate, artifact_payload=artifact)
    with pytest.raises(TennisCalibrationShadowError, match="TENNIS_FROZEN_PARTITION_MISMATCH"):
        replay_tennis_calibration_shadow(rows, corrupted)


def test_unsorted_input_never_silently_reorders_training_corpus():
    rows = _rows()
    candidate = _candidate(rows)
    changed = list(rows)
    changed[0], changed[1] = changed[1], changed[0]
    with pytest.raises(TennisCalibrationShadowError, match="TENNIS_ROWS_NOT_CHRONOLOGICAL"):
        replay_tennis_calibration_shadow(changed, candidate)


def test_wta_has_independent_calibration_shadow_and_no_promotion():
    rows = _rows(tour="WTA")
    candidate = _candidate(rows, tour="WTA")
    result = replay_tennis_calibration_shadow(rows, candidate)
    assert result["model_family"].endswith("_WTA")
    assert result["test_n"] == candidate.metrics.test_n
    assert result["selection_used_untouched_test"] is False
    assert result["certification_eligible"] is False
    assert result["probability_publishable"] is False


def test_invalid_scale_from_saved_artifact_is_typed_hold():
    rows = _rows()
    candidate = _candidate(rows)
    artifact = dict(candidate.artifact_payload)
    artifact["scaler_scale"] = [0.0] * len(NAMES)
    with pytest.raises(TennisCalibrationShadowError, match="TENNIS_FROZEN_ARTIFACT_INVALID"):
        replay_tennis_calibration_shadow(
            rows, replace(candidate, artifact_payload=artifact)
        )
