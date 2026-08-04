"""
Tests for gate_engine/hit_probability.py
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from gate_engine.hit_probability import (
    compute,
    compute_batch,
    _bernoulli_hit_rate,
    _mlb_formula_v2,
    _poisson_model,
    _is_mlb_binary,
    _is_mlb_hits_prop,
    _is_counting_stat,
    _coerce_game_log,
    MODEL_MLB_FORMULA,
    MODEL_BERNOULLI,
    MODEL_POISSON,
    MODEL_CLAUDE,
    MODEL_NO_REGISTERED_MODEL,
    MODEL_NO_DATA,
    _POISSON_IDEAL_SAMPLE,
    HitProbResult,
)

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

class TestClassification:
    def test_mlb_binary_hits_at_0_5(self):
        assert _is_mlb_binary("MLB", "H", 0.5) is True

    def test_mlb_binary_hr_at_0_5(self):
        assert _is_mlb_binary("MLB", "HR", 0.5) is True

    def test_mlb_binary_not_at_1_5(self):
        # 1.5 is still binary range
        assert _is_mlb_binary("MLB", "H", 1.5) is True

    def test_mlb_binary_not_above_1_5(self):
        assert _is_mlb_binary("MLB", "H", 2.0) is False

    def test_mlb_binary_not_for_nba(self):
        assert _is_mlb_binary("NBA", "H", 0.5) is False

    # _is_mlb_hits_prop — only H/HITS eligible for mlb_formula_v2
    def test_mlb_hits_prop_h(self):
        assert _is_mlb_hits_prop("MLB", "H", 0.5) is True

    def test_mlb_hits_prop_hits(self):
        assert _is_mlb_hits_prop("MLB", "HITS", 0.5) is True

    def test_mlb_hits_prop_hr_false(self):
        assert _is_mlb_hits_prop("MLB", "HR", 0.5) is False

    def test_mlb_hits_prop_rbi_false(self):
        assert _is_mlb_hits_prop("MLB", "RBI", 0.5) is False

    def test_mlb_hits_prop_sb_false(self):
        assert _is_mlb_hits_prop("MLB", "SB", 0.5) is False

    def test_mlb_hits_prop_tb_false(self):
        assert _is_mlb_hits_prop("MLB", "TB", 1.5) is False

    def test_mlb_hits_prop_above_1_5_false(self):
        assert _is_mlb_hits_prop("MLB", "H", 2.0) is False

    def test_mlb_hits_prop_at_1_5_false(self):
        # 1.5 line means "at least 2 hits" — formula cannot handle this; use Bernoulli
        assert _is_mlb_hits_prop("MLB", "H", 1.5) is False

    def test_mlb_hits_prop_at_0_5_true(self):
        assert _is_mlb_hits_prop("MLB", "H", 0.5) is True

    def test_nba_pts_is_counting(self):
        assert _is_counting_stat("NBA", "PTS") is True

    def test_nba_pra_is_counting(self):
        assert _is_counting_stat("NBA", "PRA") is True

    def test_nba_combo_is_counting(self):
        assert _is_counting_stat("NBA", "PTS+REB+AST") is True

    def test_wnba_pts_is_counting(self):
        assert _is_counting_stat("WNBA", "PTS") is True

    def test_mlb_so_is_counting(self):
        assert _is_counting_stat("MLB", "SO") is True

    def test_nhl_goals_not_counting(self):
        # NHL not in the counting stat list → Claude fallback
        assert _is_counting_stat("NHL", "goals") is False

    def test_nfl_passing_yards_not_counting(self):
        assert _is_counting_stat("NFL", "passing_yards") is False


# ---------------------------------------------------------------------------
# Bernoulli model
# ---------------------------------------------------------------------------

class TestBernoulliHitRate:
    def test_empty_log_returns_none(self):
        result = _bernoulli_hit_rate([], 0.5, "MORE")
        assert result.hit_probability is None
        assert result.model_used == MODEL_NO_DATA

    def test_perfect_hit_rate(self):
        log = [1.0] * 10   # all games got a hit
        result = _bernoulli_hit_rate(log, 0.5, "MORE")
        assert result.hit_probability == pytest.approx(1.0)
        assert result.model_used == MODEL_BERNOULLI
        assert result.sample_size == 10

    def test_zero_hit_rate(self):
        log = [0.0] * 10
        result = _bernoulli_hit_rate(log, 0.5, "MORE")
        assert result.hit_probability == pytest.approx(0.0)

    def test_partial_hit_rate(self):
        log = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0]  # 6/10
        result = _bernoulli_hit_rate(log, 0.5, "MORE")
        assert result.hit_probability == pytest.approx(0.6)

    def test_less_direction(self):
        log = [0.0, 0.0, 0.0, 1.0, 1.0]  # 3/5 below 0.5 line
        result = _bernoulli_hit_rate(log, 0.5, "LESS")
        assert result.hit_probability == pytest.approx(0.6)

    def test_returns_4_decimal_places(self):
        log = [1.0] * 3 + [0.0] * 7   # 3/10
        result = _bernoulli_hit_rate(log, 0.5, "MORE")
        assert result.hit_probability == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Poisson model
# ---------------------------------------------------------------------------

class TestPoissonModel:
    def test_empty_log_returns_none(self):
        result = _poisson_model([], 27.5, "MORE")
        assert result.hit_probability is None
        assert result.model_used == MODEL_NO_DATA

    def test_lambda_equals_line_approx_50pct(self):
        # When λ ≈ line, P(X ≥ line) should be near 50%
        lam = 27.5
        log = [lam] * 20
        result = _poisson_model(log, lam, "MORE")
        # Not exactly 50% but should be in reasonable range
        assert 0.40 < result.hit_probability < 0.60

    def test_high_lambda_gives_high_prob(self):
        # λ=35 for a 27.5 line → high probability
        log = [35.0] * 10
        result = _poisson_model(log, 27.5, "MORE")
        assert result.hit_probability > 0.85

    def test_low_lambda_gives_low_prob(self):
        # λ=15 for a 27.5 line → low probability
        log = [15.0] * 10
        result = _poisson_model(log, 27.5, "MORE")
        assert result.hit_probability < 0.15

    def test_less_direction(self):
        # λ=15, LESS line=27.5 → high probability
        log = [15.0] * 10
        result = _poisson_model(log, 27.5, "LESS")
        assert result.hit_probability > 0.85

    def test_probability_clamped_to_0_1(self):
        log = [100.0] * 5
        result = _poisson_model(log, 27.5, "MORE")
        assert 0.0 <= result.hit_probability <= 1.0

    def test_lambda_recorded(self):
        log = [28.0, 30.0, 27.0]
        result = _poisson_model(log, 27.5, "MORE")
        assert result.lambda_used == pytest.approx(sum(log) / 3, abs=0.01)

    def test_sample_size_recorded(self):
        log = [28.0] * 7
        result = _poisson_model(log, 27.5, "MORE")
        assert result.sample_size == 7

    def test_integer_line_more(self):
        # Line=28.0, MORE means P(X ≥ 28)
        log = [30.0] * 10
        result = _poisson_model(log, 28.0, "MORE")
        assert result.hit_probability > 0.60


# ---------------------------------------------------------------------------
# compute() dispatch
# ---------------------------------------------------------------------------

class TestCompute:
    def test_mlb_binary_dispatches_formula_v2(self):
        leg = {"sport": "MLB", "stat_key": "H", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [1.0, 0.0, 1.0, 1.0, 1.0])
        assert result.model_used == MODEL_MLB_FORMULA

    def test_nba_pts_dispatches_poisson(self):
        leg = {"sport": "NBA", "stat_key": "PTS", "line_value": 27.5, "side": "MORE"}
        result = compute(leg, [28.0, 30.0, 25.0, 32.0, 27.0])
        assert MODEL_POISSON[:7] in result.model_used

    def test_wnba_pra_dispatches_poisson(self):
        leg = {"sport": "WNBA", "stat_key": "PRA", "line_value": 20.5, "side": "MORE"}
        result = compute(leg, [18.0, 22.0, 19.0, 24.0, 21.0])
        assert MODEL_POISSON[:7] in result.model_used

    def test_nfl_fails_closed_no_registered_model(self):
        """NFL has no registered model — compute() returns NO_REGISTERED_MODEL, not Claude."""
        leg = {"sport": "NFL", "stat_key": "passing_yards", "line_value": 245.5,
               "side": "MORE", "player_name": "P. Mahomes"}
        result = compute(leg, [250.0, 230.0, 270.0, 240.0, 260.0])
        assert result.model_used == MODEL_NO_REGISTERED_MODEL
        assert result.hit_probability is None

    def test_no_game_log_returns_no_data(self):
        leg = {"sport": "NBA", "stat_key": "PTS", "line_value": 27.5, "side": "MORE"}
        result = compute(leg, [])
        assert result.hit_probability is None
        assert result.model_used == MODEL_NO_DATA

    def test_no_vig_prob_stored_in_result(self):
        leg = {"sport": "NBA", "stat_key": "PTS", "line_value": 20.0, "side": "MORE"}
        result = compute(leg, [22.0] * 5, no_vig_prob=0.58)
        assert result.market_calibration == pytest.approx(0.58)


# ---------------------------------------------------------------------------
# compute_batch()
# ---------------------------------------------------------------------------

class TestComputeBatch:
    def test_empty_legs(self):
        assert compute_batch([], {}) == []

    def test_batch_uses_enrichment_game_log(self):
        legs = [{"leg_id": "l1", "sport": "NBA", "stat_key": "PTS",
                 "line_value": 20.0, "side": "MORE"}]
        enrichment = {"l1": {"game_log": [22.0, 24.0, 20.0, 21.0, 23.0]}}
        results = compute_batch(legs, enrichment)
        assert len(results) == 1
        assert results[0]["leg_id"] == "l1"
        assert results[0]["hit_probability"] is not None
        assert MODEL_POISSON[:7] in results[0]["model_used"]

    def test_batch_no_game_log_returns_no_data(self):
        legs = [{"leg_id": "l1", "sport": "NBA", "stat_key": "PTS",
                 "line_value": 20.0, "side": "MORE"}]
        results = compute_batch(legs, {})
        assert results[0]["hit_probability"] is None
        assert results[0]["model_used"] == MODEL_NO_DATA

    def test_batch_multiple_legs(self):
        legs = [
            {"leg_id": "l1", "sport": "MLB", "stat_key": "H",
             "line_value": 0.5, "side": "MORE"},
            {"leg_id": "l2", "sport": "NBA", "stat_key": "REB",
             "line_value": 7.5, "side": "MORE"},
        ]
        enrichment = {
            "l1": {"game_log": [1.0, 0.0, 1.0, 1.0, 0.0]},
            "l2": {"game_log": [9.0, 7.0, 8.0, 10.0, 6.0]},
        }
        results = compute_batch(legs, enrichment)
        assert len(results) == 2
        mlb_r = next(r for r in results if r["leg_id"] == "l1")
        nba_r = next(r for r in results if r["leg_id"] == "l2")
        assert mlb_r["model_used"] == MODEL_MLB_FORMULA
        assert MODEL_POISSON in nba_r["model_used"]

    def test_no_vig_prob_from_enrichment(self):
        legs = [{"leg_id": "l1", "sport": "NBA", "stat_key": "PTS",
                 "line_value": 20.0, "side": "MORE"}]
        enrichment = {"l1": {"game_log": [22.0] * 5, "sharp_no_vig_prob": 0.61}}
        results = compute_batch(legs, enrichment)
        assert results[0]["market_calibration"] == pytest.approx(0.61)


# ---------------------------------------------------------------------------
# _coerce_game_log
# ---------------------------------------------------------------------------

class TestCoerceGameLog:
    def test_plain_floats_passthrough(self):
        result = _coerce_game_log([22.0, 24.0, 20.0], {})
        assert result == [22.0, 24.0, 20.0]

    def test_plain_ints_converted(self):
        result = _coerce_game_log([22, 24, 20], {})
        assert result == [22.0, 24.0, 20.0]

    def test_dict_log_extracts_pts(self):
        log = [{"PTS": 22, "REB": 5, "AST": 3},
               {"PTS": 28, "REB": 7, "AST": 4}]
        result = _coerce_game_log(log, {"stat_key": "PTS"})
        assert result == [22.0, 28.0]

    def test_dict_log_combo_stat(self):
        log = [{"PTS": 20, "REB": 8, "AST": 5},
               {"PTS": 25, "REB": 6, "AST": 4}]
        result = _coerce_game_log(log, {"stat_key": "PTS+REB+AST"})
        assert result == [33.0, 35.0]

    def test_empty_log(self):
        assert _coerce_game_log([], {}) == []

    def test_none_values_skipped(self):
        result = _coerce_game_log([22.0, None, 20.0], {})
        assert result == [22.0, 20.0]


# ---------------------------------------------------------------------------
# MLB formula v2 (_mlb_formula_v2)
# ---------------------------------------------------------------------------

class TestMlbFormulaV2:
    def test_model_name_is_mlb_formula_v2(self):
        result = _mlb_formula_v2([1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0],
                                  0.5, "MORE")
        assert result.model_used == MODEL_MLB_FORMULA

    def test_empty_log_returns_no_data(self):
        result = _mlb_formula_v2([], 0.5, "MORE")
        assert result.hit_probability is None
        assert result.model_used == MODEL_NO_DATA

    def test_probability_in_0_1(self):
        result = _mlb_formula_v2([1.0, 1.0, 0.0, 1.0, 0.0], 0.5, "MORE")
        assert result.hit_probability is not None
        assert 0.0 <= result.hit_probability <= 1.0

    def test_less_side_uses_p_zero_hits(self):
        # With high BA, P(LESS=0 hits) should be low
        result = _mlb_formula_v2([1.0] * 10, 0.5, "LESS")
        assert result.hit_probability < 0.5

    def test_enrichment_batting_average_used(self):
        # With explicit high BA supplied, probability should be high
        result = _mlb_formula_v2([1.0, 0.0], 0.5, "MORE",
                                  enrichment={"batting_average": 0.350})
        assert result.hit_probability > 0.70
        assert "BA derived" not in result.calibration_note

    def test_small_sample_warning_in_note(self):
        result = _mlb_formula_v2([1.0, 0.0, 1.0], 0.5, "MORE")
        assert "L3 only" in result.calibration_note or "L10 unavailable" in result.calibration_note

    def test_calibration_note_contains_ba(self):
        result = _mlb_formula_v2([1.0] * 10, 0.5, "MORE")
        assert "BA=" in result.calibration_note

    def test_dispatched_via_compute(self):
        leg = {"sport": "MLB", "stat_key": "H", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0])
        assert result.model_used == MODEL_MLB_FORMULA

    def test_h_at_1_5_uses_bernoulli_not_formula(self):
        """H 1.5 means ≥2 hits; formula always returns P(≥1 hit), so must use Bernoulli."""
        leg = {"sport": "MLB", "stat_key": "H", "line_value": 1.5, "side": "MORE"}
        result = compute(leg, [2.0, 1.0, 0.0, 3.0, 1.0])
        assert result.model_used == MODEL_BERNOULLI
        assert result.model_used != MODEL_MLB_FORMULA

    def test_h_at_1_5_threshold_respected(self):
        """H 1.5 MORE: only games with ≥2 hits count."""
        log = [2.0, 1.0, 0.0, 3.0, 1.0]   # 2 games have H≥2: indices 0, 3
        leg = {"sport": "MLB", "stat_key": "H", "line_value": 1.5, "side": "MORE"}
        result = compute(leg, log)
        assert result.hit_probability == pytest.approx(0.4)   # 2/5

    def test_h_at_1_5_less_threshold_respected(self):
        """H 1.5 LESS: games with H < 1.5, i.e. ≤1 hit."""
        log = [2.0, 1.0, 0.0, 3.0, 1.0]   # 3 games with H<1.5: indices 1, 2, 4
        leg = {"sport": "MLB", "stat_key": "H", "line_value": 1.5, "side": "LESS"}
        result = compute(leg, log)
        assert result.hit_probability == pytest.approx(0.6)   # 3/5


# ---------------------------------------------------------------------------
# MLB non-hit binary props — Bernoulli dispatch (HR, RBI, SB, TB, BB, R)
# ---------------------------------------------------------------------------

class TestMlbNonHitBinaryDispatch:
    """HR/RBI/SB/TB at ≤1.5 line must use Bernoulli (game-log), not mlb_formula_v2."""

    def test_hr_uses_bernoulli(self):
        leg = {"sport": "MLB", "stat_key": "HR", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [0.0, 1.0, 0.0, 0.0, 1.0])
        assert result.model_used == MODEL_BERNOULLI

    def test_rbi_uses_bernoulli(self):
        leg = {"sport": "MLB", "stat_key": "RBI", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [1.0, 0.0, 2.0, 0.0, 1.0])
        assert result.model_used == MODEL_BERNOULLI

    def test_sb_uses_bernoulli(self):
        leg = {"sport": "MLB", "stat_key": "SB", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [0.0, 0.0, 1.0, 0.0, 1.0])
        assert result.model_used == MODEL_BERNOULLI

    def test_tb_at_1_5_uses_bernoulli(self):
        # TB 1.5 — line honors threshold (≥2 TB means at least a double/HR)
        leg = {"sport": "MLB", "stat_key": "TB", "line_value": 1.5, "side": "MORE"}
        result = compute(leg, [2.0, 1.0, 3.0, 0.0, 4.0])
        assert result.model_used == MODEL_BERNOULLI

    def test_tb_line_threshold_respected(self):
        # 3 of 5 games have TB≥2 (above 1.5 line)
        log = [2.0, 1.0, 3.0, 0.0, 4.0]
        leg = {"sport": "MLB", "stat_key": "TB", "line_value": 1.5, "side": "MORE"}
        result = compute(leg, log)
        assert result.hit_probability == pytest.approx(0.6)   # 3/5

    def test_bb_uses_bernoulli(self):
        leg = {"sport": "MLB", "stat_key": "BB", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [1.0, 0.0, 0.0, 1.0, 0.0])
        assert result.model_used == MODEL_BERNOULLI

    def test_hr_does_not_use_mlb_formula(self):
        leg = {"sport": "MLB", "stat_key": "HR", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [0.0, 1.0, 0.0, 0.0, 1.0])
        assert result.model_used != MODEL_MLB_FORMULA

    def test_tb_above_1_5_routes_to_counting_or_no_model(self):
        # TB 2.5 is above binary range — no longer _is_mlb_binary
        leg = {"sport": "MLB", "stat_key": "TB", "line_value": 2.5, "side": "MORE"}
        result = compute(leg, [2.0, 3.0, 1.0, 4.0, 2.0])
        # TB is in _MLB_COUNTING_STATS, so should hit Poisson; never Claude
        assert MODEL_POISSON in result.model_used or result.model_used == MODEL_NO_REGISTERED_MODEL

    def test_1b_at_0_5_uses_bernoulli_not_formula(self):
        """1B props are NOT eligible for mlb_formula_v2 — must route to Bernoulli."""
        leg = {"sport": "MLB", "stat_key": "1B", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [0.0, 1.0, 1.0, 0.0, 1.0])
        assert result.model_used == MODEL_BERNOULLI
        assert result.model_used != MODEL_MLB_FORMULA

    def test_2b_at_0_5_uses_bernoulli_not_formula(self):
        """2B props are NOT eligible for mlb_formula_v2."""
        leg = {"sport": "MLB", "stat_key": "2B", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [0.0, 0.0, 1.0, 0.0, 0.0])
        assert result.model_used == MODEL_BERNOULLI

    def test_3b_at_0_5_uses_bernoulli_not_formula(self):
        """3B props are NOT eligible for mlb_formula_v2."""
        leg = {"sport": "MLB", "stat_key": "3B", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [0.0, 0.0, 0.0, 1.0, 0.0])
        assert result.model_used == MODEL_BERNOULLI

    def test_2b_line_threshold_respected(self):
        """Bernoulli counts games where value ≥ line — must honor the actual line."""
        # 2 of 5 games have 2B≥0.5 (i.e. ≥1 double)
        log = [0.0, 1.0, 0.0, 0.0, 2.0]
        leg = {"sport": "MLB", "stat_key": "2B", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, log)
        assert result.hit_probability == pytest.approx(0.4)   # 2/5


# ---------------------------------------------------------------------------
# Poisson model — over/under boundary, sample warnings
# ---------------------------------------------------------------------------

class TestPoissonExtended:
    def test_model_name_is_poisson_l10(self):
        log = [27.0] * 10
        result = _poisson_model(log, 25.5, "MORE")
        assert result.model_used == MODEL_POISSON

    def test_small_sample_calibration_warning(self):
        log = [25.0] * 6   # below _POISSON_IDEAL_SAMPLE=10
        result = _poisson_model(log, 25.5, "MORE")
        assert str(6) in result.calibration_note
        assert "below" in result.calibration_note

    def test_full_sample_no_warning(self):
        log = [25.0] * 10   # exactly at ideal
        result = _poisson_model(log, 25.5, "MORE")
        assert "below" not in result.calibration_note

    def test_over_threshold_boundary(self):
        # Line=25.5, MORE → P(X≥26); λ=30 gives ~0.79
        log = [30.0] * 10
        result = _poisson_model(log, 25.5, "MORE")
        assert result.hit_probability > 0.70

    def test_under_threshold_boundary(self):
        # Line=25.5, LESS → P(X≤25)
        log = [15.0] * 10
        result = _poisson_model(log, 25.5, "LESS")
        assert result.hit_probability > 0.80


# ---------------------------------------------------------------------------
# Claude fallback — missing game_log, UNRESOLVABLE
# ---------------------------------------------------------------------------

class TestNoRegisteredModel:
    def test_no_game_log_returns_no_data_not_no_registered_model(self):
        """compute() returns MODEL_NO_DATA when game_log is empty (short-circuits before dispatch)."""
        leg = {"sport": "NFL", "stat_key": "passing_yards", "line_value": 245.5,
               "side": "MORE", "player_name": "P. Mahomes"}
        result = compute(leg, [])
        assert result.hit_probability is None
        assert result.model_used == MODEL_NO_DATA

    def test_nhl_fails_closed_no_registered_model(self):
        """NHL/NHL with game_log → NO_REGISTERED_MODEL, never Claude fallback."""
        leg = {"sport": "NHL", "stat_key": "goals", "line_value": 0.5,
               "side": "MORE", "player_name": "A. Matthews"}
        result = compute(leg, [1.0, 0.0, 0.0, 1.0, 1.0])
        assert result.model_used == MODEL_NO_REGISTERED_MODEL
        assert result.hit_probability is None

    def test_no_registered_model_carries_market_calibration(self):
        """market_calibration still populated when no model is registered."""
        leg = {"sport": "NFL", "stat_key": "passing_yards", "line_value": 245.5,
               "side": "MORE", "player_name": "P. Mahomes"}
        result = compute(leg, [250.0, 230.0], no_vig_prob=0.55)
        assert result.model_used == MODEL_NO_REGISTERED_MODEL
        assert result.market_calibration == pytest.approx(0.55)

    def test_null_on_unresolvable_leg_no_log(self):
        """UNRESOLVABLE leg with no game_log → null probability."""
        leg = {"sport": "NBA", "stat_key": "PTS", "line_value": 25.5, "side": "MORE"}
        result = compute(leg, [])
        assert result.hit_probability is None
        assert result.model_used == MODEL_NO_DATA

    def test_null_on_mlb_no_log(self):
        """MLB hit prop with no game_log → null via _mlb_formula_v2 no_data path."""
        leg = {"sport": "MLB", "stat_key": "H", "line_value": 0.5, "side": "MORE"}
        result = compute(leg, [])
        assert result.hit_probability is None
        assert result.model_used == MODEL_NO_DATA
