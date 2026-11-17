"""Opponent-aware NBA/WNBA game-script shadow: strict research regression tests.

These tests check invariants, not predictive skill or calibration. The
underlying scenario heuristic and downstream Poisson rates remain unfit.
"""
from __future__ import annotations

import copy
import unittest

from gate_engine.universal_agent.lanes.wnba_props.game_script.game_environment import (
    derive_script_priors,
    parse_game_environment,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.shadow_gate import (
    GameScriptShadowGate,
    SHADOW_ONLY,
    PRODUCTION_AUTHORITY,
    CAPITAL_AUTHORITY,
)


def _matched(sport: str = "NBA") -> dict:
    return {
        "sport": sport,
        "matchup": {
            "spread": 9.5,       # Must not affect the opponent-aware result.
            "total_line": 280,  # Must not affect the opponent-aware result.
            "opponent_adjusted": {
                "home": {
                    "team_id": "home-001",
                    "adjusted_off_rating": 115.0,
                    "adjusted_def_rating": 107.0,  # points allowed / 100 poss
                    "pace": 98.0,
                },
                "away": {
                    "team_id": "away-001",
                    "adjusted_off_rating": 110.0,
                    "adjusted_def_rating": 112.0,
                    "pace": 100.0,
                },
                "league_avg_off_rating": 110.0,
                "opponent_adjustment_method": "point-in-time schedule adjusted ratings v1",
                "source_record_ids": {
                    "home": "home-rating-receipt",
                    "away": "away-rating-receipt",
                    "league": "league-rating-receipt",
                },
                "as_of_utc": "2026-11-16T12:00:00Z",
                "event_start_utc": "2026-11-16T20:00:00Z",
            },
        },
    }


class OpponentAdjustedGameScriptTest(unittest.TestCase):
    def test_symmetric_offense_defense_interaction(self):
        matched = _matched()
        env = parse_game_environment(matched)
        self.assertIsNotNone(env)
        poss = 99.0
        home_points = poss * (115.0 * 112.0 / 110.0) / 100.0
        away_points = poss * (110.0 * 107.0 / 110.0) / 100.0
        self.assertAlmostEqual(env.spread, home_points - away_points)
        self.assertAlmostEqual(env.total_line, home_points + away_points)
        self.assertEqual(env.basis, "OPPONENT_ADJUSTED_UNFITTED_SHADOW")
        self.assertEqual(len(env.source_record_ids), 3)

    def test_tougher_away_defense_lowers_home_scoring(self):
        original = _matched()
        difficult = copy.deepcopy(original)
        difficult["matchup"]["opponent_adjusted"]["away"]["adjusted_def_rating"] = 95.0
        easy = parse_game_environment(original)
        hard = parse_game_environment(difficult)
        # Away offense and home defense unchanged: only home projected scoring moves.
        self.assertLess(hard.total_line, easy.total_line)
        self.assertLess(hard.spread, easy.spread)

    def test_stronger_home_offense_increases_home_advantage(self):
        original = _matched()
        stronger = copy.deepcopy(original)
        stronger["matchup"]["opponent_adjusted"]["home"]["adjusted_off_rating"] = 125.0
        self.assertGreater(
            parse_game_environment(stronger).spread,
            parse_game_environment(original).spread,
        )

    def test_faster_opponents_increase_total(self):
        original = _matched()
        faster = copy.deepcopy(original)
        faster["matchup"]["opponent_adjusted"]["home"]["pace"] = 110.0
        faster["matchup"]["opponent_adjusted"]["away"]["pace"] = 110.0
        self.assertGreater(
            parse_game_environment(faster).total_line,
            parse_game_environment(original).total_line,
        )

    def test_legacy_market_lines_cannot_override_opponent_features(self):
        original = _matched()
        revised = copy.deepcopy(original)
        revised["matchup"]["spread"] = -12.0
        revised["matchup"]["total_line"] = 170.0
        self.assertEqual(parse_game_environment(original), parse_game_environment(revised))

    def test_opponent_path_runs_without_betting_lines(self):
        item = _matched()
        del item["matchup"]["spread"]
        del item["matchup"]["total_line"]
        env = parse_game_environment(item)
        self.assertIsNotNone(env)
        self.assertEqual(env.basis, "OPPONENT_ADJUSTED_UNFITTED_SHADOW")

    def test_home_away_swap_reverses_spread_not_total(self):
        original = _matched()
        swapped = copy.deepcopy(original)
        pair = swapped["matchup"]["opponent_adjusted"]
        pair["home"], pair["away"] = pair["away"], pair["home"]
        pair["source_record_ids"]["home"], pair["source_record_ids"]["away"] = (
            pair["source_record_ids"]["away"], pair["source_record_ids"]["home"]
        )
        a, b = parse_game_environment(original), parse_game_environment(swapped)
        self.assertAlmostEqual(a.spread, -b.spread)
        self.assertAlmostEqual(a.total_line, b.total_line)

    def test_wnba_basketball_path(self):
        payload = _matched("WNBA")
        env = parse_game_environment(payload)
        self.assertEqual(env.sport, "WNBA")
        self.assertEqual(env.baseline, 160.0)
        self.assertAlmostEqual(derive_script_priors(env).sum(), 1.0, places=5)

    def test_legacy_path_remains_explicitly_uncertified(self):
        legacy = {"sport": "NBA", "matchup": {"spread": 4.0, "total_line": 224}}
        env = parse_game_environment(legacy)
        self.assertEqual(env.basis, "MARKET_ONLY_UNFITTED_SHADOW")
        self.assertEqual(env.source_record_ids, ())

    def test_invalid_input_fails_closed_not_market_fallback(self):
        variants = []
        base = _matched()
        for key in ("adjusted_off_rating", "adjusted_def_rating", "pace"):
            item = copy.deepcopy(base)
            del item["matchup"]["opponent_adjusted"]["away"][key]
            variants.append(item)
        for value in (float("nan"), float("inf"), True, "112", -5.0, 1e100):
            item = copy.deepcopy(base)
            item["matchup"]["opponent_adjusted"]["away"]["adjusted_def_rating"] = value
            variants.append(item)
        for key in ("home", "away", "league"):
            item = copy.deepcopy(base)
            del item["matchup"]["opponent_adjusted"]["source_record_ids"][key]
            variants.append(item)
        for key in ("as_of_utc", "event_start_utc", "opponent_adjustment_method"):
            item = copy.deepcopy(base)
            del item["matchup"]["opponent_adjusted"][key]
            variants.append(item)
        for item in variants:
            with self.subTest(item=item):
                self.assertIsNone(parse_game_environment(item))

    def test_reject_post_start_and_naive_times(self):
        for bad in ("2026-11-16T21:00:00Z", "2026-11-16T19:30:00"):
            item = _matched()
            item["matchup"]["opponent_adjusted"]["as_of_utc"] = bad
            self.assertIsNone(parse_game_environment(item))

    def test_reject_duplicate_teams_and_unadjusted_ratings(self):
        same = _matched()
        same["matchup"]["opponent_adjusted"]["away"]["team_id"] = "home-001"
        self.assertIsNone(parse_game_environment(same))
        raw = _matched()
        raw["matchup"]["opponent_adjusted"]["opponent_adjustment_method"] = "raw"
        self.assertIsNone(parse_game_environment(raw))

    def test_no_cross_sport_numeric_transplant(self):
        item = _matched("NHL")
        self.assertIsNone(parse_game_environment(item))

    def test_shadow_gate_exposes_basis_and_never_executes(self):
        item = _matched("NBA")
        item.update({
            "market": "rebounds",
            "line": 10.5,
            "role_status": {
                "active_status": "ACTIVE",
                "projected_minutes": 32.0,
                "minutes_low": 28.0,
                "minutes_high": 36.0,
            },
            "game_log": [{"reb": 10.0, "min": 32.0}] * 5,
        })
        result = GameScriptShadowGate().run(combined=item, run_id="research-fixture")
        self.assertIn(result["status"], ("COMPLETE", "PARTIAL"))
        self.assertEqual(result["script_basis"], "OPPONENT_ADJUSTED_UNFITTED_SHADOW")
        self.assertEqual(len(result["source_record_ids_unverified"]), 3)
        self.assertFalse(result["can_execute"])
        self.assertTrue(result["shadow_only"])
        self.assertTrue(SHADOW_ONLY)
        self.assertFalse(PRODUCTION_AUTHORITY)
        self.assertFalse(CAPITAL_AUTHORITY)

    def test_bad_opponent_data_stays_unavailable_even_with_market(self):
        item = _matched()
        del item["matchup"]["opponent_adjusted"]["away"]["adjusted_def_rating"]
        result = GameScriptShadowGate().run(combined=item, run_id="invalid-fixture")
        self.assertEqual(result["status"], "SCRIPT_UNAVAILABLE")
        self.assertEqual(result["script_basis"], "UNAVAILABLE")
        self.assertIsNone(result["unconditional_prob"])


if __name__ == "__main__":
    unittest.main()
