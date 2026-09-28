from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mlb_event_specialist_v16 import LineupAdjustment, WeatherContext
from v17 import mlb_run_line_shadow as shadow


class _Query:
    def __init__(self, rows): self.rows = rows
    def select(self, *_args, **_kwargs): return self
    def eq(self, *_args, **_kwargs): return self
    def limit(self, *_args, **_kwargs): return self
    def execute(self): return SimpleNamespace(data=self.rows)


class _DB:
    score_status = "SHADOW_SCORED_PREGAME"
    def table(self, name):
        if name == "wow_mlb_forward_score_snapshots":
            return _Query([{ "score_snapshot_id": "s1", "shadow_event_id": "e1", "distribution_id": "d1", "home_mu": 4.7, "away_mu": 4.1, "score_status": self.score_status, "probability_publishable": False, "can_execute": False }])
        if name == "wow_mlb_v2b_distribution_state":
            return _Query([{ "distribution_id": "d1", "model_version": "MLB_DIST", "home_alpha_total": .3, "away_alpha_total": .35, "extra_inning_home_win_probability": .5, "extra_inning_training_games": 315, "training_end": "2025-08-09", "research_only": True, "probability_publishable": False, "can_execute": False }])
        raise AssertionError(name)


class _PendingDB(_DB):
    score_status = "SHADOW_SCORED_LINEUP_PENDING"


def test_final_run_line_sample_scoring_supports_half_and_whole_lines():
    half = shadow.score_final_run_line_samples([5, 4, 3, 7], [3, 4, 5, 6], home_run_line=-1.5)
    assert half == {"p_cover": .25, "p_push": 0.0, "p_not_cover": .75, "distribution_sample_n": 4}
    whole = shadow.score_final_run_line_samples([5, 4, 3, 7], [3, 4, 5, 6], home_run_line=-1.0)
    assert whole == {"p_cover": .25, "p_push": .25, "p_not_cover": .5, "distribution_sample_n": 4}


