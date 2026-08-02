"""
test_distribution_models.py
Tests for:
  gate_engine/mlb/hit_probability_model.py
  gate_engine/wnba/points_model.py
  gate_engine/wnba/assists_model.py
  gate_engine/wnba/threes_model.py
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT
"""
import pytest
from gate_engine.mlb.hit_probability_model import (
    compute_hit_probability,
    score_zero_point_five_hits,
)
from gate_engine.wnba.points_model import compute_points_probability
from gate_engine.wnba.assists_model import compute_assists_probability
from gate_engine.wnba.threes_model import compute_threes_probability


# ===========================================================================
# MLB Hit Probability Model
# ===========================================================================

class TestMLBHitProbabilityModel:

    def test_probability_is_between_0_and_1(self):
        r = compute_hit_probability(batting_average=0.280, batting_order=3)
        assert 0 < r["p_at_least_one_hit"] < 1

    def test_higher_ba_gives_higher_probability(self):
        r_low  = compute_hit_probability(batting_average=0.220, batting_order=3)
        r_high = compute_hit_probability(batting_average=0.320, batting_order=3)
        assert r_high["p_at_least_one_hit"] > r_low["p_at_least_one_hit"]

    def test_more_pa_gives_higher_probability(self):
        r_few  = compute_hit_probability(batting_average=0.280, projected_pa=2.0)
        r_many = compute_hit_probability(batting_average=0.280, projected_pa=5.0)
        assert r_many["p_at_least_one_hit"] > r_few["p_at_least_one_hit"]

    def test_leadoff_slot_more_pa_than_9_hole(self):
        r1 = compute_hit_probability(batting_average=0.280, batting_order=1)
        r9 = compute_hit_probability(batting_average=0.280, batting_order=9)
        assert r1["p_at_least_one_hit"] > r9["p_at_least_one_hit"]

    def test_platoon_advantage_righty_vs_lhp(self):
        r_no_adv = compute_hit_probability(batting_average=0.280, batter_hand="R", starter_hand="R")
        r_adv    = compute_hit_probability(batting_average=0.280, batter_hand="R", starter_hand="L")
        assert r_adv["p_at_least_one_hit"] > r_no_adv["p_at_least_one_hit"]

    def test_pinch_hit_risk_caps_pa_at_1(self):
        r = compute_hit_probability(batting_average=0.280, batting_order=3, pinch_hit_risk=True)
        assert r["n_projected_pa"] <= 1.0

    def test_explicit_pa_and_prob_override(self):
        r = compute_hit_probability(projected_pa=4.0, per_pa_hit_prob=0.30)
        expected = 1.0 - (0.70 ** 4.0)
        assert abs(r["p_at_least_one_hit"] - expected) < 0.001

    def test_can_execute_always_false(self):
        r = compute_hit_probability()
        assert r["can_execute"] is False

    def test_no_inputs_uses_league_average_fallback(self):
        r = compute_hit_probability()
        assert r["data_quality"] == "MINIMAL"
        assert r["data_quality_warning"] is not None
        assert 0 < r["p_at_least_one_hit"] < 1

    def test_score_zero_point_five_hits_returns_lb(self):
        r = score_zero_point_five_hits(batting_average=0.280, batting_order=3)
        assert "calibrated_lower_bound" in r
        assert r["calibrated_lower_bound"] <= r["raw_probability"]

    def test_lb_equals_raw_minus_floor(self):
        r = score_zero_point_five_hits(
            batting_average=0.280, batting_order=3, calibration_floor=0.05
        )
        assert abs(r["calibrated_lower_bound"] - (r["raw_probability"] - 0.05)) < 0.001

    def test_model_name_set(self):
        r = score_zero_point_five_hits(batting_average=0.280)
        assert r["model_name"] == "mlb_binomial_hit_v1"

    def test_regression_freeman_over_0_5_hits(self):
        """Freeman .300 avg, cleanup slot, mild RHP advantage."""
        r = score_zero_point_five_hits(
            batting_average=0.305, batting_order=3,
            batter_hand="L", starter_hand="R",
            park_factor=1.03,
        )
        # Cleanup slot, .305 BA, platoon advantage → should be meaningful probability
        assert r["raw_probability"] > 0.65
        assert r["calibrated_lower_bound"] > 0.55
        assert r["data_quality"] in ("FULL", "PARTIAL")


# ===========================================================================
# WNBA Points Model
# ===========================================================================

