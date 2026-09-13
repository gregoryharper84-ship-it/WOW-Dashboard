from datetime import datetime, timezone

import pytest

from nfl_event_features_p3 import (
    PregameContext,
    build_early_season_features,
    continuity_points,
    prior_weight_for_week,
    situational_points,
)


def _ctx(**kwargs):
    base = dict(
        kickoff_at=datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc),
        as_of=datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc),
        week=2,
    )
    base.update(kwargs)
    return PregameContext(**base)


def test_prior_decay_schedule_is_early_season_heavy():
    assert prior_weight_for_week(1) == 1.0
    assert prior_weight_for_week(2) == 0.85
    assert prior_weight_for_week(3) == 0.70
    assert prior_weight_for_week(6) == 0.25
    assert prior_weight_for_week(10) == 0.15


def test_week2_blends_2025_prior_with_2026_observation():
    row = build_early_season_features(
        week=2,
        prior_season={"games": 17, "off_epa_pp": 0.10, "def_epa_pp": -0.03, "success_rate": 0.46, "point_diff_pg": 4.0},
        current_season={"games": 1, "off_epa_pp": 0.30, "def_epa_pp": -0.10, "success_rate": 0.55, "point_diff_pg": 14.0},
        league_means={"off_epa_pp": 0.0, "def_epa_pp": 0.0, "success_rate": 0.44, "point_diff_pg": 0.0},
        context=_ctx(),
    )
    assert row["prior_weight"] == 0.85
    assert row["current_weight"] == pytest.approx(0.15)
    assert row["current_games"] == 1
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_continuity_and_situational_features_are_bounded():
    ctx = _ctx(
        home_qb_continuity=1.0,
        away_qb_continuity=-1.0,
        home_coaching_continuity=1.0,
        away_coaching_continuity=-1.0,
        home_roster_continuity=1.0,
        away_roster_continuity=-1.0,
        situational_home_signal=1.0,
    )
    assert continuity_points(ctx) <= 1.5
    assert situational_points(ctx) == 1.0


def test_market_movement_is_evidence_not_probability_output():
    row = build_early_season_features(
        week=2,
        prior_season={"games": 17, "off_epa_pp": 0.02, "def_epa_pp": 0.01, "success_rate": 0.44, "point_diff_pg": 0.0},
        current_season={"games": 1, "off_epa_pp": 0.04, "def_epa_pp": 0.00, "success_rate": 0.45, "point_diff_pg": 3.0},
        league_means={"off_epa_pp": 0.0, "def_epa_pp": 0.0, "success_rate": 0.44, "point_diff_pg": 0.0},
        context=_ctx(market_home_prob_open=0.52, market_home_prob_current=0.56),
    )
    assert row["market_move_points"] == pytest.approx(0.4)
    assert "model_probability" not in row
    assert "calibrated_probability" not in row


def test_post_kickoff_context_fails_closed():
    with pytest.raises(ValueError, match="PREGAME_CUTOFF_VIOLATION"):
        build_early_season_features(
            week=2,
            prior_season={"games": 17},
            current_season={"games": 1},
            league_means={},
            context=PregameContext(
                kickoff_at=datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc),
                as_of=datetime(2026, 9, 13, 18, 1, tzinfo=timezone.utc),
                week=2,
            ),
        )
