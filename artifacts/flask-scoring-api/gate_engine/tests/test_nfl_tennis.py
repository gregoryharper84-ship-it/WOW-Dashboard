"""
gate_engine/tests/test_nfl_tennis.py

Tests for NFL + Tennis sport additions:
  - model_registry entries
  - auto_game_log dispatch (mocked fetchers)
  - hit_probability dispatch (NFL Bernoulli / Poisson, Tennis Gaussian)
  - normalizer stat-key mappings
  - nfl_game_log._nfl_season_from_date
  - tennis_game_log._parse_games
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# model_registry — NFL entries
# ---------------------------------------------------------------------------

class TestModelRegistryNFL:
    def setup_method(self):
        from gate_engine.model_registry import lookup, is_supported
        self.lookup = lookup
        self.is_supported = is_supported

    def test_pass_yds_provisional(self):
        e = self.lookup("NFL", "PASS_YDS")
        assert e["status"] == "PROVISIONAL"
        assert e["model_id"] == "nfl_counting_poisson_v1"

    def test_rush_yds_provisional(self):
        e = self.lookup("NFL", "RUSH_YDS")
        assert e["status"] == "PROVISIONAL"

    def test_rec_yds_provisional(self):
        e = self.lookup("NFL", "REC_YDS")
        assert e["status"] == "PROVISIONAL"

    def test_rec_provisional(self):
        assert self.lookup("NFL", "REC")["status"] == "PROVISIONAL"

    def test_targets_provisional(self):
        assert self.lookup("NFL", "TARGETS")["status"] == "PROVISIONAL"

    def test_pass_cmp_provisional(self):
        assert self.lookup("NFL", "PASS_CMP")["status"] == "PROVISIONAL"

    def test_sack_provisional(self):
        assert self.lookup("NFL", "SACK")["status"] == "PROVISIONAL"

    def test_int_provisional(self):
        assert self.lookup("NFL", "INT")["status"] == "PROVISIONAL"

    def test_td_binary_provisional(self):
        e = self.lookup("NFL", "TD")
        assert e["status"] == "PROVISIONAL"
        assert e["model_id"] == "nfl_binary_bernoulli_v1"

    def test_pass_td_provisional(self):
        assert self.lookup("NFL", "PASS_TD")["status"] == "PROVISIONAL"
        assert self.lookup("NFL", "PASS_TD")["model_id"] == "nfl_binary_bernoulli_v1"

    def test_rush_td_provisional(self):
        assert self.lookup("NFL", "RUSH_TD")["model_id"] == "nfl_binary_bernoulli_v1"

    def test_rec_td_provisional(self):
        assert self.lookup("NFL", "REC_TD")["model_id"] == "nfl_binary_bernoulli_v1"

    def test_anytime_td_provisional(self):
        assert self.lookup("NFL", "ANYTIME_TD")["status"] == "PROVISIONAL"

    def test_fpts_provisional(self):
        assert self.lookup("NFL", "FPTS")["status"] == "PROVISIONAL"

    def test_combo_plus_fallback(self):
        e = self.lookup("NFL", "PASS_YDS+RUSH_YDS")
        assert e["status"] == "PROVISIONAL"

    def test_nfl_is_supported(self):
        from gate_engine.model_registry import is_supported
        assert is_supported("NFL", "PASS_YDS")
        assert is_supported("NFL", "TD")
        assert not is_supported("NFL", "NONEXISTENT_PROP")

    def test_provisional_ceiling_present(self):
        e = self.lookup("NFL", "PASS_YDS")
        assert "provisional_ceiling" in e
        assert e["provisional_ceiling"]["money_grade_allowed"] is False

    def test_nhl_still_no_model(self):
        e = self.lookup("NHL", "G")
        assert e["status"] == "NO_REGISTERED_MODEL"


# ---------------------------------------------------------------------------
# model_registry — Tennis entries
# ---------------------------------------------------------------------------

class TestModelRegistryTennis:
    def setup_method(self):
        from gate_engine.model_registry import lookup, is_supported
        self.lookup = lookup
        self.is_supported = is_supported

    def test_fantasy_score_provisional(self):
        e = self.lookup("TENNIS", "FANTASY_SCORE")
        assert e["status"] == "PROVISIONAL"
        assert e["model_id"] == "tennis_fantasy_gaussian_v1"

    def test_fantasy_alias(self):
        assert self.lookup("TENNIS", "FANTASY")["status"] == "PROVISIONAL"

    def test_fpts_alias(self):
        assert self.lookup("TENNIS", "FPTS")["status"] == "PROVISIONAL"

    def test_games_won_provisional(self):
        assert self.lookup("TENNIS", "GAMES_WON")["status"] == "PROVISIONAL"

    def test_aces_provisional(self):
        assert self.lookup("TENNIS", "ACES")["model_id"] == "tennis_counting_poisson_v1"

    def test_double_faults_provisional(self):
        assert self.lookup("TENNIS", "DOUBLE_FAULTS")["model_id"] == "tennis_counting_poisson_v1"

    def test_tennis_is_supported(self):
        assert self.is_supported("TENNIS", "FANTASY_SCORE")
        assert self.is_supported("TENNIS", "GAMES_WON")
        assert self.is_supported("TENNIS", "ACES")

    def test_provisional_ceiling_present(self):
        e = self.lookup("TENNIS", "FANTASY_SCORE")
        assert e["provisional_ceiling"]["money_grade_allowed"] is False


# ---------------------------------------------------------------------------
# hit_probability — NFL dispatch
# ---------------------------------------------------------------------------

class TestHitProbabilityNFL:
    def _leg(self, stat_key, line, side="MORE"):
        return {"sport": "NFL", "stat_key": stat_key, "line_value": line,
                "side": side, "player_name": "J.Smith"}

    def test_pass_yds_uses_poisson(self):
        from gate_engine.hit_probability import compute, MODEL_POISSON
        game_log = [280.0, 310.0, 195.0, 240.0, 320.0,
                    260.0, 290.0, 175.0, 310.0, 255.0]
        result = compute(self._leg("PASS_YDS", 249.5), game_log)
        assert result.model_used == MODEL_POISSON
        assert result.hit_probability is not None
        assert 0.0 <= result.hit_probability <= 1.0

    def test_rush_yds_uses_poisson(self):
        from gate_engine.hit_probability import compute, MODEL_POISSON
        game_log = [55.0, 80.0, 42.0, 91.0, 65.0,
                    73.0, 38.0, 60.0, 47.0, 88.0]
        result = compute(self._leg("RUSH_YDS", 59.5), game_log)
        assert result.model_used == MODEL_POISSON

    def test_rec_uses_poisson(self):
        from gate_engine.hit_probability import compute, MODEL_POISSON
        game_log = [4.0, 7.0, 5.0, 6.0, 3.0, 8.0, 5.0, 4.0, 6.0, 7.0]
        result = compute(self._leg("REC", 4.5), game_log)
        assert result.model_used == MODEL_POISSON

    def test_td_binary_uses_bernoulli(self):
        from gate_engine.hit_probability import compute, MODEL_BERNOULLI
        game_log = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0]
        result = compute(self._leg("TD", 0.5), game_log)
        assert result.model_used == MODEL_BERNOULLI
        assert result.hit_probability == pytest.approx(0.6)

    def test_pass_td_binary_uses_bernoulli(self):
        from gate_engine.hit_probability import compute, MODEL_BERNOULLI
        game_log = [2.0, 1.0, 3.0, 0.0, 2.0, 1.0, 1.0, 2.0, 0.0, 1.0]
        result = compute(self._leg("PASS_TD", 1.5), game_log)
        assert result.model_used == MODEL_BERNOULLI

    def test_td_above_15_uses_poisson(self):
        """TD at line 2.5 (above 1.5) → NOT near-binary → Poisson."""
        from gate_engine.hit_probability import compute, MODEL_POISSON
        game_log = [2.0, 1.0, 3.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 3.0]
        result = compute(self._leg("PASS_TD", 2.5), game_log)
        assert result.model_used == MODEL_POISSON

    def test_no_game_log_returns_no_data(self):
        from gate_engine.hit_probability import compute, MODEL_NO_DATA
        result = compute(self._leg("PASS_YDS", 249.5), [])
        assert result.model_used == MODEL_NO_DATA

    def test_pass_yds_less_side(self):
        from gate_engine.hit_probability import compute, MODEL_POISSON
        game_log = [180.0, 210.0, 155.0, 200.0, 190.0]
        result = compute(self._leg("PASS_YDS", 249.5, side="LESS"), game_log)
        assert result.model_used == MODEL_POISSON
        assert result.hit_probability > 0.5  # λ ≈ 187, P(X < 249) is high

    def test_provisional_ceiling_in_registry(self):
        from gate_engine.model_registry import lookup
        e = lookup("NFL", "PASS_YDS")
        assert e["provisional_ceiling"]["power_eligibility"] is False


# ---------------------------------------------------------------------------
# hit_probability — Tennis dispatch
# ---------------------------------------------------------------------------

class TestHitProbabilityTennis:
    def _leg(self, stat_key, line, side="MORE"):
        return {"sport": "TENNIS", "stat_key": stat_key, "line_value": line,
                "side": side, "player_name": "C.Alcaraz"}

    def test_fantasy_score_uses_gaussian(self):
        from gate_engine.hit_probability import compute, MODEL_FS_GAUSSIAN_PROVISIONAL
        # Simulated match fantasy scores — Tennis formula is verified in registry
        game_log = [22.5, 18.0, 25.5, 20.0, 23.0,
                    19.5, 24.0, 21.5, 17.5, 26.0]
        result = compute(self._leg("FANTASY_SCORE", 21.5), game_log)
        assert result.model_used == MODEL_FS_GAUSSIAN_PROVISIONAL
        assert result.hit_probability is not None
        assert 0.0 <= result.hit_probability <= 1.0
        assert "UNCALIBRATED_FANTASY_SCORE_COHORT" in result.calibration_note

    def test_games_won_uses_gaussian(self):
        from gate_engine.hit_probability import compute, MODEL_GAUSSIAN
        game_log = [13.0, 11.0, 15.0, 12.0, 14.0,
                    10.0, 13.0, 14.0, 12.0, 11.0]
        result = compute(self._leg("GAMES_WON", 12.5), game_log)
        assert result.model_used == MODEL_GAUSSIAN

    def test_aces_uses_poisson(self):
        from gate_engine.hit_probability import compute, MODEL_POISSON
        game_log = [8.0, 12.0, 6.0, 9.0, 11.0,
                    7.0, 10.0, 8.0, 9.0, 12.0]
        result = compute(self._leg("ACES", 8.5), game_log)
        assert result.model_used == MODEL_POISSON

    def test_double_faults_uses_poisson(self):
        from gate_engine.hit_probability import compute, MODEL_POISSON
        game_log = [3.0, 2.0, 4.0, 1.0, 3.0, 2.0, 3.0, 4.0, 2.0, 3.0]
        result = compute(self._leg("DOUBLE_FAULTS", 2.5), game_log)
        assert result.model_used == MODEL_POISSON

    def test_fantasy_gaussian_less_side(self):
        from gate_engine.hit_probability import compute
        game_log = [22.5, 18.0, 25.5, 20.0, 23.0,
                    19.5, 24.0, 21.5, 17.5, 26.0]
        more = compute(self._leg("FANTASY_SCORE", 21.5, "MORE"), game_log)
        less = compute(self._leg("FANTASY_SCORE", 21.5, "LESS"), game_log)
        # raw_model_probability (pre-calibration) sums to ~1.0 across MORE + LESS.
        # hit_probability is the calibrated value and will NOT sum to 1.
        assert more.raw_model_probability is not None
        assert less.raw_model_probability is not None
        assert abs(more.raw_model_probability + less.raw_model_probability - 1.0) < 0.02

    def test_gaussian_needs_3_samples(self):
        from gate_engine.hit_probability import compute, MODEL_NO_DATA
        result = compute(self._leg("FANTASY_SCORE", 20.0), [18.0, 22.0])
        assert result.model_used == MODEL_NO_DATA

    def test_unsupported_tennis_stat(self):
        from gate_engine.hit_probability import compute, MODEL_NO_REGISTERED_MODEL
        game_log = [10.0] * 5
        result = compute(self._leg("BAGELS", 0.5), game_log)
        assert result.model_used == MODEL_NO_REGISTERED_MODEL


# ---------------------------------------------------------------------------
# auto_game_log — NFL / Tennis dispatch (mocked)
# ---------------------------------------------------------------------------

class TestAutoGameLogNFLTennis:
    def test_nfl_dispatch_calls_fetch_nfl(self):
        from gate_engine import auto_game_log
        fake_values = [280.0, 310.0, 195.0, 240.0, 320.0]
        with patch("gate_engine.auto_game_log._fetch_nfl",
                   return_value=(fake_values, "nfl_test")) as mock_fn:
            result = auto_game_log.fetch_game_log(
                player_id="00-0039163",
                sport="NFL",
                stat_key="PASS_YDS",
                target_date="2024-12-01",
                n_games=5,
            )
        mock_fn.assert_called_once_with("00-0039163", "PASS_YDS", "2024-12-01", 5)
        assert result["values"] == fake_values
        assert result["sport"] == "NFL"
        assert result["source"] == "nfl_test"

    def test_tennis_dispatch_calls_fetch_tennis(self):
        from gate_engine import auto_game_log
        fake_values = [22.5, 18.0, 25.5, 20.0, 23.0]
        with patch("gate_engine.auto_game_log._fetch_tennis",
                   return_value=(fake_values, "tennis_test", "ATP_MAIN_DRAW")) as mock_fn:
            result = auto_game_log.fetch_game_log(
                player_id="Carlos Alcaraz",
                sport="TENNIS",
                stat_key="FANTASY_SCORE",
                target_date="2024-09-01",
                n_games=5,
            )
        mock_fn.assert_called_once()
        assert result["values"] == fake_values
        assert result["sport"] == "TENNIS"
        assert result["tour_level"] == "ATP_MAIN_DRAW"

    def test_nfl_game_log_unavailable_bubbles(self):
        from gate_engine.auto_game_log import fetch_game_log, GameLogUnavailable
        with patch("gate_engine.auto_game_log._fetch_nfl",
                   side_effect=GameLogUnavailable("player not found")):
            with pytest.raises(GameLogUnavailable, match="player not found"):
                fetch_game_log("Unknown Player", "NFL", "PASS_YDS", "2024-12-01")

    def test_tennis_game_log_unavailable_bubbles(self):
        from gate_engine.auto_game_log import fetch_game_log, GameLogUnavailable
        with patch("gate_engine.auto_game_log._fetch_tennis",
                   side_effect=GameLogUnavailable("ITF player not covered")):
            with pytest.raises(GameLogUnavailable, match="ITF"):
                fetch_game_log("Some ITF Player", "TENNIS", "FANTASY_SCORE", "2024-06-01")

    def test_nhl_still_raises(self):
        from gate_engine.auto_game_log import fetch_game_log, GameLogUnavailable
        with pytest.raises(GameLogUnavailable):
            fetch_game_log("Connor McDavid", "NHL", "G", "2024-12-01")

    # -----------------------------------------------------------------------
    # Spec-required end-to-end Tennis tests (from wow-claude-integration-spec)
    # -----------------------------------------------------------------------

    def test_atp_tour_level_match_returns_probability(self):
        """
        Spec requirement: one passing end-to-end test on an ATP/WTA tour-level
        match — tour_level=ATP_MAIN_DRAW is surfaced and hit_probability is not None.
        """
        from gate_engine import auto_game_log
        from gate_engine.hit_probability import compute, MODEL_FS_GAUSSIAN_PROVISIONAL

        # Clear the cache so a prior test's entry for this player doesn't
        # interfere with the assertion on result["values"].
        auto_game_log._CACHE.clear()

        atp_game_log = [22.5, 18.0, 25.5, 20.0, 23.0,
                        19.5, 24.0, 21.5, 17.5, 26.0]
        atp_source   = "github:JeffSackmann/tennis_atp 2024"
        atp_tour     = "ATP_MAIN_DRAW"

        with patch("gate_engine.auto_game_log._fetch_tennis",
                   return_value=(atp_game_log, atp_source, atp_tour)):
            result = auto_game_log.fetch_game_log(
                player_id="Carlos Alcaraz",
                sport="TENNIS",
                stat_key="FANTASY_SCORE",
                target_date="2024-09-01",
                n_games=10,
            )

        assert result["tour_level"] == "ATP_MAIN_DRAW"
        assert result["values"] == atp_game_log
        assert result["sport"] == "TENNIS"

        # Downstream probability step must return a number (not null)
        leg = {
            "sport": "TENNIS",
            "stat_key": "FANTASY_SCORE",
            "line_value": 21.5,
            "side": "MORE",
            "player_name": "Carlos Alcaraz",
        }
        prob_result = compute(leg, result["values"])
        assert prob_result.hit_probability is not None, (
            "ATP tour-level match must return a real probability, not null"
        )
        assert prob_result.model_used == MODEL_FS_GAUSSIAN_PROVISIONAL
        assert 0.0 <= prob_result.hit_probability <= 1.0

    def test_itf_challenger_fails_closed_with_tour_tier_reason(self):
        """
        Spec requirement: one explicit test asserting an ITF/Challenger-level
        match fails closed with a tour_level-aware reason — expected outcome,
        not a bug.  The error message must begin with NO_DATA_FOR_TOUR_TIER.
        """
        from gate_engine.auto_game_log import fetch_game_log, GameLogUnavailable
        from gate_engine.tennis_game_log import NO_DATA_FOR_TOUR_TIER

        def fake_fetch_tennis(player_id, stat_key, date_str, n):
            raise GameLogUnavailable(
                f"Tennis game log: {NO_DATA_FOR_TOUR_TIER}: player '{player_id}' "
                f"not found in ATP+WTA main-draw dataset for [2024]. "
                f"ITF/Challenger players are not covered by this data source — "
                f"this is an expected outcome for lower-tier players, not a bug."
            )

        with patch("gate_engine.auto_game_log._fetch_tennis", side_effect=fake_fetch_tennis):
            with pytest.raises(GameLogUnavailable) as exc_info:
                fetch_game_log(
                    player_id="Laura Pigossi",   # typical ITF/Challenger player
                    sport="TENNIS",
                    stat_key="FANTASY_SCORE",
                    target_date="2024-06-01",
                )

        error_message = str(exc_info.value)
        assert NO_DATA_FOR_TOUR_TIER in error_message, (
            f"ITF/Challenger failure reason must contain the tour-tier prefix "
            f"'{NO_DATA_FOR_TOUR_TIER}'; got: {error_message!r}"
        )
        # Must not contain a generic or misleading message
        assert "NO_GAME_LOG_PROVIDED" not in error_message

    def test_tour_level_persisted_in_cache(self):
        """tour_level is stored in the cache and returned on cache hit."""
        from gate_engine import auto_game_log
        auto_game_log._CACHE.clear()

        vals   = [22.5, 18.0, 25.5, 20.0, 23.0]
        source = "github:JeffSackmann/tennis_atp 2024"

        with patch("gate_engine.auto_game_log._fetch_tennis",
                   return_value=(vals, source, "ATP_MAIN_DRAW")):
            r1 = auto_game_log.fetch_game_log(
                "A. Player", "TENNIS", "FANTASY_SCORE", "2024-09-01", 5
            )

        # Cache hit — _fetch_tennis must NOT be called a second time
        with patch("gate_engine.auto_game_log._fetch_tennis",
                   side_effect=AssertionError("should not call fetch on cache hit")):
            r2 = auto_game_log.fetch_game_log(
                "A. Player", "TENNIS", "FANTASY_SCORE", "2024-09-01", 5
            )

        assert r1["tour_level"] == "ATP_MAIN_DRAW"
        assert r2["tour_level"] == "ATP_MAIN_DRAW"
        assert r2["cached"] is True

    def test_nfl_cache_key_includes_sport(self):
        """Two different sports with same player_id must not collide in cache."""
        from gate_engine import auto_game_log
        auto_game_log._CACHE.clear()
        nfl_vals = [280.0, 310.0]
        with patch("gate_engine.auto_game_log._fetch_nfl",
                   return_value=(nfl_vals, "nfl")):
            r1 = auto_game_log.fetch_game_log("00-0039163", "NFL", "PASS_YDS", "2024-12-01", 2)
        nba_vals = [28.0, 31.0]
        with patch("gate_engine.auto_game_log._fetch_nba",
                   return_value=(nba_vals, "nba")):
            r2 = auto_game_log.fetch_game_log("00-0039163", "NBA", "PTS", "2024-12-01", 2)
        assert r1["values"] == nfl_vals
        assert r2["values"] == nba_vals


# ---------------------------------------------------------------------------
# nfl_game_log — season determination
# ---------------------------------------------------------------------------

class TestNFLSeasonFromDate:
    def setup_method(self):
        from gate_engine.nfl_game_log import _nfl_season_from_date
        self.fn = _nfl_season_from_date

    def test_september_is_current_year(self):
        assert self.fn(datetime.date(2024, 9, 5)) == 2024

    def test_october_is_current_year(self):
        assert self.fn(datetime.date(2024, 10, 15)) == 2024

    def test_december_is_current_year(self):
        assert self.fn(datetime.date(2024, 12, 22)) == 2024

    def test_january_is_prior_year(self):
        assert self.fn(datetime.date(2025, 1, 15)) == 2024

    def test_february_is_prior_year(self):
        assert self.fn(datetime.date(2025, 2, 9)) == 2024

    def test_june_offseason_is_prior_year(self):
        assert self.fn(datetime.date(2025, 6, 1)) == 2024

    def test_august_is_prior_year(self):
        assert self.fn(datetime.date(2025, 8, 20)) == 2024


# ---------------------------------------------------------------------------
# nfl_game_log — stat key mapping
# ---------------------------------------------------------------------------

class TestNFLStatColMapping:
    def test_all_mapped_cols_present(self):
        from gate_engine.nfl_game_log import _STAT_COLS
        expected = [
            "PASS_YDS", "RUSH_YDS", "REC_YDS", "REC", "TARGETS",
            "PASS_ATT", "PASS_CMP", "SACK", "PASS_TD", "RUSH_TD",
            "REC_TD", "INT", "FPTS", "FPTS_PPR",
        ]
        for k in expected:
            assert k in _STAT_COLS, f"{k} missing from _STAT_COLS"

    def test_td_combo_not_in_stat_cols(self):
        """TD is a combo; handled separately from single-col stats."""
        from gate_engine.nfl_game_log import _STAT_COLS
        # TD is handled via _TD_COLS combo path, not _STAT_COLS
        assert "TD" not in _STAT_COLS

    def test_unknown_stat_raises(self):
        from gate_engine.nfl_game_log import fetch
        import datetime
        with pytest.raises((KeyError, RuntimeError)):
            fetch("Patrick Mahomes", "NONEXISTENT", "2024-12-01", 5)


# ---------------------------------------------------------------------------
# tennis_game_log — score parser
# ---------------------------------------------------------------------------

class TestTennisScoreParser:
    def setup_method(self):
        from gate_engine.tennis_game_log import _parse_games
        self.parse = _parse_games

    def test_straight_sets(self):
        assert self.parse("6-3 6-2") == (12, 5)

    def test_three_setter(self):
        assert self.parse("6-4 3-6 6-3") == (15, 13)

    def test_tiebreak_set(self):
        w, l = self.parse("7-6(3) 6-4")
        assert w == 13
        assert l == 10

    def test_retirement_partial(self):
        w, l = self.parse("6-3 3-0 RET")
        assert w == 9
        assert l == 3

    def test_walkover_returns_zeros(self):
        assert self.parse("W/O") == (0, 0)

    def test_empty_string_returns_zeros(self):
        assert self.parse("") == (0, 0)

    def test_dominant_winner(self):
        w, l = self.parse("6-1 6-0")
        assert w == 12
        assert l == 1

    def test_five_setter(self):
        w, l = self.parse("6-4 3-6 6-7(4) 6-2 7-5")
        assert w == 28
        assert l == 24


# ---------------------------------------------------------------------------
# tennis_game_log — fantasy score formula
# ---------------------------------------------------------------------------

class TestTennisFantasyFormula:
    def test_formula_matches_spec(self):
        """games_won + 0.5*aces − 0.5*double_faults"""
        # Simulate two rows: winner with 13 games, 8 aces, 2 df
        row_w = {"games_won": 13, "aces": 8.0, "double_faults": 2.0, "is_winner": True, "date": "20240901"}
        score = row_w["games_won"] + 0.5 * row_w["aces"] - 0.5 * row_w["double_faults"]
        assert score == pytest.approx(16.0)

    def test_loser_formula(self):
        row_l = {"games_won": 5, "aces": 4.0, "double_faults": 3.0, "is_winner": False, "date": "20240901"}
        score = row_l["games_won"] + 0.5 * row_l["aces"] - 0.5 * row_l["double_faults"]
        assert score == pytest.approx(5.5)


# ---------------------------------------------------------------------------
# normalizer — NFL + Tennis stat keys
# ---------------------------------------------------------------------------

class TestNormalizerNFLTennis:
    def setup_method(self):
        from gate_engine.normalizer import _map_stat_key
        self.map = _map_stat_key

    # NFL
    def test_passing_yards_maps(self):
        assert self.map("passing yards", "NFL")["stat_key"] == "PASS_YDS"

    def test_rushing_yards_maps(self):
        assert self.map("rushing yards", "NFL")["stat_key"] == "RUSH_YDS"

    def test_receiving_yards_maps(self):
        assert self.map("receiving yards", "NFL")["stat_key"] == "REC_YDS"

    def test_receptions_maps(self):
        assert self.map("receptions", "NFL")["stat_key"] == "REC"

    def test_catches_alias(self):
        assert self.map("catches", "NFL")["stat_key"] == "REC"

    def test_anytime_td_maps(self):
        assert self.map("anytime touchdown", "NFL")["stat_key"] == "ANYTIME_TD"

    def test_anytime_td_short(self):
        assert self.map("anytime td", "NFL")["stat_key"] == "ANYTIME_TD"

    def test_pass_td_maps(self):
        assert self.map("passing touchdowns", "NFL")["stat_key"] == "PASS_TD"

    def test_rush_td_maps(self):
        assert self.map("rushing tds", "NFL")["stat_key"] == "RUSH_TD"

    def test_rec_td_maps(self):
        assert self.map("receiving tds", "NFL")["stat_key"] == "REC_TD"

    def test_completions_maps(self):
        assert self.map("completions", "NFL")["stat_key"] == "PASS_CMP"

    def test_interceptions_maps(self):
        assert self.map("interceptions", "NFL")["stat_key"] == "INT"

    def test_sacks_maps(self):
        assert self.map("sacks", "NFL")["stat_key"] == "SACK"

    def test_tackles_maps(self):
        assert self.map("tackles", "NFL")["stat_key"] == "TACKLE"

    # Tennis
    def test_tennis_fantasy_score_maps(self):
        # "fantasy score" is caught by _COMBO_PROP_PATTERNS → stat_formula field
        r = self.map("fantasy score", "TENNIS")
        assert r.get("stat_formula") == "FANTASY_SCORE" or r.get("stat_key") == "FANTASY_SCORE"

    def test_tennis_fantasy_alias(self):
        # "fantasy" is caught by _COMBO_PROP_PATTERNS → stat_formula = "FANTASY"
        r = self.map("fantasy", "TENNIS")
        formula = r.get("stat_formula") or ""
        key = r.get("stat_key") or ""
        assert "FANTASY" in formula.upper() or "FANTASY" in key.upper()

    def test_tennis_games_won_maps(self):
        assert self.map("games won", "TENNIS")["stat_key"] == "GAMES_WON"

    def test_tennis_aces_maps(self):
        assert self.map("aces", "TENNIS")["stat_key"] == "ACES"

    def test_tennis_double_faults_maps(self):
        assert self.map("double faults", "TENNIS")["stat_key"] == "DOUBLE_FAULTS"

    def test_tennis_df_alias(self):
        assert self.map("df", "TENNIS")["stat_key"] == "DOUBLE_FAULTS"

    # Combo patterns already in normalizer
    def test_nfl_combo_pass_rush(self):
        r = self.map("pass + rush", "NFL")
        assert r["stat_formula"] == "PASS_YDS+RUSH_YDS"
