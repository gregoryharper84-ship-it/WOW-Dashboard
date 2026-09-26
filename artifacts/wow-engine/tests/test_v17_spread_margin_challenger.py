from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest

from v17.spread_margin_challenger import (
    AUTOMATIC_CERTIFICATION,
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
    GLOBAL_TERMINAL_REDUCER,
    MONEYLINE_TO_SPREAD_CONVERSION_ALLOWED,
    PROBABILITY_PUBLISHABLE,
    MarginDistributionArtifact,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    build_dynamic_margin_rows,
    research_receipt,
    score_home_spread,
    train_margin_distribution_candidate,
)


def _artifact(*, residuals=(-1.0, 0.0, 1.0)) -> MarginDistributionArtifact:
    return MarginDistributionArtifact(
        sport="NFL",
        model_family="NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version="NFL_SPREAD_MARGIN_TEAM_STATE_V1",
        feature_names=("x",),
        scaler_mean=(0.0,),
        scaler_scale=(1.0,),
        coefficients=(0.0,),
        intercept=0.0,
        calibration_residuals=tuple(residuals),
        train_rows=10,
        calibration_rows=len(residuals),
        test_rows=5,
        training_dataset_hash="abc",
        ridge_alpha=4.0,
    )


def _row(index: int, *, leaked: bool = False) -> MarginTrainingRow:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
    x1 = float((index % 9) - 4)
    x2 = float(((index * 3) % 11) - 5)
    deterministic_noise = float(((index * 7) % 5) - 2)
    margin = int(round(1.7 * x1 - 0.8 * x2 + deterministic_noise))
    feature_as_of = start + timedelta(seconds=1) if leaked else start - timedelta(seconds=1)
    return MarginTrainingRow(
        event_id=f"event-{index:04d}",
        event_start_time=start.isoformat(),
        feature_as_of=feature_as_of.isoformat(),
        margin=margin,
        features={"x1": x1, "x2": x2},
        source_manifest_sha256=f"sha-{index}",
    )


def test_governance_is_shadow_only_and_non_executable():
    assert CAN_EXECUTE is False
    assert DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS is True
    assert GLOBAL_TERMINAL_REDUCER == "V17_TERMINAL_REDUCER"
    assert AUTOMATIC_CERTIFICATION is False
    assert AUTOMATIC_PROMOTION is False
    assert PROBABILITY_PUBLISHABLE is False
    assert MONEYLINE_TO_SPREAD_CONVERSION_ALLOWED is False


def test_exact_integer_line_has_explicit_push_probability():
    scored = score_home_spread(_artifact(), {"x": 0.0}, home_spread=0.0)
    assert scored["p_cover"] == pytest.approx(1 / 3)
    assert scored["p_push"] == pytest.approx(1 / 3)
    assert scored["p_not_cover"] == pytest.approx(1 / 3)
    assert scored["probability_sum"] == pytest.approx(1.0)
    assert scored["probability_publishable"] is False
    assert scored["can_execute"] is False


def test_half_point_line_cannot_push_with_integer_margin_distribution():
    scored = score_home_spread(_artifact(), {"x": 0.0}, home_spread=0.5)
    assert scored["p_cover"] == pytest.approx(2 / 3)
    assert scored["p_push"] == 0.0
    assert scored["p_not_cover"] == pytest.approx(1 / 3)
    assert scored["probability_sum"] == pytest.approx(1.0)


def test_cover_probability_is_monotone_as_home_line_becomes_more_favorable():
    artifact = _artifact(residuals=(-10, -7, -3, -1, 0, 2, 4, 8, 12))
    probs = [score_home_spread(artifact, {"x": 0.0}, home_spread=line)["p_cover"] for line in (-7.5, -3.5, 0.5, 3.5, 7.5)]
    assert probs == sorted(probs)


