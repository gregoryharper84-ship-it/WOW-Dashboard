"""
gate_engine/tests/test_wow_v16_acceptance.py
WOW v16 Skills Pack — 25 Acceptance Tests

Maps directly to the 25 acceptance test scenarios that define "done" for the
WOW v16 Clean Core Skills Pack orchestration layer.  All tests are deterministic
(no live API calls, no app.py imports).

AT-1  Slate purge before modeling (missing event date)
AT-2  Source conflict → no READY label
AT-3  L5/L10 divergence >20% triggers outlier isolation
AT-4  Role-dependent player requires matching role-split ledger
AT-5  Coin-flip MORE evaluation automatically assesses LESS
AT-6  WNBA teammate OUT/GTD → role-amplification flag
AT-7  Operator-supplied / screenshot price caps at WATCH
AT-8  Kalshi price age > 10 min → DATA_UNOBTAINABLE
AT-9  Empty Kalshi orderbook → DATA_UNOBTAINABLE
AT-10 Kalshi market closed → REJECT_BAD_RULES
AT-11 Kalshi sports INVENTORY_EMPTY → immediate stop
AT-12 can_execute is always false in every result
AT-13 Bare LLP_PLAYABLE_LIMIT_ONLY normalizes to LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN
AT-14 CHI NHIGH station → KMDW (never KORD)
AT-15 MIA NHIGH station → KMIA (never PBI / KPBI)
AT-16 LA  NHIGH station → KLAX (never BUR / KBUR)
AT-17 Gaussian weather brackets normalize between 0.97 and 1.03
AT-18 Four-market Kalshi sports combo hard-rejects during Reliability Freeze
AT-19 Duplicate same-event same-side entries count as one observation
AT-20 Missing joint probability / combo breakeven → COMBO_EV_UNOBTAINABLE / REJECT_BAD_STRUCTURE
AT-21 QA auditor recomputes edge and catches arithmetic mismatch
AT-22 Lowest-ceiling propagation prevents downstream READY from overriding upstream HOLD
AT-23 Bankroll manager: no allocation when capital lane is blocked
AT-24 Sports psychology cannot exceed low-weight adjustment cap (±3%)
AT-25 Referee / umpire skill: no adjustment when assignment is unconfirmed
"""
from __future__ import annotations

import sys
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from skills.contracts import (
    SkillResult, SkillLabel, lower_ceiling,
    DRY_RUN_LABEL, BARE_LABEL_FORBIDDEN, label_severity,
)
from skills.orchestrator import SkillOrchestrator
from skills.adapters.sports_research   import SportsResearchAdapter
from skills.adapters.market_odds       import MarketOddsAdapter
from skills.adapters.kalshi_contract   import KalshiContractAdapter
from skills.adapters.player_prop       import PlayerPropAdapter
from skills.adapters.wnba_specialist   import WnbaSpecialistAdapter
from skills.adapters.probability_ev    import ProbabilityEvAdapter
from skills.adapters.correlation_slip  import CorrelationSlipAdapter
from skills.adapters.weather_intel     import WeatherIntelAdapter, resolve_nhigh_station, normalize_brackets
from skills.adapters.bankroll_risk     import BankrollRiskAdapter, RELIABILITY_FREEZE_MAX_UNITS
from skills.adapters.qa_auditor        import QaAuditorAdapter
from skills.adapters.sports_psychology import SportsPsychologyAdapter, LOW_WEIGHT_CAP_ABS
from skills.adapters.referee_umpire    import RefereeUmpireAdapter


class TestAT01_SlatePurge(unittest.TestCase):
    """AT-1: Missing event date triggers slate purge before modeling."""

    def test_missing_event_date_triggers_purge(self):
        adapter = SportsResearchAdapter()
        result = adapter.run({"target_date": "2026-07-14"})
        self.assertEqual(result.label, SkillLabel.REJECT_DATA_QUALITY.value)
        codes = [b["code"] for b in result.blockers]
        self.assertTrue(any("SLATE_PURGE" in c for c in codes))
        self.assertFalse(result.can_execute)

    def test_present_event_date_does_not_purge(self):
        adapter = SportsResearchAdapter()
        result = adapter.run({"event_date": "2026-07-14", "target_date": "2026-07-14"})
        self.assertNotEqual(result.label, SkillLabel.REJECT_DATA_QUALITY.value)
        self.assertFalse(result.can_execute)


