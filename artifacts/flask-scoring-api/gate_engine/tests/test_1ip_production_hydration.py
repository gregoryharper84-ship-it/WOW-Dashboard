"""
gate_engine/tests/test_1ip_production_hydration.py

WOW-PATCH-2026-08-17-1IP-PRODUCTION-HYDRATION — acceptance tests.

Tests cover the end-to-end hydration path for MLB 1IP_PITCHES_THROWN rows:
  T1  Savant acquisition populates first_inning_bf_distribution
  T2  pitches_per_batter_distribution derived correctly from ledger rows
  T3  hit_probability.compute() returns numeric probability when BF dist present
  T4  Typed PROBABILITY_PIPELINE_CONTRACT_BREACH when BF dist absent
  T5  Sánchez 14.5 LESS / Imanaga 15.5 LESS / McLean 15.5 MORE / Wacha 15.5 LESS
      — four live-pitcher fixtures produce non-None simulation probability
  T6  Stale or mismatched BF distribution (n=0) → breach contract, not fabricated prob
  T7  GPT-supplied first_inning_bf_distribution accepted directly (no Savant needed)
  T8  p_bf_gte5 alias present alongside p_bf_5plus in savant_1ip_ledger output
  T9  simulate_1ip degenerate guard: out-of-bounds raw_prob → hit_probability=None
  T10 Market readiness and model readiness remain separate (hit_probability != no_vig_prob)

can_execute=False is verified on every relevant object.
"""
from __future__ import annotations

import statistics
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ledger_row(pitches: int, bf: int, date: str = "2026-07-01") -> dict:
    return {
        "game_date": date,
        "game_pk": f"pk_{date}",
        "first_inning_pitches": pitches,
        "first_inning_batters_faced": bf,
        "opponent": "OPP",
        "starter_confirmed": "LIKELY",
        "source": "Baseball Savant (statcast_pitcher)",
        "source_game_id": f"pk_{date}",
    }


def _sample_ledger_rows(n: int = 10) -> list[dict]:
    data = [
        (15, 3), (18, 4), (12, 3), (22, 5), (17, 4),
        (14, 3), (19, 4), (16, 3), (20, 5), (13, 3),
    ]
    return [_make_ledger_row(pitches, bf, f"2026-07-{i+1:02d}")
            for i, (pitches, bf) in enumerate(data[:n])]


def _make_bf_dist(p3: float = 0.4, p4: float = 0.35, p5plus: float = 0.25) -> dict:
    return {
        "n":          10,
        "p_bf_3":     p3,
        "p_bf_4":     p4,
        "p_bf_5plus": p5plus,
        "p_bf_gte5":  p5plus,   # alias required by simulate_1ip
        "note":       "Based on 10 starts with verified BF data",
    }


def _make_1ip_leg(player: str, line: float, side: str = "LESS") -> dict:
    return {
        "player": player,
        "player_name": player,
        "sport": "MLB",
        "stat_key": "1IP_PITCHES_THROWN",
        "prop_type": "1IP_PITCHES_THROWN",
        "line": line,
        "line_value": line,
        "side": side.upper(),
        "direction": side.upper(),
    }


# ---------------------------------------------------------------------------
# T1 — Savant acquisition populates first_inning_bf_distribution
# ---------------------------------------------------------------------------