def test_training_is_chronological_and_returns_holdout_calibration_metrics():
    rows = [_row(i) for i in range(120)]
    artifact, metrics = train_margin_distribution_candidate(rows, sport="NFL", min_rows=100)
    assert artifact.train_rows == 72
    assert artifact.calibration_rows == 24
    assert artifact.test_rows == 24
    assert artifact.feature_names == ("x1", "x2")
    assert len(artifact.calibration_residuals) == 24
    assert metrics["evaluation_rows"] == 24
    assert metrics["evaluation_lines"] > 1
    assert metrics["evaluation_predictions"] == metrics["evaluation_rows"] * metrics["evaluation_lines"]
    assert math.isfinite(metrics["margin_mae"])
    assert math.isfinite(metrics["margin_rmse"])
    assert math.isfinite(metrics["three_way_brier"])
    assert math.isfinite(metrics["cover_brier"])
    assert math.isfinite(metrics["cover_log_loss"])
    assert math.isfinite(metrics["cover_ece"])
    for cohort in ("favorites", "underdogs", "small_spreads", "large_spreads", "integer_lines", "half_lines"):
        assert cohort in metrics["cohorts"]
    assert metrics["market_features_used"] is False
    assert metrics["moneyline_probability_used"] is False
    assert metrics["spread_line_used_as_feature"] is False
    assert metrics["probability_publishable"] is False
    assert metrics["can_execute"] is False


def test_feature_as_of_leakage_fails_closed_before_model_fit():
    rows = [_row(i) for i in range(120)]
    rows[70] = _row(70, leaked=True)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        train_margin_distribution_candidate(rows, sport="NFL", min_rows=100)
    assert exc.value.code == "SPREAD_FEATURE_LEAKAGE"


def test_feature_schema_mismatch_fails_closed():
    rows = [_row(i) for i in range(120)]
    bad = rows[30]
    rows[30] = MarginTrainingRow(
        event_id=bad.event_id,
        event_start_time=bad.event_start_time,
        feature_as_of=bad.feature_as_of,
        margin=bad.margin,
        features={"x1": 1.0, "different": 2.0},
        source_manifest_sha256=bad.source_manifest_sha256,
    )
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        train_margin_distribution_candidate(rows, sport="NFL", min_rows=100)
    assert exc.value.code == "SPREAD_FEATURE_SCHEMA_MISMATCH"


def test_research_receipt_cannot_self_promote_or_publish():
    rows = [_row(i) for i in range(120)]
    artifact, metrics = train_margin_distribution_candidate(rows, sport="NFL", min_rows=100)
    receipt = research_receipt(artifact, metrics)
    assert receipt["status"] == "EXPERIMENT_CREATED"
    assert receipt["next_required_stage"] == "REAL_HISTORICAL_REPLAY_AND_GOVERNED_CERTIFICATION"
    assert receipt["automatic_certification"] is False
    assert receipt["automatic_promotion"] is False
    assert receipt["probability_publishable"] is False
    assert receipt["can_execute"] is False


def test_dynamic_builder_uses_prior_team_state_and_ignores_market_line_fields():
    base = datetime(2025, 9, 1, tzinfo=timezone.utc)
    events = []
    for i in range(10):
        events.append({
            "event_id": f"nfl-{i}",
            "event_start_time": (base + timedelta(days=7 * i)).isoformat(),
            "season": 2025,
            "home_team": "A" if i % 2 == 0 else "B",
            "away_team": "B" if i % 2 == 0 else "A",
            "home_score": 24 + (i % 4),
            "away_score": 20 + ((i * 2) % 5),
            "home_spread": -3.5,
            "sportsbook_implied_probability": 0.91,
            "source_manifest": {"source": "fixture"},
        })
    rows, feature_names = build_dynamic_margin_rows(events, sport="NFL", min_prior_games=2)
    assert rows
    assert feature_names
    for row in rows:
        assert "home_spread" not in row.features
        assert "sportsbook_implied_probability" not in row.features
        assert datetime.fromisoformat(row.feature_as_of) < datetime.fromisoformat(row.event_start_time)


def test_unsupported_sport_does_not_fall_back_to_generic_probability():
    rows = [_row(i) for i in range(120)]
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        train_margin_distribution_candidate(rows, sport="MLB", min_rows=100)
    assert exc.value.code == "SPREAD_SPORT_UNSUPPORTED"