class TestAT02_SourceConflict(unittest.TestCase):
    """AT-2: Conflicting current and stale season averages → source conflict, no READY."""

    def test_conflict_produces_no_ready(self):
        adapter = SportsResearchAdapter()
        ctx = {
            "event_date": "2026-07-14", "target_date": "2026-07-14",
            "current_season_avg": 25.5, "stale_season_avg": 22.0,
        }
        result = adapter.run(ctx)
        self.assertNotEqual(result.label, SkillLabel.READY.value)
        self.assertGreater(len(result.conflicts), 0)
        self.assertFalse(result.can_execute)

    def test_matching_averages_no_conflict(self):
        adapter = SportsResearchAdapter()
        ctx = {
            "event_date": "2026-07-14", "target_date": "2026-07-14",
            "current_season_avg": 25.5, "stale_season_avg": 25.5,
        }
        result = adapter.run(ctx)
        self.assertEqual(len(result.conflicts), 0)
        self.assertFalse(result.can_execute)


class TestAT03_L5L10Divergence(unittest.TestCase):
    """AT-3: L10/L5 divergence >20% triggers outlier isolation and L9 recomputation."""

    def test_divergence_over_threshold_flags_outlier(self):
        adapter = PlayerPropAdapter()
        result = adapter.run({"l10_avg": 20.0, "l5_avg": 25.0, "l9_avg": 19.5})
        calcs = {c["op"]: c for c in result.calculations}
        self.assertIn("l5_l10_divergence", calcs)
        self.assertTrue(calcs["l5_l10_divergence"]["flagged"])
        self.assertTrue(any("outlier" in str(f).lower() for f in result.findings))
        self.assertFalse(result.can_execute)

    def test_divergence_below_threshold_no_flag(self):
        adapter = PlayerPropAdapter()
        result = adapter.run({"l10_avg": 20.0, "l5_avg": 21.5})
        calcs = {c["op"]: c for c in result.calculations}
        if "l5_l10_divergence" in calcs:
            self.assertFalse(calcs["l5_l10_divergence"]["flagged"])
        self.assertFalse(result.can_execute)


class TestAT04_RoleDependentLedger(unittest.TestCase):
    """AT-4: Role-dependent player uses matching role-split ledger."""

    def test_role_dependent_without_ledger_blocked(self):
        adapter = PlayerPropAdapter()
        result = adapter.run({"is_role_dependent": True})
        codes = [b["code"] for b in result.blockers]
        self.assertIn("MISSING_ROLE_SPLIT_LEDGER", codes)
        self.assertFalse(result.can_execute)

    def test_role_dependent_with_ledger_passes(self):
        adapter = PlayerPropAdapter()
        result = adapter.run({"is_role_dependent": True,
                              "role_split_ledger": {"starter_only": True}})
        self.assertNotIn("MISSING_ROLE_SPLIT_LEDGER",
                         [b["code"] for b in result.blockers])
        self.assertTrue(any(f.get("role_split_applied") for f in result.findings))
        self.assertFalse(result.can_execute)