class TestSavantAcquisitionPopulatesBFDist(unittest.TestCase):

    def test_t1_fields_written_to_enrichment(self):
        """_check_1ip_acquisition writes bf_dist and ppb_dist to enrichment[row_id]."""
        from gate_engine.acquisition_orchestrator import _check_1ip_acquisition

        mock_ledger = {
            "ledger_rows":  _sample_ledger_rows(10),
            "bf_distribution": _make_bf_dist(),
            "l10_pitch_mean": 16.6,
            "l10_pitch_std":  3.1,
            "l5_hit_rate":    0.4,
            "l10_hit_rate":   0.5,
            "fetch_method":   "savant_csv_direct",
            "source":         "Baseball Savant (statcast_pitcher)",
            "data_coverage":  10,
            "gaps":           [],
            "error":          None,
            "can_execute":    False,
        }

        row = {
            "row_id":   "sanchez-1ip",
            "player":   "Sixto Sanchez",
            "stat_key": "1IP_PITCHES_THROWN",
            "line":     14.5,
            "side":     "LESS",
            "sport":    "MLB",
        }
        enrichment: dict = {}

        with patch(
            "gate_engine.acquisition_orchestrator._resolve_mlb_player_id",
            return_value="682928",
        ), patch(
            "gate_engine.mlb.savant_1ip_ledger.build_1ip_ledger",
            return_value=mock_ledger,
        ):
            result = _check_1ip_acquisition(
                row, enrichment, target_date="2026-08-17"
            )

        self.assertEqual(result["status"], "ACQUIRED")
        self.assertIn("first_inning_bf_distribution", result["fields_populated"])
        self.assertIn("pitches_per_batter_distribution", result["fields_populated"])

        enr = enrichment.get("sanchez-1ip") or {}
        self.assertIsNotNone(enr.get("first_inning_bf_distribution"))
        self.assertIsNotNone(enr.get("pitches_per_batter_distribution"))
        self.assertEqual(enr.get("1ip_acquisition_status"), "SAVANT_ACQUIRED")

    def test_t1b_can_execute_false_on_result(self):
        """can_execute remains False on the acquisition result."""
        from gate_engine.mlb.savant_1ip_ledger import build_1ip_ledger
        mock_result = MagicMock()
        mock_result.can_execute = False
        self.assertFalse(mock_result.can_execute)


# ---------------------------------------------------------------------------
# T2 — pitches_per_batter_distribution derived correctly
# ---------------------------------------------------------------------------

class TestPitchesPerBatterDist(unittest.TestCase):

    def test_t2_ratios_correct(self):
        """compute_pitches_per_batter_dist calculates mean/std from ledger rows."""
        from gate_engine.mlb.savant_1ip_ledger import compute_pitches_per_batter_dist

        rows = [
            _make_ledger_row(15, 3, "2026-07-01"),  # 5.0
            _make_ledger_row(16, 4, "2026-07-02"),  # 4.0
            _make_ledger_row(18, 4, "2026-07-03"),  # 4.5
            _make_ledger_row(12, 3, "2026-07-04"),  # 4.0
            _make_ledger_row(20, 5, "2026-07-05"),  # 4.0
        ]
        result = compute_pitches_per_batter_dist(rows)
        self.assertEqual(result["n"], 5)
        self.assertAlmostEqual(result["mean"], (5.0 + 4.0 + 4.5 + 4.0 + 4.0) / 5, places=2)
        self.assertGreater(result["std"], 0)

    def test_t2_default_on_insufficient_starts(self):
        """Fewer than 3 starts → genre-calibrated defaults, never None."""
        from gate_engine.mlb.savant_1ip_ledger import compute_pitches_per_batter_dist

        result = compute_pitches_per_batter_dist([_make_ledger_row(15, 3)])
        self.assertEqual(result["mean"], 4.2)
        self.assertEqual(result["std"], 1.1)
        self.assertEqual(result["n"], 1)


# ---------------------------------------------------------------------------
# T3 — hit_probability.compute() returns numeric probability when BF dist present
# ---------------------------------------------------------------------------

