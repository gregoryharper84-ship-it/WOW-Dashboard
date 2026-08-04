"""
gate_engine/tests/test_fantasy_score.py

Tests for Fantasy Score formula derivation (Task #84):
  - is_quality_start / _ip_to_outs helpers
  - derive_nba_wnba_row
  - derive_nfl_row
  - derive_mlb_hitter_row
  - derive_mlb_pitcher_row
  - derive_series (all sports)
  - auto_game_log dispatch (mocked)
  - model_registry entries
  - hit_probability Gaussian routing for FANTASY_SCORE
  - FORMULA_FLAGS / unvalidated warnings present
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# QS helper + IP parsing
# ---------------------------------------------------------------------------

class TestQualityStart:
    def setup_method(self):
        from gate_engine.fantasy_score import is_quality_start, _ip_to_outs
        self.qs = is_quality_start
        self.outs = _ip_to_outs

    def test_qs_classic(self):
        assert self.qs(6.0, 2) is True

    def test_qs_exactly_6_ip_3_er(self):
        assert self.qs(6.0, 3) is True

    def test_qs_6_ip_4_er_fails(self):
        assert self.qs(6.0, 4) is False

    def test_qs_5_2_ip_fails(self):
        # 5.2 = 5 innings + 2 outs → 5.667 IP < 6
        assert self.qs(5.2, 1) is False

    def test_qs_7_ip_2_er(self):
        assert self.qs(7.0, 2) is True

    def test_qs_6_1_ip_2_er(self):
        # 6.1 → 6.333 IP — qualifies
        assert self.qs(6.1, 2) is True

    def test_outs_6_0(self):
        assert self.outs(6.0) == 18

    def test_outs_6_2(self):
        # 6.2 = 6 inn + 2 outs = 18 + 2 = 20
        assert self.outs(6.2) == 20

    def test_outs_7_1(self):
        # 7.1 = 7 inn + 1 out = 21 + 1 = 22
        assert self.outs(7.1) == 22

    def test_outs_3_0(self):
        assert self.outs(3.0) == 9


# ---------------------------------------------------------------------------
# NBA / WNBA row derivation
# ---------------------------------------------------------------------------

class TestDeriveNBAWNBARow:
    def setup_method(self):
        from gate_engine.fantasy_score import derive_nba_wnba_row, NBA_WNBA_WEIGHTS
        self.derive = derive_nba_wnba_row
        self.w = NBA_WNBA_WEIGHTS

    def _expect(self, pts, reb, ast, stl, blk, tov):
        return round(
            pts*1.0 + reb*1.2 + ast*1.5 + stl*3.0 + blk*3.0 + tov*(-1.0), 2
        )

    def test_sample_nba_row_uppercase(self):
        row = {"PTS": 28, "REB": 7, "AST": 9, "STL": 1, "BLK": 2, "TOV": 3}
        expected = self._expect(28, 7, 9, 1, 2, 3)
        assert self.derive(row) == pytest.approx(expected)

    def test_sample_wnba_row_lowercase(self):
        row = {"pts": 22, "reb": 5, "ast": 4, "stl": 2, "blk": 1, "tov": 2}
        expected = self._expect(22, 5, 4, 2, 1, 2)
        assert self.derive(row) == pytest.approx(expected)

    def test_wnba_bdl_turnover_key(self):
        """BallDontLie WNBA uses 'turnover' not 'tov'."""
        row = {"pts": 18, "reb": 8, "ast": 3, "stl": 1, "blk": 0, "turnover": 2}
        expected = self._expect(18, 8, 3, 1, 0, 2)
        assert self.derive(row) == pytest.approx(expected)

    def test_high_tov_reduces_score(self):
        row_clean = {"pts": 30, "reb": 5, "ast": 5, "stl": 2, "blk": 1, "tov": 0}
        row_sloppy = dict(row_clean, tov=5)
        assert self.derive(row_sloppy) < self.derive(row_clean)

    def test_missing_fields_default_zero(self):
        row = {"pts": 20}
        # reb=ast=stl=blk=tov=0
        assert self.derive(row) == pytest.approx(20.0)

    def test_weights_match_constants(self):
        row = {"pts": 1, "reb": 1, "ast": 1, "stl": 1, "blk": 1, "tov": 1}
        expected = sum([1.0, 1.2, 1.5, 3.0, 3.0, -1.0])
        assert self.derive(row) == pytest.approx(expected)

    def test_empty_row_returns_zero(self):
        assert self.derive({}) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# NFL row derivation
# ---------------------------------------------------------------------------

class TestDeriveNFLRow:
    def setup_method(self):
        from gate_engine.fantasy_score import derive_nfl_row, NFL_WEIGHTS
        self.derive = derive_nfl_row
        self.w = NFL_WEIGHTS

    def test_qb_game(self):
        row = {
            "pass_yds": 300, "pass_td": 3, "int": 1,
            "rush_yds": 12, "rush_td": 0,
            "rec_yds": 0, "rec_td": 0, "rec": 0,
            "fumbles_lost": 0,
        }
        expected = (300/25) + (3*4) + (1*-2) + (12/10)
        assert self.derive(row) == pytest.approx(expected, abs=0.01)

    def test_wr_game(self):
        row = {
            "pass_yds": 0, "pass_td": 0, "int": 0,
            "rush_yds": 0, "rush_td": 0,
            "rec_yds": 95, "rec_td": 1, "rec": 7,
            "fumbles_lost": 0,
        }
        expected = (95/10) + (1*6) + (7*0.5)
        assert self.derive(row) == pytest.approx(expected, abs=0.01)

    def test_rb_game(self):
        row = {
            "pass_yds": 0, "pass_td": 0, "int": 0,
            "rush_yds": 85, "rush_td": 1,
            "rec_yds": 25, "rec_td": 0, "rec": 4,
            "fumbles_lost": 1,
        }
        expected = (85/10) + 6 + (25/10) + (4*0.5) + (-2)
        assert self.derive(row) == pytest.approx(expected, abs=0.01)

    def test_int_reduces_score(self):
        base = {"pass_yds": 250, "pass_td": 2, "int": 0,
                "rush_yds": 0, "rush_td": 0, "rec_yds": 0,
                "rec_td": 0, "rec": 0, "fumbles_lost": 0}
        with_int = dict(base, int=2)
        assert self.derive(with_int) < self.derive(base)

    def test_zero_game(self):
        row = {k: 0 for k in [
            "pass_yds", "pass_td", "int", "rush_yds", "rush_td",
            "rec_yds", "rec_td", "rec", "fumbles_lost"
        ]}
        assert self.derive(row) == pytest.approx(0.0)

    def test_nfl_data_py_column_names(self):
        """nfl_data_py uses snake_case column names — alternate aliases work."""
        row = {
            "passing_yards": 320, "passing_tds": 2, "interceptions": 1,
            "rushing_yards": 0, "rushing_tds": 0,
            "receiving_yards": 0, "receiving_tds": 0,
            "receptions": 0, "fumbles_lost": 0,
        }
        result = self.derive(row)
        expected = (320/25) + (2*4) + (1*-2)
        assert result == pytest.approx(expected, abs=0.01)

    def test_reception_weight_is_half_ppr(self):
        """Confirm half-PPR (0.5) weight is being used — flag for verification."""
        from gate_engine.fantasy_score import NFL_WEIGHTS, NFL_RECEPTION_WEIGHT_NOTE
        assert NFL_WEIGHTS["reception"] == pytest.approx(0.5)
        assert "UNCONFIRMED" in NFL_RECEPTION_WEIGHT_NOTE


# ---------------------------------------------------------------------------
# MLB Hitter row derivation
# ---------------------------------------------------------------------------

class TestDeriveMLBHitterRow:
    def setup_method(self):
        from gate_engine.fantasy_score import derive_mlb_hitter_row
        self.derive = derive_mlb_hitter_row

    def _expect(self, singles, doubles, triples, hrs, runs, rbi, bb, hbp, sb):
        return round(
            singles*3 + doubles*5 + triples*8 + hrs*10
            + runs*2 + rbi*2 + bb*2 + hbp*2 + sb*5, 2
        )

    def test_typical_2_hit_game(self):
        row = {
            "hits": 2, "doubles": 1, "triples": 0, "homeRuns": 0,
            "runs": 1, "rbi": 1, "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0
        }
        # singles = 2-1-0-0 = 1
        expected = self._expect(1, 1, 0, 0, 1, 1, 0, 0, 0)
        assert self.derive(row) == pytest.approx(expected)

    def test_hr_game(self):
        row = {
            "hits": 2, "doubles": 0, "triples": 0, "homeRuns": 2,
            "runs": 2, "rbi": 4, "baseOnBalls": 1, "hitByPitch": 0, "stolenBases": 0
        }
        # singles = 2-0-0-2 = 0
        expected = self._expect(0, 0, 0, 2, 2, 4, 1, 0, 0)
        assert self.derive(row) == pytest.approx(expected)

    def test_stolen_base_bonus(self):
        row = {
            "hits": 1, "doubles": 0, "triples": 0, "homeRuns": 0,
            "runs": 1, "rbi": 0, "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 2
        }
        expected = self._expect(1, 0, 0, 0, 1, 0, 0, 0, 2)
        assert self.derive(row) == pytest.approx(expected)

    def test_hitless_game(self):
        row = {
            "hits": 0, "doubles": 0, "triples": 0, "homeRuns": 0,
            "runs": 0, "rbi": 0, "baseOnBalls": 1, "hitByPitch": 0, "stolenBases": 0
        }
        # BB = 2 pts
        assert self.derive(row) == pytest.approx(2.0)

    def test_singles_cannot_go_negative(self):
        """Singles = max(0, H - 2B - 3B - HR) — can't be negative."""
        row = {
            "hits": 1, "doubles": 1, "triples": 0, "homeRuns": 0,
            "runs": 0, "rbi": 0, "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0
        }
        result = self.derive(row)
        assert result >= 0.0

    def test_triple_highest_per_hit_score(self):
        """Triples are worth the most among hit types (8 pts)."""
        single_row = {"hits": 1, "doubles": 0, "triples": 0, "homeRuns": 0,
                      "runs": 0, "rbi": 0, "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0}
        triple_row = dict(single_row, triples=1)
        assert self.derive(triple_row) > self.derive(single_row)


# ---------------------------------------------------------------------------
# MLB Pitcher row derivation
# ---------------------------------------------------------------------------

class TestDeriveMLBPitcherRow:
    def setup_method(self):
        from gate_engine.fantasy_score import derive_mlb_pitcher_row
        self.derive = derive_mlb_pitcher_row

    def test_quality_start_game(self):
        row = {"wins": 1, "strikeOuts": 7, "inningsPitched": 6.1, "earnedRuns": 2}
        # QS = True (6.333 IP, 2 ER)
        # Outs = 6*3+1 = 19
        # FS = 1*6 + 1*4 + 7*3 + 19*1 + 2*(-3) = 6+4+21+19-6 = 44
        expected = 6 + 4 + 21 + 19 - 6
        assert self.derive(row) == pytest.approx(expected)

    def test_loss_no_qs(self):
        row = {"wins": 0, "strikeOuts": 5, "inningsPitched": 5.0, "earnedRuns": 4}
        # QS = False (5 IP)
        # Outs = 15
        # FS = 0 + 0 + 5*3 + 15 + 4*(-3) = 15+15-12 = 18
        expected = 0 + 0 + 15 + 15 - 12
        assert self.derive(row) == pytest.approx(expected)

    def test_earned_runs_penalize(self):
        row_clean = {"wins": 1, "strikeOuts": 6, "inningsPitched": 6.0, "earnedRuns": 1}
        row_rough  = dict(row_clean, earnedRuns=5)
        assert self.derive(row_rough) < self.derive(row_clean)

    def test_qs_threshold_exactly_6ip_3er(self):
        row = {"wins": 0, "strikeOuts": 4, "inningsPitched": 6.0, "earnedRuns": 3}
        # QS = True (exactly 6 IP, 3 ER)
        # Outs = 18; FS = 0 + 4 + 4*3 + 18 - 9 = 0+4+12+18-9 = 25
        expected = 0 + 4 + 12 + 18 - 9
        assert self.derive(row) == pytest.approx(expected)

    def test_missing_fields_default_zero(self):
        row = {"strikeOuts": 5}
        result = self.derive(row)
        # wins=0, qs=False(ip=0), outs=0, er=0
        assert result == pytest.approx(5 * 3)


# ---------------------------------------------------------------------------
# derive_series
# ---------------------------------------------------------------------------

class TestDeriveSeries:
    def setup_method(self):
        from gate_engine.fantasy_score import derive_series
        self.derive = derive_series

    def test_nba_series(self):
        rows = [
            {"PTS": 28, "REB": 7, "AST": 9, "STL": 1, "BLK": 2, "TOV": 3},
            {"PTS": 22, "REB": 5, "AST": 6, "STL": 0, "BLK": 1, "TOV": 2},
        ]
        result = self.derive("NBA", rows)
        assert len(result) == 2
        assert all(isinstance(v, float) for v in result)
        assert result[0] > result[1]  # first row has higher stats

    def test_wnba_series(self):
        rows = [{"pts": 18, "reb": 9, "ast": 4, "stl": 2, "blk": 1, "turnover": 1}]
        result = self.derive("WNBA", rows)
        assert len(result) == 1

    def test_nfl_series(self):
        rows = [
            {"pass_yds": 300, "pass_td": 2, "int": 0, "rush_yds": 15,
             "rush_td": 0, "rec_yds": 0, "rec_td": 0, "rec": 0, "fumbles_lost": 0},
        ]
        result = self.derive("NFL", rows)
        assert len(result) == 1
        assert result[0] > 0

    def test_mlb_hitter_series(self):
        rows = [{"hits": 2, "doubles": 1, "triples": 0, "homeRuns": 0,
                 "runs": 1, "rbi": 1, "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0}]
        result = self.derive("MLB", rows, position="hitter")
        assert len(result) == 1

    def test_mlb_pitcher_series(self):
        rows = [{"wins": 1, "strikeOuts": 7, "inningsPitched": 6.1, "earnedRuns": 2}]
        result = self.derive("MLB", rows, position="pitcher")
        assert len(result) == 1

    def test_bad_row_skipped(self):
        rows = [
            {"PTS": 28, "REB": 7, "AST": 9, "STL": 1, "BLK": 2, "TOV": 3},
            None,  # bad row
            {"PTS": 20, "REB": 5, "AST": 4, "STL": 0, "BLK": 0, "TOV": 1},
        ]
        result = self.derive("NBA", [r for r in rows if r is not None])
        assert len(result) == 2

    def test_unsupported_sport_returns_empty(self):
        result = self.derive("NHL", [{"goals": 1}])
        assert result == []

    def test_empty_rows(self):
        assert self.derive("NBA", []) == []


# ---------------------------------------------------------------------------
# Formula flags — unvalidated warnings
# ---------------------------------------------------------------------------

class TestFormulaFlags:
    def test_global_flag_present(self):
        from gate_engine.fantasy_score import FS_GLOBAL_FLAG
        assert FS_GLOBAL_FLAG == "FANTASY_SCORE_FORMULA_UNVALIDATED"

    def test_nfl_has_reception_unconfirmed(self):
        from gate_engine.fantasy_score import FORMULA_FLAGS
        assert "NFL_RECEPTION_WEIGHT_UNCONFIRMED" in FORMULA_FLAGS["NFL"]

    def test_wnba_has_assumed_same_as_nba(self):
        from gate_engine.fantasy_score import FORMULA_FLAGS
        assert "WNBA_WEIGHTS_ASSUMED_SAME_AS_NBA" in FORMULA_FLAGS["WNBA"]

    def test_nba_no_open_questions(self):
        from gate_engine.fantasy_score import FORMULA_FLAGS
        assert FORMULA_FLAGS["NBA"] == []

    def test_nfl_reception_note_mentions_unconfirmed(self):
        from gate_engine.fantasy_score import NFL_RECEPTION_WEIGHT_NOTE
        assert "UNCONFIRMED" in NFL_RECEPTION_WEIGHT_NOTE
        assert "half-PPR" in NFL_RECEPTION_WEIGHT_NOTE


# ---------------------------------------------------------------------------
# model_registry — FANTASY_SCORE entries
# ---------------------------------------------------------------------------

class TestModelRegistryFantasyScore:
    def setup_method(self):
        from gate_engine.model_registry import lookup
        self.lookup = lookup

    def test_nba_fantasy_score_provisional(self):
        e = self.lookup("NBA", "FANTASY_SCORE")
        assert e["status"] == "PROVISIONAL"
        assert "UNVALIDATED" in e.get("notes", "") or "UNVALIDATED" in e.get("model_id", "")

    def test_wnba_fantasy_score_provisional(self):
        e = self.lookup("WNBA", "FANTASY_SCORE")
        assert e["status"] == "PROVISIONAL"

    def test_nfl_fantasy_score_provisional(self):
        e = self.lookup("NFL", "FANTASY_SCORE")
        assert e["status"] == "PROVISIONAL"

    def test_mlb_fantasy_score_hit_provisional(self):
        e = self.lookup("MLB", "FANTASY_SCORE_HIT")
        assert e["status"] == "PROVISIONAL"

    def test_mlb_fantasy_score_pit_provisional(self):
        e = self.lookup("MLB", "FANTASY_SCORE_PIT")
        assert e["status"] == "PROVISIONAL"

    def test_fantasy_score_has_provisional_ceiling(self):
        """FANTASY_SCORE must not be money-gradeable before validation."""
        e = self.lookup("NBA", "FANTASY_SCORE")
        assert e.get("provisional_ceiling", {}).get("money_grade_allowed") is False

    def test_nfl_reception_flag_in_notes(self):
        e = self.lookup("NFL", "FANTASY_SCORE")
        notes = e.get("notes", "")
        assert "UNCONFIRMED" in notes or "reception" in notes.lower()


# ---------------------------------------------------------------------------
# hit_probability — FANTASY_SCORE routes to Gaussian across sports
# ---------------------------------------------------------------------------

class TestHitProbabilityFantasyScore:
    def _leg(self, sport, line, side="MORE"):
        return {"sport": sport, "stat_key": "FANTASY_SCORE",
                "line_value": line, "side": side, "player_name": "Test Player"}

    def test_nba_fantasy_score_gaussian(self):
        from gate_engine.hit_probability import compute, MODEL_GAUSSIAN
        game_log = [38.5, 42.1, 35.8, 44.0, 39.2, 41.5, 37.6, 43.8, 36.9, 40.1]
        result = compute(self._leg("NBA", 39.5), game_log)
        assert result.model_used == MODEL_GAUSSIAN
        assert result.hit_probability is not None

    def test_wnba_fantasy_score_gaussian(self):
        from gate_engine.hit_probability import compute, MODEL_GAUSSIAN
        game_log = [28.5, 32.1, 25.8, 30.0, 29.2, 27.5, 31.6, 28.8, 26.9, 30.1]
        result = compute(self._leg("WNBA", 29.5), game_log)
        assert result.model_used == MODEL_GAUSSIAN

    def test_nfl_fantasy_score_gaussian(self):
        from gate_engine.hit_probability import compute, MODEL_GAUSSIAN
        game_log = [22.5, 18.0, 25.5, 20.0, 23.0, 19.5, 24.0, 21.5, 17.5, 26.0]
        result = compute(self._leg("NFL", 21.5), game_log)
        assert result.model_used == MODEL_GAUSSIAN

    def test_mlb_hitter_fantasy_score_gaussian(self):
        from gate_engine.hit_probability import compute, MODEL_GAUSSIAN
        game_log = [8.0, 12.5, 6.0, 15.0, 9.5, 11.0, 7.5, 13.0, 10.0, 14.5]
        result = compute(self._leg("MLB", 10.5), game_log)
        assert result.model_used == MODEL_GAUSSIAN

    def test_fantasy_score_less_side(self):
        from gate_engine.hit_probability import compute, MODEL_GAUSSIAN
        game_log = [38.5, 42.1, 35.8, 44.0, 39.2, 41.5, 37.6, 43.8, 36.9, 40.1]
        more = compute(self._leg("NBA", 39.5, "MORE"), game_log)
        less = compute(self._leg("NBA", 39.5, "LESS"), game_log)
        assert abs(more.hit_probability + less.hit_probability - 1.0) < 0.02

    def test_fantasy_score_needs_3_samples(self):
        from gate_engine.hit_probability import compute, MODEL_NO_DATA
        result = compute(self._leg("NBA", 39.5), [38.5, 42.1])
        assert result.model_used == MODEL_NO_DATA


# ---------------------------------------------------------------------------
# auto_game_log dispatch — FANTASY_SCORE (mocked)
# ---------------------------------------------------------------------------

class TestAutoGameLogFantasyScore:
    def test_nba_fantasy_dispatches(self):
        from gate_engine import auto_game_log
        auto_game_log._CACHE.clear()
        fake_vals = [38.5, 42.1, 35.8, 44.0, 39.2]
        with patch("gate_engine.auto_game_log._fetch_nba_fantasy",
                   return_value=(fake_vals, "nba_api_fs")) as mock_fn:
            result = auto_game_log.fetch_game_log(
                "203999", "NBA", "FANTASY_SCORE", "2024-12-01", 5
            )
        mock_fn.assert_called_once()
        assert result["values"] == fake_vals
        assert result["stat_key"] == "FANTASY_SCORE"

    def test_wnba_fantasy_dispatches(self):
        from gate_engine import auto_game_log
        auto_game_log._CACHE.clear()
        fake_vals = [28.5, 32.1, 25.8]
        with patch("gate_engine.auto_game_log._fetch_wnba_fantasy",
                   return_value=(fake_vals, "bdl_fs")):
            result = auto_game_log.fetch_game_log(
                "wnba_player_1", "WNBA", "FANTASY_SCORE", "2024-07-01", 3
            )
        assert result["values"] == fake_vals

    def test_nfl_fantasy_dispatches(self):
        from gate_engine import auto_game_log
        auto_game_log._CACHE.clear()
        fake_vals = [22.5, 18.0, 25.5]
        with patch("gate_engine.auto_game_log._fetch_nfl_fantasy",
                   return_value=(fake_vals, "nfl_data_py_fs")):
            result = auto_game_log.fetch_game_log(
                "P.Mahomes", "NFL", "FANTASY_SCORE", "2024-12-01", 3
            )
        assert result["values"] == fake_vals

    def test_mlb_hitter_dispatches(self):
        from gate_engine import auto_game_log
        auto_game_log._CACHE.clear()
        fake_vals = [8.0, 12.5, 6.0]
        with patch("gate_engine.auto_game_log._fetch_mlb_hitter_fantasy",
                   return_value=(fake_vals, "statsapi_fs_hit")):
            result = auto_game_log.fetch_game_log(
                "mlb_player_1", "MLB", "FANTASY_SCORE_HIT", "2024-09-01", 3
            )
        assert result["values"] == fake_vals

    def test_mlb_pitcher_dispatches(self):
        from gate_engine import auto_game_log
        auto_game_log._CACHE.clear()
        fake_vals = [34.0, 28.5, 41.0]
        with patch("gate_engine.auto_game_log._fetch_mlb_pitcher_fantasy",
                   return_value=(fake_vals, "statsapi_fs_pit")):
            result = auto_game_log.fetch_game_log(
                "mlb_pitcher_1", "MLB", "FANTASY_SCORE_PIT", "2024-09-01", 3
            )
        assert result["values"] == fake_vals

    def test_unsupported_sport_fs_raises(self):
        from gate_engine.auto_game_log import fetch_game_log, GameLogUnavailable
        with pytest.raises(GameLogUnavailable):
            fetch_game_log("player", "NHL", "FANTASY_SCORE", "2024-12-01")

    def test_fs_cache_works(self):
        """FANTASY_SCORE cache key is separate from individual stat keys."""
        from gate_engine import auto_game_log
        auto_game_log._CACHE.clear()
        fake_vals = [38.5, 42.1]
        with patch("gate_engine.auto_game_log._fetch_nba_fantasy",
                   return_value=(fake_vals, "nba_api_fs")):
            r1 = auto_game_log.fetch_game_log("203999", "NBA", "FANTASY_SCORE", "2024-12-01", 2)
        with patch("gate_engine.auto_game_log._fetch_nba_fantasy",
                   side_effect=AssertionError("should use cache")):
            r2 = auto_game_log.fetch_game_log("203999", "NBA", "FANTASY_SCORE", "2024-12-01", 2)
        assert r1["values"] == r2["values"] == fake_vals
        assert r2["cached"] is True