class TestWNBAPointsModel:

    def test_probability_between_0_and_1(self):
        r = compute_points_probability(line=14.5, mean_points=18.0, std_points=5.0)
        assert 0 < r["raw_probability"] < 1

    def test_higher_mean_higher_probability(self):
        r_low  = compute_points_probability(line=14.5, mean_points=12.0, std_points=5.0)
        r_high = compute_points_probability(line=14.5, mean_points=22.0, std_points=5.0)
        assert r_high["raw_probability"] > r_low["raw_probability"]

    def test_line_above_mean_gives_low_probability(self):
        r = compute_points_probability(line=25.0, mean_points=14.0, std_points=5.0)
        assert r["raw_probability"] < 0.20

    def test_line_below_mean_gives_high_probability(self):
        r = compute_points_probability(line=8.5, mean_points=20.0, std_points=4.0)
        assert r["raw_probability"] > 0.80

    def test_blowout_risk_reduces_probability(self):
        r_none  = compute_points_probability(line=14.5, mean_points=18.0, std_points=5.0, blowout_risk=0.0)
        r_high  = compute_points_probability(line=14.5, mean_points=18.0, std_points=5.0, blowout_risk=0.8)
        assert r_high["raw_probability"] < r_none["raw_probability"]

    def test_bad_opponent_defense_boosts_probability(self):
        r_good = compute_points_probability(line=14.5, mean_points=18.0, std_points=5.0, opponent_def_rank=1)
        r_bad  = compute_points_probability(line=14.5, mean_points=18.0, std_points=5.0, opponent_def_rank=12)
        assert r_bad["raw_probability"] > r_good["raw_probability"]

    def test_computed_from_game_values(self):
        r = compute_points_probability(line=14.5, game_values=[18, 22, 15, 12, 20, 17, 19, 24])
        assert 0 < r["raw_probability"] < 1
        assert "computed_from" in r["data_source"]

    def test_lb_less_than_or_equal_raw(self):
        r = compute_points_probability(line=14.5, mean_points=18.0, std_points=5.0)
        assert r["calibrated_lower_bound"] <= r["raw_probability"]

    def test_can_execute_always_false(self):
        assert compute_points_probability(line=14.5)["can_execute"] is False

    def test_league_average_fallback_warns(self):
        r = compute_points_probability(line=14.5)
        assert r["data_quality"] == "MINIMAL"
        assert r["data_quality_warning"] is not None

    def test_regression_plum_14_5_points(self):
        """Plum ~24 ppg mean, line 14.5 — should give high raw probability."""
        r = compute_points_probability(
            line=14.5, mean_points=23.8, std_points=6.5,
            opponent_def_rank=9,
        )
        assert r["raw_probability"] > 0.80
        assert r["calibrated_lower_bound"] > 0.70

    def test_direction_less(self):
        r_more = compute_points_probability(line=14.5, mean_points=18.0, std_points=5.0, direction="MORE")
        r_less = compute_points_probability(line=14.5, mean_points=18.0, std_points=5.0, direction="LESS")
        assert abs(r_more["raw_probability"] + r_less["raw_probability"] - 1.0) < 0.02


# ===========================================================================
# WNBA Assists Model
# ===========================================================================

class TestWNBAAssistsModel:

    def test_probability_between_0_and_1(self):
        r = compute_assists_probability(line=4.5, lambda_assists=5.0)
        assert 0 < r["raw_probability"] < 1

    def test_higher_lambda_higher_probability(self):
        r_low  = compute_assists_probability(line=4.5, lambda_assists=3.0)
        r_high = compute_assists_probability(line=4.5, lambda_assists=7.0)
        assert r_high["raw_probability"] > r_low["raw_probability"]

    def test_primary_teammate_out_reduces_lambda(self):
        r_avail = compute_assists_probability(line=3.5, lambda_assists=4.0, primary_teammate_avail=True)
        r_out   = compute_assists_probability(line=3.5, lambda_assists=4.0, primary_teammate_avail=False)
        assert r_out["lambda_used"] < r_avail["lambda_used"]

    def test_turnover_risk_reduces_lambda(self):
        r_low  = compute_assists_probability(line=3.5, lambda_assists=5.0, turnover_risk=0.0)
        r_high = compute_assists_probability(line=3.5, lambda_assists=5.0, turnover_risk=0.5)
        assert r_high["lambda_used"] < r_low["lambda_used"]

    def test_lb_less_than_or_equal_raw(self):
        r = compute_assists_probability(line=4.5, lambda_assists=5.0)
        assert r["calibrated_lower_bound"] <= r["raw_probability"]

    def test_can_execute_always_false(self):
        assert compute_assists_probability(line=4.5)["can_execute"] is False

    def test_league_average_fallback_warns(self):
        r = compute_assists_probability(line=4.5)
        assert r["data_quality"] == "MINIMAL"

    def test_computed_from_game_values(self):
        r = compute_assists_probability(line=3.5, game_values=[4, 5, 3, 6, 4, 5, 7, 3])
        assert "computed_from" in r["data_source"]
        assert r["lambda_used"] > 0

    def test_regression_plum_4_5_assists(self):
        """Plum averages ~5-6 assists; line 4.5 should be achievable probability."""
        r = compute_assists_probability(
            line=4.5, lambda_assists=5.2,
            on_ball_role=True,
            primary_teammate_avail=True,
        )
        assert r["raw_probability"] > 0.40

    def test_direction_less(self):
        r_more = compute_assists_probability(line=4.5, lambda_assists=5.0, direction="MORE")
        r_less = compute_assists_probability(line=4.5, lambda_assists=5.0, direction="LESS")
        total  = r_more["raw_probability"] + r_less["raw_probability"]
        # Should approximately sum to 1 (Poisson is discrete; close but not exact)
        assert 0.90 <= total <= 1.05