class TestAT05_CoinFlipBothSides(unittest.TestCase):
    """AT-5: Gate 3 coin-flip MORE evaluation automatically assesses LESS."""

    def test_coinflip_more_assesses_less(self):
        adapter = ProbabilityEvAdapter()
        ctx = {
            "model_probability": 0.51,
            "no_vig_probability": 0.50,
            "direction": "MORE",
            "gate3_evaluate_both_sides": True,
        }
        result = adapter.run(ctx)
        calcs = {c["op"]: c for c in result.calculations}
        self.assertIn("coinflip_both_sides_assessment", calcs)
        self.assertTrue(calcs["coinflip_both_sides_assessment"]["is_coinflip_more"])
        self.assertFalse(result.can_execute)

    def test_strong_more_does_not_flag_coinflip(self):
        adapter = ProbabilityEvAdapter()
        ctx = {"model_probability": 0.65, "no_vig_probability": 0.50, "direction": "MORE"}
        result = adapter.run(ctx)
        self.assertNotIn("REJECT_COINFLIP", [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)


class TestAT06_WnbaTeammateAmplification(unittest.TestCase):
    """AT-6: WNBA primary teammate OUT/GTD creates role-amplification flag."""

    def test_teammate_out_creates_flag(self):
        for status in ("OUT", "GTD", "DOUBTFUL"):
            with self.subTest(status=status):
                adapter = WnbaSpecialistAdapter()
                result = adapter.run({"player_name": "Player A",
                                      "primary_teammate_status": status})
                self.assertTrue(any(f.get("role_amplification") for f in result.findings),
                                f"Expected role_amplification for status={status!r}")
                self.assertFalse(result.can_execute)

    def test_teammate_active_no_flag(self):
        adapter = WnbaSpecialistAdapter()
        result = adapter.run({"player_name": "Player A",
                              "primary_teammate_status": "ACTIVE"})
        self.assertFalse(any(f.get("role_amplification") for f in result.findings))
        self.assertFalse(result.can_execute)


class TestAT07_OperatorSuppliedPriceCap(unittest.TestCase):
    """AT-7: User screenshot price remains operator_supplied and caps at WATCH."""

    def test_screenshot_source_caps_at_watch(self):
        adapter = MarketOddsAdapter()
        result = adapter.run({"odds_source_type": "screenshot",
                              "home_american_odds": -110, "away_american_odds": -110})
        self.assertEqual(result.label, SkillLabel.WATCH.value)
        self.assertFalse(result.can_execute)

    def test_direct_source_not_capped(self):
        adapter = MarketOddsAdapter()
        result = adapter.run({"odds_source_type": "direct",
                              "home_american_odds": -110, "away_american_odds": -110})
        self.assertNotEqual(result.label, SkillLabel.WATCH.value)
        self.assertFalse(result.can_execute)


class TestAT08_KalshiStalePriceUnobtainable(unittest.TestCase):
    """AT-8: Kalshi price age 11 minutes returns DATA_UNOBTAINABLE."""

    def test_11min_price_unobtainable(self):
        adapter = KalshiContractAdapter()
        result = adapter.run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_price_age_seconds": 660,
            "kalshi_orderbook": {"yes_bids": [0.55], "no_bids": [0.45]},
        })
        self.assertEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)

    def test_9min_price_not_stale(self):
        adapter = KalshiContractAdapter()
        result = adapter.run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_price_age_seconds": 540,
            "kalshi_orderbook": {"yes_bids": [0.55], "no_bids": [0.45]},
        })
        self.assertNotEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)


class TestAT09_KalshiEmptyOrderbook(unittest.TestCase):
    """AT-9: Empty Kalshi orderbook returns DATA_UNOBTAINABLE."""

    def test_empty_orderbook_unobtainable(self):
        adapter = KalshiContractAdapter()
        result = adapter.run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_orderbook": {"yes_bids": [], "no_bids": []},
        })
        self.assertEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)

    def test_populated_orderbook_not_unobtainable(self):
        adapter = KalshiContractAdapter()
        result = adapter.run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]},
        })
        self.assertNotEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)


class TestAT10_KalshiClosedMarket(unittest.TestCase):
    """AT-10: Kalshi market closed returns REJECT_BAD_RULES."""

    def test_closed_market_reject_bad_rules(self):
        for status in ("closed", "settled", "finalized"):
            with self.subTest(status=status):
                adapter = KalshiContractAdapter()
                result = adapter.run({
                    "kalshi_inventory_health": "INVENTORY_READY",
                    "kalshi_market_status": status,
                    "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]},
                })
                self.assertEqual(result.label, SkillLabel.REJECT_BAD_RULES.value)
                self.assertFalse(result.can_execute)

    def test_open_market_not_rejected(self):
        adapter = KalshiContractAdapter()
        result = adapter.run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_market_status": "open",
            "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]},
        })
        self.assertNotEqual(result.label, SkillLabel.REJECT_BAD_RULES.value)
        self.assertFalse(result.can_execute)