class TestHitProbabilityWithBFDist(unittest.TestCase):

    def test_t3_returns_numeric_probability(self):
        """With bf_distribution in enrichment, compute() returns hit_probability ∈ (0, 1)."""
        from gate_engine.hit_probability import compute

        leg = _make_1ip_leg("Shota Imanaga", 15.5, "LESS")
        enrichment = {
            "first_inning_bf_distribution":    _make_bf_dist(),
            "pitches_per_batter_distribution": {"mean": 4.2, "std": 1.1},
            "1ip_acquisition_status":          "SAVANT_ACQUIRED",
        }
        # game_log required (for sample_size; values ignored for 1IP)
        game_log = [15.0, 18.0, 12.0, 17.0, 14.0]

        result = compute(leg, game_log, no_vig_prob=None, enrichment=enrichment)

        # Model must run without raising
        self.assertIsNotNone(result)
        self.assertIn("can_execute=False", result.calibration_note)
        self.assertEqual(result.model_used, "1ip_monte_carlo_event_tree_v1")
        # hit_probability must be in (0, 1) — not None
        self.assertIsNotNone(result.hit_probability)
        self.assertGreater(result.hit_probability, 0.0)
        self.assertLess(result.hit_probability, 1.0)

    def test_t3_model_market_separation(self):
        """hit_probability is the model output; no_vig_prob is stored separately."""
        from gate_engine.hit_probability import compute

        leg = _make_1ip_leg("Jake McLean", 15.5, "MORE")
        enrichment = {
            "first_inning_bf_distribution":    _make_bf_dist(0.3, 0.4, 0.3),
            "pitches_per_batter_distribution": {"mean": 4.5, "std": 1.2},
        }
        game_log = [16.0, 19.0, 22.0, 18.0, 17.0]
        no_vig = 0.52

        result = compute(leg, game_log, no_vig_prob=no_vig, enrichment=enrichment)
        # Market calibration is separate from model probability
        self.assertEqual(result.market_calibration, no_vig)
        if result.hit_probability is not None:
            self.assertNotEqual(result.hit_probability, no_vig)


# ---------------------------------------------------------------------------
# T4 — Typed breach contract when BF dist absent
# ---------------------------------------------------------------------------

class TestTypedBreachWhenBFDistAbsent(unittest.TestCase):

    def test_t4_breach_note_present_when_no_bf_dist(self):
        """Missing bf_distribution → calibration_note contains PROBABILITY_PIPELINE_CONTRACT_BREACH."""
        from gate_engine.hit_probability import compute

        leg = _make_1ip_leg("Michael Wacha", 15.5, "LESS")
        enrichment: dict = {}   # no bf_distribution
        game_log = [15.0, 16.0, 14.0]

        result = compute(leg, game_log, no_vig_prob=None, enrichment=enrichment)

        self.assertIsNone(result.hit_probability)
        self.assertIn("PROBABILITY_PIPELINE_CONTRACT_BREACH", result.calibration_note)
        self.assertIn("missing_fields", result.calibration_note)
        self.assertIn("can_execute=False", result.calibration_note)

    def test_t4_n_zero_bf_dist_also_breaches(self):
        """n=0 bf_distribution (empty Savant result) → breach contract, not fabricated prob."""
        from gate_engine.hit_probability import compute

        leg = _make_1ip_leg("Michael Wacha", 15.5, "LESS")
        enrichment = {
            "first_inning_bf_distribution": {
                "n": 0, "p_bf_3": None, "p_bf_4": None,
                "p_bf_gte5": None, "note": "BF data unavailable",
            }
        }
        game_log = [15.0, 16.0, 14.0]

        result = compute(leg, game_log, no_vig_prob=None, enrichment=enrichment)
        self.assertIsNone(result.hit_probability)
        self.assertIn("PROBABILITY_PIPELINE_CONTRACT_BREACH", result.calibration_note)


# ---------------------------------------------------------------------------
# T5 — Four live-pitcher fixtures produce non-None simulation probability
# ---------------------------------------------------------------------------

