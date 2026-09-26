from datetime import datetime, timedelta, timezone

import pytest

from v17 import spread_forward_shadow as shadow
from v17.spread_margin_challenger import (
    MarginDistributionArtifact,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    build_dynamic_margin_rows,
)


def _event(index, home, away, home_score, away_score):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=index)
    return {
        "event_id": f"e{index}",
        "event_start_time": start.isoformat(),
        "season": 2026,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "source_manifest": {"market_features_used": False},
    }


def _history_and_target():
    prior = [
        _event(0, "A", "C", 24, 10),
        _event(1, "B", "D", 20, 17),
        _event(2, "C", "A", 14, 21),
        _event(3, "D", "B", 7, 28),
        _event(4, "A", "D", 35, 14),
        _event(5, "B", "C", 31, 13),
    ]
    target = _event(6, "A", "B", 27, 24)
    return prior, target


def test_forward_features_exactly_match_replay_feature_family():
    prior, target = _history_and_target()
    replay_rows, _ = build_dynamic_margin_rows(prior + [target], sport="NCAAF", min_prior_games=3)
    replay_target = next(row for row in replay_rows if row.event_id == target["event_id"])
    forward_features, audit = shadow.build_forward_matchup_features(
        prior,
        target_event=target,
        min_prior_games=3,
    )
    assert forward_features == replay_target.features
    assert audit["home_prior_events"] == 3
    assert audit["away_prior_events"] == 3
    assert audit["market_features_used"] is False
    assert audit["moneyline_probability_used"] is False
    assert audit["spread_line_used_as_feature"] is False
    assert audit["can_execute"] is False


def test_forward_features_fail_closed_on_insufficient_history():
    prior, target = _history_and_target()
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        shadow.build_forward_matchup_features(prior[:2], target_event=target, min_prior_games=3)
    assert exc.value.code == "SPREAD_FORWARD_HISTORY_INSUFFICIENT"
    assert exc.value.code != "MODEL_UNAVAILABLE"


def _artifact():
    return MarginDistributionArtifact(
        sport="NCAAF",
        model_family="NCAAF_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version="NCAAF_SPREAD_MARGIN_TEAM_STATE_V1",
        feature_names=("x",),
        scaler_mean=(0.0,),
        scaler_scale=(1.0,),
        coefficients=(1.0,),
        intercept=0.0,
        calibration_residuals=(-3.0, 0.0, 3.0),
        train_rows=600,
        calibration_rows=200,
        test_rows=200,
        training_dataset_hash="dataset",
        ridge_alpha=4.0,
    )


def _training_row(start):
    return MarginTrainingRow(
        event_id="hist",
        event_start_time=(start - timedelta(days=2)).isoformat(),
        feature_as_of=(start - timedelta(days=2, seconds=1)).isoformat(),
        margin=3,
        features={"x": 1.0},
        source_manifest_sha256="sha",
    )


def test_forward_shadow_uses_fixed_challenger_and_line_only_as_threshold(monkeypatch):
    start = datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc)
    rows = [_training_row(start)]
    artifact = _artifact()
    seen = {}
    monkeypatch.setattr(shadow, "load_replay_rows", lambda *_args, **_kwargs: rows)

    def fit(fit_rows, *, sport, min_rows, ridge_alpha):
        seen.update({"rows": fit_rows, "sport": sport, "min_rows": min_rows, "ridge_alpha": ridge_alpha})
        return artifact, {"margin_mae": 10.0, "cover_brier": 0.22, "cover_log_loss": 0.65, "cover_ece": 0.04}

    monkeypatch.setattr(shadow, "train_margin_distribution_candidate", fit)
    monkeypatch.setattr(shadow, "load_ncaaf_settled_events", lambda _client: [{"event_id": "old"}])
    monkeypatch.setattr(
        shadow,
        "build_forward_matchup_features",
        lambda *_args, **_kwargs: ({"x": 1.0}, {"market_features_used": False, "spread_line_used_as_feature": False}),
    )
    monkeypatch.setattr(
        shadow,
        "score_home_spread",
        lambda _artifact, features, *, home_spread: {
            "predicted_home_margin_center": 4.0,
            "p_cover": 0.61,
            "p_push": 0.01,
            "p_not_cover": 0.38,
            "p_cover_given_no_push": 0.6161616,
            "research_lower_bound_cover": 0.55,
            "distribution_sample_n": 200,
        },
    )
    result = shadow.run_ncaaf_forward_shadow(
        object(),
        event_id="401000001",
        event_start_time=start.isoformat(),
        home_team="Home",
        away_team="Away",
        home_spread=-3.5,
        season=2026,
    )
    assert seen["sport"] == "NCAAF"
    assert seen["min_rows"] == shadow.MIN_TRAIN_ROWS == 300
    assert seen["ridge_alpha"] == shadow.RIDGE_ALPHA == 4.0
    assert result["p_cover"] == 0.61
    assert result["p_push"] == 0.01
    assert result["p_not_cover"] == 0.38
    assert result["training_cutoff_event_time"] < result["event_start_time"]
    assert result["spread_line_used_as_feature"] is False
    assert result["market_probability_substitution_used"] is False
    assert result["moneyline_probability_used"] is False
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
    assert result["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"


def test_forward_shadow_blocks_target_at_or_before_training_cutoff(monkeypatch):
    start = datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc)
    leaked = MarginTrainingRow(
        event_id="future-row",
        event_start_time=(start + timedelta(minutes=1)).isoformat(),
        feature_as_of=start.isoformat(),
        margin=1,
        features={"x": 1.0},
        source_manifest_sha256="sha",
    )
    monkeypatch.setattr(shadow, "load_replay_rows", lambda *_args, **_kwargs: [leaked])
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        shadow.run_ncaaf_forward_shadow(
            object(), event_id="e", event_start_time=start.isoformat(), home_team="A", away_team="B",
            home_spread=-3.5, season=2026,
        )
    assert exc.value.code == "SPREAD_FORWARD_TARGET_NOT_AFTER_TRAINING_CUTOFF"


def test_forward_shadow_requires_matching_season():
    start = datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        shadow.run_ncaaf_forward_shadow(
            object(), event_id="e", event_start_time=start.isoformat(), home_team="A", away_team="B",
            home_spread=-3.5, season=2025,
        )
    assert exc.value.code == "SPREAD_FORWARD_SEASON_MISMATCH"

    with pytest.raises(SpreadChallengerUnavailable) as exc:
        shadow.run_ncaaf_forward_shadow(
            object(), event_id="e", event_start_time=start.isoformat(), home_team="A", away_team="B",
            home_spread=-3.5, season=None,
        )
    assert exc.value.code == "SPREAD_FORWARD_SEASON_REQUIRED"