class TestAT11_KalshiInventoryEmptyStop(unittest.TestCase):
    """AT-11: Kalshi sports INVENTORY_EMPTY causes immediate stop and no scan."""

    def test_inventory_empty_stops_scan(self):
        adapter = KalshiContractAdapter()
        result = adapter.run({"kalshi_inventory_health": "INVENTORY_EMPTY",
                              "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]}})
        self.assertEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertIn("KALSHI_INVENTORY_EMPTY",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_orchestrator_stops_on_inventory_empty(self):
        orch = SkillOrchestrator()
        result = orch.run({"market_type": "kalshi_sports",
                           "kalshi_inventory_health": "INVENTORY_EMPTY"})
        self.assertTrue(result["stopped_early"])
        self.assertEqual(result["stop_reason"], "KALSHI_INVENTORY_EMPTY")
        self.assertFalse(result["can_execute"])


class TestAT12_CanExecuteAlwaysFalse(unittest.TestCase):
    """AT-12: can_execute is false in every result — cannot be bypassed."""

    def test_contracts_invariant_cannot_be_bypassed(self):
        r = SkillResult(
            skill_id="test", skill_version="1.0.0",
            inputs_used={}, sources=[], findings=[], blockers=[],
            label=SkillLabel.READY.value, confidence=0.5,
            can_execute=True,
        )
        self.assertFalse(r.can_execute)
        self.assertFalse(r.to_dict()["can_execute"])

    def test_all_adapters_return_can_execute_false(self):
        from skills.adapters import ADAPTER_MAP
        spot_checks = {
            "wow.kalshi-contract-intelligence": {
                "kalshi_inventory_health": "INVENTORY_READY",
                "kalshi_orderbook": {"yes_bids": [0.6], "no_bids": [0.4]},
            },
            "wow.weather-intelligence":     {"weather_city": "CHI", "weather_station": "KMDW"},
            "wow.sports-research-analyst":   {"event_date": "2026-07-14", "target_date": "2026-07-14"},
            "wow.market-odds-intelligence":  {"home_american_odds": -110, "away_american_odds": -110},
            "wow.bankroll-risk-manager":     {"upstream_final_label": "READY", "kelly_fraction": 0.1},
            "wow.qa-hallucination-auditor":  {"upstream_skill_results": []},
            "wow.sports-psychology-context": {"psychology_adjustment": 0.01},
            "wow.referee-umpire-tendency":   {"ref_assignment_confirmed": False},
        }
        for skill_id, ctx in spot_checks.items():
            with self.subTest(skill_id=skill_id):
                cls = ADAPTER_MAP.get(skill_id)
                if cls is None:
                    self.skipTest(f"Adapter {skill_id!r} not in map")
                result = cls().run(ctx)
                self.assertFalse(result.can_execute)
                self.assertFalse(result.to_dict()["can_execute"])

    def test_orchestrator_result_can_execute_false(self):
        for market_type in ("player_prop", "kalshi_weather", "lottery", "team_winner"):
            with self.subTest(market_type=market_type):
                result = SkillOrchestrator().run({"market_type": market_type})
                self.assertFalse(result["can_execute"])


class TestAT13_BareLabelNormalization(unittest.TestCase):
    """AT-13: Bare LLP_PLAYABLE_LIMIT_ONLY normalizes to LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN."""

    def test_bare_label_normalizes(self):
        r = SkillResult(
            skill_id="test", skill_version="1.0.0",
            inputs_used={}, sources=[], findings=[], blockers=[],
            label=BARE_LABEL_FORBIDDEN, confidence=0.5,
        )
        self.assertEqual(r.label, DRY_RUN_LABEL)
        self.assertNotEqual(r.label, BARE_LABEL_FORBIDDEN)

    def test_dry_run_label_unchanged(self):
        r = SkillResult(
            skill_id="test", skill_version="1.0.0",
            inputs_used={}, sources=[], findings=[], blockers=[],
            label=DRY_RUN_LABEL, confidence=0.5,
        )
        self.assertEqual(r.label, DRY_RUN_LABEL)

    def test_kalshi_adapter_emits_dry_run_not_bare(self):
        adapter = KalshiContractAdapter()
        result = adapter.run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]},
        })
        self.assertNotEqual(result.label, BARE_LABEL_FORBIDDEN)
        self.assertFalse(result.can_execute)