# ===========================================================================
# WNBA Threes Model
# ===========================================================================

class TestWNBAThreesModel:

    def test_probability_between_0_and_1(self):
        r = compute_threes_probability(line=2.5, projected_attempts=6, three_point_pct=0.37)
        assert 0 < r["raw_probability"] < 1

    def test_over_0_5_should_be_high_for_shooter(self):
        # 6 attempts at 37% — P(≥1 make) should be very high
        r = compute_threes_probability(line=0.5, projected_attempts=6, three_point_pct=0.37)
        assert r["raw_probability"] > 0.85

    def test_over_2_5_harder_than_over_0_5(self):
        r05 = compute_threes_probability(line=0.5, projected_attempts=6, three_point_pct=0.37)
        r25 = compute_threes_probability(line=2.5, projected_attempts=6, three_point_pct=0.37)
        assert r05["raw_probability"] > r25["raw_probability"]

    def test_more_attempts_higher_probability(self):
        r_few  = compute_threes_probability(line=2.5, projected_attempts=4, three_point_pct=0.37)
        r_many = compute_threes_probability(line=2.5, projected_attempts=8, three_point_pct=0.37)
        assert r_many["raw_probability"] > r_few["raw_probability"]

    def test_lb_less_than_or_equal_raw(self):
        r = compute_threes_probability(line=2.5, projected_attempts=6, three_point_pct=0.37)
        assert r["calibrated_lower_bound"] <= r["raw_probability"]

    def test_high_variance_warning_always_present(self):
        r = compute_threes_probability(line=2.5, projected_attempts=6, three_point_pct=0.37)
        assert r["high_variance_warning"] is not None
        assert "high-variance" in r["high_variance_warning"].lower()

    def test_can_execute_always_false(self):
        assert compute_threes_probability(line=2.5)["can_execute"] is False

    def test_league_average_fallback_warns(self):
        r = compute_threes_probability(line=2.5)
        assert r["data_quality"] == "MINIMAL"

    def test_shot_quality_adj_increases_probability(self):
        r_base = compute_threes_probability(line=1.5, projected_attempts=5, three_point_pct=0.35)
        r_adj  = compute_threes_probability(line=1.5, projected_attempts=5, three_point_pct=0.35,
                                            shot_quality_adj=0.05)
        assert r_adj["raw_probability"] >= r_base["raw_probability"]

    def test_regression_mitchell_over_2_5_threes(self):
        """
        Kelsey Mitchell: mean 2.83 on line 2.5 is NOT a large margin per Linemaker analysis.
        Model should show moderate probability and the high_variance_warning.
        """
        r = compute_threes_probability(
            line=2.5,
            projected_attempts=7,   # she takes a lot
            three_point_pct=0.378,  # season 3P%
        )
        # P(≥3 makes from 7 attempts at 37.8%) — should be meaningful but not overwhelming
        assert 0.30 < r["raw_probability"] < 0.85
        assert r["high_variance_warning"] is not None
        # Calibrated lb should be notably lower than raw — this is the key point
        assert r["raw_probability"] - r["calibrated_lower_bound"] > 0.02

    def test_computed_from_game_values(self):
        r = compute_threes_probability(
            line=1.5,
            game_attempt_values=[6, 7, 5, 8, 6, 7, 5, 6],
            game_make_values=[2, 3, 2, 4, 2, 3, 2, 3],
        )
        assert any(s in r["n_source"] for s in ("computed_from", "mean_from", "estimated"))
        assert 0 < r["raw_probability"] < 1

    def test_zero_attempts_gives_zero_probability_over(self):
        r = compute_threes_probability(line=0.5, projected_attempts=0, three_point_pct=0.37)
        assert r["raw_probability"] == 0.0
