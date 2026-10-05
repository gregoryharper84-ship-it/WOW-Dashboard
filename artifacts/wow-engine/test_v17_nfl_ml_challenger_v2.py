from __future__ import annotations

import pytest

from v17.nfl_ml_challenger_v2 import (
    BOOTSTRAP_QUANTILE,
    CAN_EXECUTE,
    CHALLENGER_ID,
    DECAY_HALF_LIFE_DAYS,
    FEATURE_SCHEMA_VERSION,
    HISTORY_SUFFICIENCY_GAMES,
    LOCAL_CALIBRATION_K,
    LOWER_BOUND_METHOD,
    PROBABILITY_PUBLISHABLE,
    PROMOTION_AUTHORIZED,
    build_forward_shadow_record,
    composite_lower_bound,
    empirical_lower,
    nearest_local_lower,
    wilson_lower,
)


def test_research_configuration_is_fail_closed():
    assert CHALLENGER_ID == "NFL_ML_STATIONARY_DECAY_PLATT_COMPOSITE_LB_V1"
    assert FEATURE_SCHEMA_VERSION == "NFL_EVENT_PREGAME_STATIONARY_DECAY_V1"
    assert HISTORY_SUFFICIENCY_GAMES == 8
    assert DECAY_HALF_LIFE_DAYS == 224
    assert LOCAL_CALIBRATION_K == 50
    assert BOOTSTRAP_QUANTILE == 0.10
    assert PROBABILITY_PUBLISHABLE is False
    assert PROMOTION_AUTHORIZED is False
    assert CAN_EXECUTE is False


def test_wilson_and_bootstrap_lower_bounds_are_bounded():
    local = wilson_lower(36, 50)
    assert 0.0 < local < 36 / 50

    q10 = empirical_lower([0.51, 0.54, 0.58, 0.62, 0.70, 0.73])
    assert q10 == pytest.approx(0.51)


def test_local_lower_uses_nearest_k_calibration_decisions():
    calibration = [
        (0.50 + i / 1000.0, 1 if i % 3 else 0)
        for i in range(100)
    ]
    lower, n = nearest_local_lower(0.58, calibration, k=50)
    assert n == 50
    assert 0.0 <= lower <= 0.58


def test_composite_is_minimum_of_independent_research_bounds():
    calibration = [(0.57 + (i % 8) / 100.0, 1 if i < 37 else 0) for i in range(50)]
    bootstrap = [0.40, 0.45, 0.50, 0.55, 0.58, 0.60, 0.61, 0.62, 0.64, 0.66]
    evidence = composite_lower_bound(0.62, calibration, bootstrap)

    assert evidence.composite_lower == min(
        evidence.point_probability,
        evidence.local_wilson_lower,
        evidence.bootstrap_q10_lower,
    )
    assert evidence.composite_lower <= 0.62
    assert evidence.method == LOWER_BOUND_METHOD


def test_forward_shadow_record_cannot_publish_promote_or_execute():
    calibration = [(0.56 + (i % 5) / 100.0, 1 if i < 38 else 0) for i in range(50)]
    bootstrap = [0.42 + i / 100.0 for i in range(20)]
    evidence = composite_lower_bound(0.60, calibration, bootstrap)

    row = build_forward_shadow_record(
        official_event_id="2026_04_ATL_NO",
        provider_event_id="401872979",
        event_start_time_utc="2026-10-06T00:15:00Z",
        prediction_created_at="2026-10-05T03:00:00Z",
        home_team="NO",
        away_team="ATL",
        selected_participant="ATL",
        calibrated_probability=0.60,
        lower_bound=evidence,
        training_cutoff="2024-12-31",
        calibration_season=2025,
        source_feature_hash="a" * 64,
        research_metadata={"purpose": "test"},
    )

    assert row["lifecycle_state"] == "FORWARD_SHADOW"
    assert row["probability_publishable"] is False
    assert row["promotion_authorized"] is False
    assert row["can_execute"] is False
    assert row["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_forward_shadow_rejects_point_mismatch():
    calibration = [(0.58, 1)] * 50
    evidence = composite_lower_bound(0.60, calibration, [0.55] * 20)

    with pytest.raises(ValueError, match="POINT_MISMATCH"):
        build_forward_shadow_record(
            official_event_id="x",
            provider_event_id=None,
            event_start_time_utc="2026-10-06T00:15:00Z",
            prediction_created_at="2026-10-05T03:00:00Z",
            home_team="H",
            away_team="A",
            selected_participant="A",
            calibrated_probability=0.61,
            lower_bound=evidence,
            training_cutoff="2024-12-31",
            calibration_season=2025,
            source_feature_hash="b" * 64,
        )