class TestAT14_ChiNhighStation(unittest.TestCase):
    """AT-14: CHI NHIGH maps to KMDW, never KORD."""

    def test_chi_kmdw_accepted(self):
        adapter = WeatherIntelAdapter()
        result = adapter.run({"weather_city": "CHI", "weather_station": "KMDW"})
        self.assertNotIn("WRONG_NHIGH_STATION",
                         [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_chi_kord_rejected(self):
        adapter = WeatherIntelAdapter()
        result = adapter.run({"weather_city": "CHI", "weather_station": "KORD"})
        codes = [b["code"] for b in result.blockers]
        self.assertTrue(any("NHIGH" in c for c in codes))
        self.assertFalse(result.can_execute)

    def test_resolve_chi_returns_kmdw(self):
        self.assertEqual(resolve_nhigh_station("CHI"), "KMDW")
        self.assertEqual(resolve_nhigh_station("CHICAGO"), "KMDW")
        self.assertNotEqual(resolve_nhigh_station("CHI"), "KORD")


class TestAT15_MiaNhighStation(unittest.TestCase):
    """AT-15: MIA NHIGH maps to KMIA, never PBI / KPBI."""

    def test_mia_kmia_accepted(self):
        adapter = WeatherIntelAdapter()
        result = adapter.run({"weather_city": "MIA", "weather_station": "KMIA"})
        self.assertNotIn("WRONG_NHIGH_STATION",
                         [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_mia_pbi_rejected(self):
        for stn in ("PBI", "KPBI"):
            with self.subTest(station=stn):
                adapter = WeatherIntelAdapter()
                result = adapter.run({"weather_city": "MIA", "weather_station": stn})
                codes = [b["code"] for b in result.blockers]
                self.assertTrue(any("NHIGH" in c for c in codes))
                self.assertFalse(result.can_execute)

    def test_resolve_mia_returns_kmia(self):
        self.assertEqual(resolve_nhigh_station("MIA"), "KMIA")
        self.assertNotEqual(resolve_nhigh_station("MIA"), "KPBI")


class TestAT16_LaNhighStation(unittest.TestCase):
    """AT-16: LA NHIGH maps to KLAX, never BUR / KBUR."""

    def test_la_klax_accepted(self):
        adapter = WeatherIntelAdapter()
        result = adapter.run({"weather_city": "LA", "weather_station": "KLAX"})
        self.assertNotIn("WRONG_NHIGH_STATION",
                         [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_la_bur_rejected(self):
        for stn in ("BUR", "KBUR"):
            with self.subTest(station=stn):
                adapter = WeatherIntelAdapter()
                result = adapter.run({"weather_city": "LA", "weather_station": stn})
                codes = [b["code"] for b in result.blockers]
                self.assertTrue(any("NHIGH" in c for c in codes))
                self.assertFalse(result.can_execute)

    def test_resolve_la_returns_klax(self):
        self.assertEqual(resolve_nhigh_station("LA"), "KLAX")
        self.assertNotEqual(resolve_nhigh_station("LA"), "KBUR")


class TestAT17_GaussianBracketNormalization(unittest.TestCase):
    """AT-17: Gaussian weather brackets normalize between 0.97 and 1.03."""

    def test_brackets_sum_in_range(self):
        adapter = WeatherIntelAdapter()
        ctx = {"weather_city": "NYC", "weather_station": "KNYC",
               "weather_threshold_f": 72.0, "weather_sigma_f": 3.5}
        result = adapter.run(ctx)
        bracket_finding = next(
            (f for f in result.findings if "gaussian_brackets" in f), None)
        self.assertIsNotNone(bracket_finding)
        total = bracket_finding["bracket_sum"]
        self.assertGreaterEqual(total, 0.97)
        self.assertLessEqual(total, 1.03)
        self.assertFalse(result.can_execute)

    def test_normalize_brackets_rescales_when_needed(self):
        probs = [0.1] * 11
        normalized = normalize_brackets(probs)
        self.assertAlmostEqual(sum(normalized), 1.0, places=5)

    def test_brackets_within_range_unchanged(self):
        probs = [0.1, 0.15, 0.20, 0.10, 0.15, 0.15, 0.15]
        normalized = normalize_brackets(probs)
        self.assertAlmostEqual(sum(normalized), sum(probs), places=5)


class TestAT18_ReliabilityFreezeComboReject(unittest.TestCase):
    """AT-18: Four-market Kalshi sports combo hard-rejects during Reliability Freeze."""

    def test_four_market_combo_rejected_in_freeze(self):
        result = SkillOrchestrator().run({
            "market_type": "kalshi_sports",
            "reliability_freeze": True,
            "kalshi_combo_markets": ["m1", "m2", "m3", "m4"],
        })
        self.assertTrue(result["stopped_early"])
        self.assertIn("RELIABILITY_FREEZE_COMBO_HARD_REJECT",
                      [b["code"] for b in result["blockers"]])
        self.assertEqual(result["final_label"], SkillLabel.REJECT_BAD_RULES.value)
        self.assertFalse(result["can_execute"])

    def test_three_market_combo_not_rejected_in_freeze(self):
        result = SkillOrchestrator().run({
            "market_type": "kalshi_sports",
            "reliability_freeze": True,
            "kalshi_combo_markets": ["m1", "m2", "m3"],
            "kalshi_inventory_health": "INVENTORY_READY",
        })
        self.assertNotIn("RELIABILITY_FREEZE_COMBO_HARD_REJECT",
                         [b["code"] for b in result["blockers"]])
        self.assertFalse(result["can_execute"])

    def test_four_market_combo_ok_without_freeze(self):
        result = SkillOrchestrator().run({
            "market_type": "kalshi_sports",
            "reliability_freeze": False,
            "kalshi_combo_markets": ["m1", "m2", "m3", "m4"],
            "kalshi_inventory_health": "INVENTORY_READY",
        })
        self.assertNotIn("RELIABILITY_FREEZE_COMBO_HARD_REJECT",
                         [b["code"] for b in result["blockers"]])
        self.assertFalse(result["can_execute"])


class TestAT19_DuplicateObservations(unittest.TestCase):
    """AT-19: Duplicate same-event same-side entries count as one observation."""

    def test_duplicate_entries_collapsed(self):
        adapter = CorrelationSlipAdapter()
        legs = [
            {"event_id": "game1", "side": "MORE", "player": "P1", "prop_type": "points"},
            {"event_id": "game1", "side": "MORE", "player": "P1", "prop_type": "points"},
            {"event_id": "game2", "side": "MORE", "player": "P2", "prop_type": "points"},
        ]
        result = adapter.run({"slip_legs": legs,
                              "joint_probability": 0.35, "combo_breakeven_prob": 0.333})
        summary = next((f for f in result.findings if "duplicate_count" in f), None)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["duplicate_count"], 1)
        self.assertEqual(summary["unique_legs"], 2)
        self.assertFalse(result.can_execute)


class TestAT20_MissingJointProbability(unittest.TestCase):
    """AT-20: Missing joint probability / combo breakeven → COMBO_EV_UNOBTAINABLE / REJECT_BAD_STRUCTURE."""

    def test_missing_joint_probability_unobtainable(self):
        adapter = CorrelationSlipAdapter()
        legs = [
            {"event_id": "g1", "side": "MORE", "player": "A", "prop_type": "pts"},
            {"event_id": "g2", "side": "MORE", "player": "B", "prop_type": "pts"},
        ]
        result = adapter.run({"slip_legs": legs})
        codes = [b["code"] for b in result.blockers]
        self.assertTrue("COMBO_EV_UNOBTAINABLE" in codes or "REJECT_BAD_STRUCTURE" in codes,
                        f"Expected COMBO_EV_UNOBTAINABLE or REJECT_BAD_STRUCTURE; got {codes}")
        self.assertFalse(result.can_execute)

    def test_missing_combo_breakeven_bad_structure(self):
        adapter = CorrelationSlipAdapter()
        legs = [
            {"event_id": "g1", "side": "MORE", "player": "A", "prop_type": "pts"},
            {"event_id": "g2", "side": "MORE", "player": "B", "prop_type": "pts"},
        ]
        result = adapter.run({"slip_legs": legs, "joint_probability": 0.35})
        self.assertIn("REJECT_BAD_STRUCTURE",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)


class TestAT21_QaArithmeticRecompute(unittest.TestCase):
    """AT-21: QA auditor recomputes edge and catches arithmetic mismatch."""

    def test_arithmetic_mismatch_caught(self):
        adapter = QaAuditorAdapter()
        ctx = {"upstream_skill_results": [{
            "skill_id": "wow.probability-ev-auditor",
            "can_execute": False,
            "calculations": [{
                "op": "ev",
                "model_prob": 0.60,
                "no_vig_prob": 0.50,
                "ev": 0.30,
            }],
        }]}
        result = adapter.run(ctx)
        ev_conflicts = [c for c in result.conflicts if c.get("type") == "EV_ARITHMETIC_MISMATCH"]
        self.assertGreater(len(ev_conflicts), 0)
        self.assertEqual(result.label, SkillLabel.REJECT_DATA_QUALITY.value)
        self.assertFalse(result.can_execute)

    def test_correct_arithmetic_no_conflict(self):
        adapter = QaAuditorAdapter()
        ctx = {"upstream_skill_results": [{
            "skill_id": "wow.probability-ev-auditor",
            "can_execute": False,
            "calculations": [{
                "op": "ev",
                "model_prob": 0.60,
                "no_vig_prob": 0.50,
                "ev": 0.20,
            }],
        }]}
        result = adapter.run(ctx)
        ev_conflicts = [c for c in result.conflicts if c.get("type") == "EV_ARITHMETIC_MISMATCH"]
        self.assertEqual(len(ev_conflicts), 0)
        self.assertFalse(result.can_execute)


class TestAT22_LowestCeilingPropagation(unittest.TestCase):
    """AT-22: Lowest-ceiling propagation prevents downstream READY from overriding upstream HOLD."""

    def test_upstream_hold_survives_downstream_ready(self):
        ceiling = SkillLabel.READY.value
        ceiling = lower_ceiling(ceiling, SkillLabel.HOLD.value)
        self.assertEqual(ceiling, SkillLabel.HOLD.value)
        ceiling = lower_ceiling(ceiling, SkillLabel.READY.value)
        self.assertEqual(ceiling, SkillLabel.HOLD.value)

    def test_label_severity_order_enforced(self):
        order = [
            SkillLabel.READY, SkillLabel.WATCH, SkillLabel.SCOUT, SkillLabel.HOLD,
            SkillLabel.REJECT_BAD_RULES, SkillLabel.REJECT_DATA_QUALITY,
            SkillLabel.DATA_UNOBTAINABLE,
        ]
        for i in range(len(order) - 1):
            self.assertLess(label_severity(order[i].value),
                            label_severity(order[i + 1].value))

    def test_orchestrator_propagates_lowest_ceiling(self):
        orch = SkillOrchestrator()
        result = orch.run({"market_type": "player_prop"})
        self.assertGreaterEqual(
            label_severity(result["final_label"]),
            label_severity(SkillLabel.REJECT_DATA_QUALITY.value),
        )
        self.assertFalse(result["can_execute"])


class TestAT23_NoAllocationBlockedCapital(unittest.TestCase):
    """AT-23: Bankroll manager returns no allocation when capital lane is blocked."""

    def test_capital_lane_blocked_no_allocation(self):
        adapter = BankrollRiskAdapter()
        result = adapter.run({"capital_lane_blocked": True, "kelly_fraction": 0.25})
        alloc = next((f for f in result.findings if "allocation" in f), None)
        self.assertIsNotNone(alloc)
        self.assertEqual(alloc["allocation"], 0.0)
        self.assertIn("CAPITAL_LANE_BLOCKED",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_upstream_hold_blocks_allocation(self):
        for lbl in (SkillLabel.HOLD, SkillLabel.REJECT_BAD_RULES,
                    SkillLabel.DATA_UNOBTAINABLE):
            with self.subTest(label=lbl.value):
                adapter = BankrollRiskAdapter()
                result = adapter.run({"upstream_final_label": lbl.value,
                                      "kelly_fraction": 0.25})
                alloc = next((f for f in result.findings if "allocation" in f), None)
                self.assertIsNotNone(alloc)
                self.assertEqual(alloc["allocation"], 0.0)
                self.assertFalse(result.can_execute)

    def test_ready_upstream_allows_allocation(self):
        adapter = BankrollRiskAdapter()
        result = adapter.run({"upstream_final_label": SkillLabel.READY.value,
                              "kelly_fraction": 0.20, "capital_lane_blocked": False})
        alloc = next((f for f in result.findings if "allocation_units" in f), None)
        self.assertIsNotNone(alloc)
        self.assertGreater(alloc["allocation_units"], 0.0)
        self.assertFalse(result.can_execute)


class TestAT24_PsychologyLowWeightCap(unittest.TestCase):
    """AT-24: Sports psychology cannot exceed low-weight adjustment cap (±3%)."""

    def test_adjustment_exceeds_cap_rejected(self):
        adapter = SportsPsychologyAdapter()
        result = adapter.run({"psychology_adjustment": 0.10})
        self.assertIn("PSYCHOLOGY_ADJUSTMENT_EXCEEDS_CAP",
                      [b["code"] for b in result.blockers])
        self.assertEqual(result.label, SkillLabel.REJECT_BAD_RULES.value)
        self.assertFalse(result.can_execute)

    def test_adjustment_within_cap_accepted(self):
        adapter = SportsPsychologyAdapter()
        result = adapter.run({"psychology_adjustment": 0.02})
        self.assertNotIn("PSYCHOLOGY_ADJUSTMENT_EXCEEDS_CAP",
                         [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_unsupported_mental_state_banned(self):
        adapter = SportsPsychologyAdapter()
        result = adapter.run({"psychology_adjustment": 0.01,
                              "mental_state_claim": "player is feeling confident today"})
        self.assertIn("PSYCHOLOGY_UNSUPPORTED_MENTAL_STATE",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_cap_constant_is_3pct(self):
        self.assertAlmostEqual(LOW_WEIGHT_CAP_ABS, 0.03)


class TestAT25_RefereeUnconfirmedNoAdjustment(unittest.TestCase):
    """AT-25: Ref/umpire skill returns no adjustment when assignment is unconfirmed."""

    def test_unconfirmed_assignment_zero_adjustment(self):
        adapter = RefereeUmpireAdapter()
        result = adapter.run({"ref_assignment_confirmed": False,
                              "official_name": "Unknown Ref"})
        self.assertEqual(result.findings[0].get("adjustment_applied", -1), 0.0)
        self.assertIn("REF_ASSIGNMENT_UNCONFIRMED",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_confirmed_assignment_can_apply_tendency(self):
        adapter = RefereeUmpireAdapter()
        result = adapter.run({"ref_assignment_confirmed": True,
                              "official_name": "Ref A",
                              "tendency_data": {"prob_adjustment": 0.02}})
        self.assertNotIn("REF_ASSIGNMENT_UNCONFIRMED",
                         [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)


if __name__ == "__main__":
    unittest.main(verbosity=2)