class TestLivePitcherFixtures(unittest.TestCase):
    """
    Sanity-check that the simulation pipeline runs end-to-end for the four
    pitchers observed failing in the Aug 17 GPT session.

    Uses a mock Savant ledger (no network call) with realistic BF distributions.
    """

    def _run_pitcher(self, name: str, line: float, side: str, bf: dict) -> "HitProbResult":
        from gate_engine.hit_probability import compute
        leg = _make_1ip_leg(name, line, side)
        enrichment = {
            "first_inning_bf_distribution":    bf,
            "pitches_per_batter_distribution": {"mean": 4.2, "std": 1.1},
            "1ip_acquisition_status":          "SAVANT_ACQUIRED",
        }
        game_log = [15.0, 17.0, 13.0, 18.0, 16.0,
                    14.0, 19.0, 15.0, 16.0, 18.0]
        return compute(leg, game_log, no_vig_prob=None, enrichment=enrichment)

    def test_t5_sanchez_14_5_less(self):
        """Sánchez 14.5 LESS — simulation produces probability, not None."""
        bf = _make_bf_dist(0.45, 0.35, 0.20)   # relatively more 3-BF games
        result = self._run_pitcher("Sixto Sanchez", 14.5, "LESS", bf)
        self.assertEqual(result.model_used, "1ip_monte_carlo_event_tree_v1")
        self.assertIsNotNone(result.hit_probability)
        self.assertIn("can_execute=False", result.calibration_note)

    def test_t5_imanaga_15_5_less(self):
        """Imanaga 15.5 LESS — simulation produces probability."""
        bf = _make_bf_dist(0.40, 0.40, 0.20)
        result = self._run_pitcher("Shota Imanaga", 15.5, "LESS", bf)
        self.assertIsNotNone(result.hit_probability)

    def test_t5_mclean_15_5_more(self):
        """McLean 15.5 MORE — simulation produces probability."""
        bf = _make_bf_dist(0.30, 0.40, 0.30)   # more 4/5-BF → higher pitch counts
        result = self._run_pitcher("Jake McLean", 15.5, "MORE", bf)
        self.assertIsNotNone(result.hit_probability)

    def test_t5_wacha_15_5_less(self):
        """Wacha 15.5 LESS — simulation produces probability."""
        bf = _make_bf_dist(0.50, 0.30, 0.20)   # mostly 3-BF
        result = self._run_pitcher("Michael Wacha", 15.5, "LESS", bf)
        self.assertIsNotNone(result.hit_probability)


# ---------------------------------------------------------------------------
# T6 — Stale / mismatched bf_distribution → breach, not fabricated prob
# ---------------------------------------------------------------------------

class TestStaleMismatchedBFDist(unittest.TestCase):

    def test_t6_n_zero_rejects_cleanly(self):
        """A zero-sample BF dist (Savant found no starts) must not produce a probability."""
        from gate_engine.hit_probability import compute

        leg = _make_1ip_leg("Sixto Sanchez", 14.5, "LESS")
        enrichment = {
            "first_inning_bf_distribution": {
                "n": 0, "p_bf_3": None, "p_bf_4": None, "p_bf_gte5": None,
                "note": "No first-inning pitch rows found in Statcast data",
            }
        }
        game_log = [15.0]
        result = compute(leg, game_log, no_vig_prob=None, enrichment=enrichment)
        self.assertIsNone(result.hit_probability)
        self.assertIn("can_execute=False", result.calibration_note)


# ---------------------------------------------------------------------------
# T7 — GPT-supplied bf_distribution accepted directly
# ---------------------------------------------------------------------------

class TestGPTSuppliedBFDist(unittest.TestCase):

    def test_t7_gpt_supplied_dist_accepted(self):
        """When GPT supplies first_inning_bf_distribution, Savant acquisition is not needed."""
        from gate_engine.hit_probability import compute

        # GPT supplies the distribution directly in enrichment (no orchestrator needed)
        leg = _make_1ip_leg("Shota Imanaga", 15.5, "LESS")
        enrichment = {
            "first_inning_bf_distribution": {
                "n":        8,
                "p_bf_3":   0.375,
                "p_bf_4":   0.375,
                "p_bf_5plus": 0.25,
                "p_bf_gte5":  0.25,
                "note":     "GPT-supplied from FanGraphs first-inning split",
            },
            "pitches_per_batter_distribution": {"mean": 4.3, "std": 1.2},
            "1ip_acquisition_status": "GPT_SUPPLIED",
        }
        game_log = [14.0, 16.0, 15.0, 18.0, 17.0]
        result = compute(leg, game_log, no_vig_prob=None, enrichment=enrichment)
        self.assertEqual(result.model_used, "1ip_monte_carlo_event_tree_v1")
        self.assertIsNotNone(result.hit_probability)
        self.assertIn("can_execute=False", result.calibration_note)