def test_preflight_reports_research_ready_for_real_pregame_shadow_status():
    result = shadow.run_mlb_run_line_shadow_preflight(_DB(), score_snapshot_id="s1", home_run_line=-1.5)
    assert result["status"] == "EXPERIMENT_CREATED"
    assert result["code"] == "MLB_RUN_LINE_FORWARD_SHADOW_READY"
    assert result["score_snapshot_status"] == "SHADOW_SCORED_PREGAME"
    assert result["extra_inning_margin_train_rows"] == 207
    assert len(result["extra_inning_margin_artifact_hash"]) == 64
    assert result["moneyline_probability_used"] is False
    assert result["market_probability_substitution_used"] is False
    assert result["run_line_used_as_feature"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_lineup_pending_snapshot_fails_closed():
    with pytest.raises(shadow.MLBRunLineShadowUnavailable) as exc:
        shadow.run_mlb_run_line_shadow_preflight(_PendingDB(), score_snapshot_id="s1", home_run_line=-1.5)
    assert exc.value.code == "MLB_RUN_LINE_SCORE_SNAPSHOT_NOT_PREGAME_SCORED"


def test_frozen_artifact_matches_reviewed_2024_histograms():
    artifact = shadow.frozen_2024_extra_inning_margin_artifact()
    assert artifact.train_rows == 207
    assert artifact.home_histogram == ((1, 98), (2, 6), (4, 1))
    assert artifact.away_histogram == ((1, 56), (2, 23), (3, 7), (4, 7), (5, 5), (6, 3), (7, 1))
    assert sum(c for _, c in artifact.home_histogram) == 105
    assert sum(c for _, c in artifact.away_histogram) == 102


def test_forward_shadow_resolves_only_tied_samples_and_preserves_governance(monkeypatch):
    lineup = {"raw_body": "{}", "home_batting_order": list(range(1, 10)), "away_batting_order": list(range(11, 20))}
    evidence = {
        "score": {"score_snapshot_id": "s1", "distribution_id": "d1", "home_mu": 4.5, "away_mu": 4.0},
        "event": {"official_date": "2026-09-26", "home_probable_pitcher_id": 100, "away_probable_pitcher_id": 200},
        "lineup": lineup,
        "features": {"HOME": {"feature_names": ["x"], "feature_vector": [1.0]}, "AWAY": {"feature_names": ["x"], "feature_vector": [1.0]}},
        "distribution": {"distribution_id": "d1", "model_version": "MLB_DIST", "home_alpha_total": .3, "away_alpha_total": .35, "extra_inning_home_win_probability": .5, "extra_inning_training_games": 315, "can_execute": False},
    }
    monkeypatch.setattr(shadow, "_score_snapshot_identity", lambda *_args, **_kwargs: {"score_snapshot_id": "s1", "shadow_event_id": "e1", "distribution_id": "d1", "home_mu": 4.5, "away_mu": 4.0, "score_status": "SHADOW_SCORED_PREGAME", "can_execute": False})
    monkeypatch.setattr(shadow, "_load_evidence", lambda *_args, **_kwargs: evidence)
    monkeypatch.setattr(shadow, "_parse_feed", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(shadow, "_starter_hand", lambda *_args, **_kwargs: "R")
    monkeypatch.setattr(shadow, "_lineup_adjustment", lambda *_args, **_kwargs: LineupAdjustment(1.0, 9, .750, .750, ()))
    monkeypatch.setattr(shadow, "_weather_context", lambda *_args, **_kwargs: WeatherContext(1.0, 0.01, 70.0, 0.0, None, "Clear", "Open"))
    monkeypatch.setattr(shadow, "_feature_map", lambda *_args, **_kwargs: {"x": 1.0})
    monkeypatch.setattr(shadow, "_simulate_nine_inning_samples", lambda **_kwargs: (np.asarray([4, 5, 2, 6]), np.asarray([4, 3, 4, 6])))

    result = shadow.run_mlb_run_line_forward_shadow(
        object(), score_snapshot_id="s1", home_run_line=-1.5,
        simulation_count=50_000, stats_fetcher=lambda *_args, **_kwargs: {},
    )
    assert result["status"] == "EXPERIMENT_CREATED"
    assert result["code"] == "MLB_RUN_LINE_FORWARD_SHADOW_COMPLETE"
    assert result["sport"] == "MLB"
    assert result["score_snapshot_status"] == "SHADOW_SCORED_PREGAME"
    assert result["distribution_sample_n"] == 4
    assert abs(result["p_cover"] + result["p_push"] + result["p_not_cover"] - 1.0) < 1e-12
    assert result["tie_after_9_probability"] == 2 / 50_000
    assert result["moneyline_probability_used"] is False
    assert result["market_probability_substitution_used"] is False
    assert result["run_line_used_as_feature"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_nine_inning_sampler_is_deterministic_and_uses_existing_failure_mixture():
    lineup = LineupAdjustment(1.0, 9, .750, .750, ())
    weather = WeatherContext(1.0, 0.02, 70.0, 5.0, None, "Clear", "Open")
    features = {"opp_starter_era": 4.0, "opp_starter_bb_rate": .09, "opp_starter_prior_starts": 10.0, "opp_starter_pitches_last3": 250.0, "opp_bp_era": 4.0, "opp_bp_bb_rate": .09, "opp_bp_pitches_3d": 180.0, "opp_errors_pg": .55}
    a = shadow._simulate_nine_inning_samples(home_mu=4.4, away_mu=4.1, home_alpha=.31, away_alpha=.32, lineup_home=lineup, lineup_away=lineup, weather=weather, home_features=features, away_features=features, seed=12345, simulation_count=50_000)
    b = shadow._simulate_nine_inning_samples(home_mu=4.4, away_mu=4.1, home_alpha=.31, away_alpha=.32, lineup_home=lineup, lineup_away=lineup, weather=weather, home_features=features, away_features=features, seed=12345, simulation_count=50_000)
    assert np.array_equal(a[0], b[0])
    assert np.array_equal(a[1], b[1])
