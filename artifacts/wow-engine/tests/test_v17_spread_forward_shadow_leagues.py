from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from basketball_team_event_specialist import TrainingGame, build_pregame_features
from v17 import spread_forward_shadow_leagues as shadow
from v17.spread_margin_challenger import SpreadChallengerUnavailable


def _wnba_games():
    start = date(2026, 5, 1)
    rows = []
    for idx in range(5):
        d = start + timedelta(days=idx * 2)
        rows.append(TrainingGame(f"a{idx}", "WNBA", 2026, d, "espn-A", "espn-C", 80 + idx, 70,))
        rows.append(TrainingGame(f"b{idx}", "WNBA", 2026, d + timedelta(days=1), "espn-D", "espn-B", 75, 78 + idx,))
    target_date = start + timedelta(days=12)
    target = TrainingGame("target", "WNBA", 2026, target_date, "espn-A", "espn-B", 88, 84)
    return rows, target


def test_wnba_forward_features_match_historical_builder_for_same_target():
    prior, target = _wnba_games()
    historical = build_pregame_features([*prior, target], "WNBA")
    target_row = next(row for row in historical if row.game_id == "target")
    expected = dict(zip(
        (
            "home_win_rate_prior", "away_win_rate_prior", "home_point_diff_prior", "away_point_diff_prior",
            "home_rest_days_capped", "away_rest_days_capped", "home_back_to_back", "away_back_to_back",
        ),
        target_row.values,
    ))
    actual, audit = shadow.build_wnba_forward_features(
        prior,
        target_date=target.game_date,
        home_team_id=target.home_team_id,
        away_team_id=target.away_team_id,
    )
    assert actual == expected
    assert audit["home_prior_games"] == 5
    assert audit["away_prior_games"] == 5
    assert audit["market_features_used"] is False
    assert audit["moneyline_probability_used"] is False
    assert audit["spread_line_used_as_feature"] is False


def test_wnba_forward_shadow_requires_governed_espn_identity():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        shadow.run_wnba_forward_shadow(
            object(), event_id="401", event_start_time="2026-09-27T19:00:00+00:00",
            home_team_id="11", away_team_id="17", home_spread=-3.5,
        )
    assert exc.value.code == "WNBA_SPREAD_FORWARD_ESPN_IDENTITY_REQUIRED"


def test_nfl_forward_shadow_uses_canonical_live_features_and_exact_line(monkeypatch):
    monkeypatch.setattr(shadow, "resolve_nfl_team_event_evidence", lambda request, db: {
        "ok": True,
        "canonical_event_id": "2026_04_BAL_DAL",
        "canonical_home_team": "DAL",
        "canonical_away_team": "BAL",
        "identity_resolution": "EXACT_CANONICAL_EVENT_ID",
        "canonical_source_snapshot_id": "sched-1",
        "evidence": {"season": 2026, "week": 4, "gameday": "2026-09-27", "schedule_content_sha256": "a" * 64},
    })
    monkeypatch.setattr(shadow, "_prediction_feature_row", lambda **kwargs: {
        "features": {"a": 1.0, "b": 2.0}, "row_inputs_hash": "hash", "feature_cutoff_date": "2026-09-26",
    })
    artifact = SimpleNamespace(
        feature_names=("a", "b"), model_family="NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version="NFL_SPREAD_MARGIN_TEAM_STATE_V1", training_dataset_hash="dataset",
        train_rows=600, calibration_rows=200, test_rows=200,
    )
    monkeypatch.setattr(shadow, "_fit_forward_artifact", lambda db, sport, target_start: (
        artifact, {"margin_mae": 10.0, "cover_brier": 0.22, "cover_log_loss": 0.66, "cover_ece": 0.05},
        shadow._dt("2026-09-21T23:59:59+00:00"),
    ))
    seen = {}
    def fake_score(artifact_arg, features, *, home_spread):
        seen["features"] = features
        seen["line"] = home_spread
        return {
            "predicted_home_margin_center": 2.2, "p_cover": 0.55, "p_push": 0.0,
            "p_not_cover": 0.45, "p_cover_given_no_push": 0.55,
            "research_lower_bound_cover": 0.50, "distribution_sample_n": 200,
        }
    monkeypatch.setattr(shadow, "score_home_spread", fake_score)

    result = shadow.run_nfl_forward_shadow(
        object(), event_id="2026_04_BAL_DAL", event_start_time="2026-09-27T20:25:00+00:00",
        home_team="Dallas Cowboys", away_team="Baltimore Ravens", home_spread=-3.5,
    )
    assert result["code"] == "NFL_SPREAD_FORWARD_SHADOW_COMPLETE"
    assert seen == {"features": {"a": 1.0, "b": 2.0}, "line": -3.5}
    assert result["market_features_used"] is False
    assert result["moneyline_probability_used"] is False
    assert result["spread_line_used_as_feature"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert abs(result["p_cover"] + result["p_push"] + result["p_not_cover"] - 1.0) < 1e-12