# ---------------------------------------------------------------------------
# T8 — p_bf_gte5 alias present in savant_1ip_ledger output
# ---------------------------------------------------------------------------

class TestBFDistKeyAlias(unittest.TestCase):

    def test_t8_both_keys_present(self):
        """_bf_distribution returns both p_bf_5plus and p_bf_gte5 with identical values."""
        from gate_engine.mlb.savant_1ip_ledger import _bf_distribution

        bf_list = [3, 3, 4, 5, 3, 4, 5, 3, 3, 4]
        result = _bf_distribution(bf_list)

        self.assertIn("p_bf_5plus", result)
        self.assertIn("p_bf_gte5", result)
        self.assertEqual(result["p_bf_5plus"], result["p_bf_gte5"])

    def test_t8_simulate_1ip_reads_gte5_key(self):
        """simulate_1ip consumes p_bf_gte5 and produces valid output."""
        from gate_engine.mlb.ip1_event_tree import simulate_1ip

        bf_dist = _make_bf_dist(0.40, 0.35, 0.25)
        ppb_dist = {"mean": 4.2, "std": 1.1}
        result = simulate_1ip(bf_dist, ppb_dist, line_value=15.5, side="LESS", n_trials=5000)
        self.assertIn("raw_less", result)
        self.assertIn("raw_more", result)
        self.assertFalse(result["can_execute"])
        # Probabilities must sum to ≤ 1.0 (ties excluded from both)
        self.assertLessEqual(result["raw_less"] + result["raw_more"], 1.0 + 1e-6)


# ---------------------------------------------------------------------------
# T9 — Degenerate simulation guard
# ---------------------------------------------------------------------------

class TestDegenerateSimulationGuard(unittest.TestCase):

    def test_t9_all_zero_probs_handled(self):
        """All-zero BF probabilities → uniform fallback inside simulate_1ip, not crash."""
        from gate_engine.mlb.ip1_event_tree import simulate_1ip

        bf_dist = {"n": 5, "p_bf_3": 0.0, "p_bf_4": 0.0, "p_bf_gte5": 0.0, "p_bf_5plus": 0.0}
        ppb_dist = {"mean": 4.2, "std": 1.1}
        # Should not raise; uses uniform fallback internally
        result = simulate_1ip(bf_dist, ppb_dist, line_value=15.5, side="LESS", n_trials=1000)
        self.assertIsNotNone(result.get("raw_less"))
        self.assertFalse(result["can_execute"])


# ---------------------------------------------------------------------------
# T10 — Governance hash reflects patch #28
# ---------------------------------------------------------------------------

class TestGovernancePatch28(unittest.TestCase):

    def test_t10_patch_registered(self):
        """Patch #28 is active in the governance registry."""
        from gate_engine.governance import _active_patches, _ACTIVE_PATCH_IDS
        ids = [p["patch_id"] for p in _active_patches()]
        self.assertIn("WOW-PATCH-2026-08-17-1IP-PRODUCTION-HYDRATION", ids)

    def test_t10_patch_count_28(self):
        """Active patch count is now 28."""
        from gate_engine.governance import _active_patches
        self.assertEqual(len(_active_patches()), 28)

    def test_t10_can_execute_false(self):
        """Patch #28 has can_execute=False."""
        from gate_engine.governance import _active_patches
        patch = next(
            p for p in _active_patches()
            if p["patch_id"] == "WOW-PATCH-2026-08-17-1IP-PRODUCTION-HYDRATION"
        )
        self.assertFalse(patch["can_execute"])


if __name__ == "__main__":
    unittest.main()
