"""
gate_engine/tests/test_wow_v16_invariants.py
WOW v16 Skills Pack — Invariant Unit Tests

Covers every contract invariant and governance rule that must hold across the
entire Skills Pack, independent of any specific acceptance test scenario.

Invariants covered:
  - can_execute is always False (SkillResult, every adapter, orchestrator)
  - Stale Kalshi price (>10 min, exact boundary 600 vs 601 s)
  - Empty Kalshi orderbook
  - Closed Kalshi market
  - Operator-supplied / screenshot price cannot become direct/live source
  - Bare LLP_PLAYABLE_LIMIT_ONLY normalizes to LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN
  - NHIGH station codes: CHI=KMDW, MIA=KMIA, LA=KLAX (banned: KORD, KPBI/PBI, KBUR/BUR)
  - Gaussian bracket bounds (0.97–1.03)
  - Combo size gates (Reliability Freeze 4-market hard reject)
  - Duplicate counting (same-event/same-side = one observation)
  - Lowest-ceiling propagation (lower_ceiling semantics + orchestrator enforcement)
  - Bankroll: no allocation when capital lane blocked or upstream label is restrictive
  - Psychology cap: ±3% hard limit, unsupported mental-state claims banned
  - Unconfirmed umpire: adjustment must be zero
  - Registry: 21 skills, no duplicates, validate_registry() returns no errors
  - SkillResult confidence clamping [0.0, 1.0]
  - All adapter SKILL_IDs match their registry IDs (canonical ID integrity)
"""
from __future__ import annotations

import sys
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from skills.contracts import (
    SkillResult, SkillLabel, Blocker, SourceEvidence, lower_ceiling,
    DRY_RUN_LABEL, BARE_LABEL_FORBIDDEN, label_severity,
    FRESHNESS_LIVE_PRICE, SOURCE_QUALITY_OPERATOR_SUPPLIED,
)
from skills.registry import SkillRegistry
from skills.orchestrator import SkillOrchestrator
from skills.adapters import ADAPTER_MAP
from skills.adapters.kalshi_contract   import KalshiContractAdapter
from skills.adapters.weather_intel     import (
    WeatherIntelAdapter, resolve_nhigh_station, normalize_brackets,
    NHIGH_STATION_MAP, BANNED_STATION_CODES,
)
from skills.adapters.bankroll_risk     import BankrollRiskAdapter, RELIABILITY_FREEZE_MAX_UNITS
from skills.adapters.sports_psychology import SportsPsychologyAdapter, LOW_WEIGHT_CAP_ABS
from skills.adapters.referee_umpire    import RefereeUmpireAdapter
from skills.adapters.correlation_slip  import CorrelationSlipAdapter
from skills.adapters.market_odds       import MarketOddsAdapter
from skills.adapters.probability_ev    import ProbabilityEvAdapter


class TestInvariantCanExecuteAlwaysFalse(unittest.TestCase):
    """can_execute is ALWAYS False — no path can set it to True."""

    def test_skill_result_overrides_true_to_false(self):
        r = SkillResult(
            skill_id="test", skill_version="1.0.0",
            inputs_used={}, sources=[], findings=[], blockers=[],
            label=SkillLabel.READY.value, confidence=0.5,
            can_execute=True,
        )
        self.assertFalse(r.can_execute)

    def test_skill_result_dict_can_execute_false(self):
        r = SkillResult(
            skill_id="test", skill_version="1.0.0",
            inputs_used={}, sources=[], findings=[], blockers=[],
            label=SkillLabel.READY.value, confidence=0.5,
        )
        self.assertFalse(r.to_dict()["can_execute"])

    def test_every_adapter_returns_can_execute_false(self):
        default_contexts = {
            "wow.kalshi-contract-intelligence": {
                "kalshi_inventory_health": "INVENTORY_READY",
                "kalshi_orderbook": {"yes_bids": [0.6], "no_bids": [0.4]},
            },
            "wow.weather-intelligence":        {"weather_city": "CHI", "weather_station": "KMDW"},
            "wow.sports-research-analyst":      {"event_date": "2026-07-14", "target_date": "2026-07-14"},
            "wow.market-odds-intelligence":     {"home_american_odds": -110, "away_american_odds": -110},
            "wow.player-prop-intelligence":     {},
            "wow.game-script-simulator":        {},
            "wow.mlb-pitching-expert":          {},
            "wow.mlb-hitting-expert":           {},
            "wow.wnba-specialist":              {},
            "wow.correlation-slip-auditor":     {},
            "wow.probability-ev-auditor":       {},
            "wow.bankroll-risk-manager":        {"upstream_final_label": "READY", "kelly_fraction": 0.1},
            "wow.qa-hallucination-auditor":     {"upstream_skill_results": []},
            "wow.patch-governance-architect":   {},
            "wow.lottery-analyst":              {},
            "wow.financial-market-analyst":     {},
            "wow.historical-trend-researcher":  {},
            "wow.sportsbook-promo-optimizer":   {},
            "wow.dfs-analyst":                  {},
            "wow.sports-psychology-context":    {"psychology_adjustment": 0.01},
            "wow.referee-umpire-tendency":      {"ref_assignment_confirmed": False},
        }
        for skill_id, adapter_cls in ADAPTER_MAP.items():
            ctx = default_contexts.get(skill_id, {})
            with self.subTest(skill_id=skill_id):
                result = adapter_cls().run(ctx)
                self.assertFalse(result.can_execute,
                                 f"Adapter {skill_id!r} returned can_execute=True")

    def test_orchestrator_can_execute_always_false(self):
        orch = SkillOrchestrator()
        for market_type in ("player_prop", "kalshi_weather", "lottery",
                            "team_winner", "kalshi_sports"):
            with self.subTest(market_type=market_type):
                ctx = {"market_type": market_type}
                if market_type == "kalshi_sports":
                    ctx["kalshi_inventory_health"] = "INVENTORY_READY"
                result = orch.run(ctx)
                self.assertFalse(result["can_execute"])

    def test_orchestrator_freeze_reject_can_execute_false(self):
        result = SkillOrchestrator().run({
            "market_type": "kalshi_sports",
            "reliability_freeze": True,
            "kalshi_combo_markets": ["m1", "m2", "m3", "m4"],
        })
        self.assertFalse(result["can_execute"])


class TestInvariantKalshiStalePriceExactBoundary(unittest.TestCase):
    """Kalshi price freshness: exactly 600 s is valid; 601 s is stale."""

    def _run(self, age_seconds: int) -> SkillResult:
        return KalshiContractAdapter().run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_price_age_seconds": age_seconds,
            "kalshi_orderbook": {"yes_bids": [0.5], "no_bids": [0.5]},
        })

    def test_exactly_600_seconds_valid(self):
        self.assertEqual(FRESHNESS_LIVE_PRICE, 600,
                         "FRESHNESS_LIVE_PRICE constant must be 600")
        result = self._run(600)
        self.assertNotEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)

    def test_601_seconds_is_stale(self):
        result = self._run(601)
        self.assertEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)

    def test_11_minutes_is_stale(self):
        result = self._run(660)
        self.assertEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)

    def test_9_minutes_is_fresh(self):
        result = self._run(540)
        self.assertNotEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)


class TestInvariantKalshiEmptyOrderbook(unittest.TestCase):
    """Empty Kalshi orderbook → DATA_UNOBTAINABLE."""

    def test_empty_yes_and_no_bids(self):
        result = KalshiContractAdapter().run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_orderbook": {"yes_bids": [], "no_bids": []},
        })
        self.assertEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)

    def test_populated_bids_not_unobtainable(self):
        result = KalshiContractAdapter().run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_orderbook": {"yes_bids": [0.55], "no_bids": [0.45]},
        })
        self.assertNotEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)
        self.assertFalse(result.can_execute)


class TestInvariantKalshiClosedMarket(unittest.TestCase):
    """Closed / settled / finalized Kalshi market → REJECT_BAD_RULES."""

    def test_all_closed_statuses(self):
        for status in ("closed", "settled", "finalized"):
            with self.subTest(status=status):
                result = KalshiContractAdapter().run({
                    "kalshi_inventory_health": "INVENTORY_READY",
                    "kalshi_market_status": status,
                    "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]},
                })
                self.assertEqual(result.label, SkillLabel.REJECT_BAD_RULES.value)
                self.assertFalse(result.can_execute)

    def test_open_status_not_rejected(self):
        result = KalshiContractAdapter().run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_market_status": "open",
            "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]},
        })
        self.assertNotEqual(result.label, SkillLabel.REJECT_BAD_RULES.value)
        self.assertFalse(result.can_execute)


class TestInvariantOperatorSuppliedSource(unittest.TestCase):
    """Operator-supplied / screenshot prices cannot become direct/live sources — cap at WATCH."""

    def test_screenshot_odds_caps_at_watch(self):
        result = MarketOddsAdapter().run({
            "odds_source_type": "screenshot",
            "home_american_odds": -110, "away_american_odds": -110,
        })
        self.assertEqual(result.label, SkillLabel.WATCH.value)
        self.assertFalse(result.can_execute)

    def test_operator_supplied_odds_caps_at_watch(self):
        result = MarketOddsAdapter().run({
            "odds_source_type": "operator_supplied",
            "home_american_odds": -110, "away_american_odds": -110,
        })
        self.assertEqual(result.label, SkillLabel.WATCH.value)
        self.assertFalse(result.can_execute)

    def test_direct_source_not_capped_at_watch(self):
        result = MarketOddsAdapter().run({
            "odds_source_type": "direct",
            "home_american_odds": -110, "away_american_odds": -110,
        })
        self.assertNotEqual(result.label, SkillLabel.WATCH.value)
        self.assertFalse(result.can_execute)

    def test_source_evidence_quality_5_is_operator_supplied(self):
        ev = SourceEvidence(source_id="screenshot", quality=SOURCE_QUALITY_OPERATOR_SUPPLIED)
        self.assertTrue(ev.is_operator_supplied)

    def test_source_evidence_quality_1_not_operator_supplied(self):
        ev = SourceEvidence(source_id="kalshi_api", quality=1)
        self.assertFalse(ev.is_operator_supplied)


class TestInvariantBareLabelNormalization(unittest.TestCase):
    """Bare LLP_PLAYABLE_LIMIT_ONLY normalizes to LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN."""

    def test_bare_label_normalized_by_post_init(self):
        r = SkillResult(
            skill_id="test", skill_version="1.0.0",
            inputs_used={}, sources=[], findings=[], blockers=[],
            label=BARE_LABEL_FORBIDDEN, confidence=0.5,
        )
        self.assertEqual(r.label, DRY_RUN_LABEL)
        self.assertNotEqual(r.label, BARE_LABEL_FORBIDDEN)

    def test_dry_run_label_is_not_bare(self):
        self.assertNotEqual(DRY_RUN_LABEL, BARE_LABEL_FORBIDDEN)

    def test_all_enum_labels_pass_through_unchanged(self):
        for lbl in SkillLabel:
            r = SkillResult(
                skill_id="t", skill_version="1.0.0",
                inputs_used={}, sources=[], findings=[], blockers=[],
                label=lbl.value, confidence=0.5,
            )
            self.assertEqual(r.label, lbl.value)

    def test_kalshi_adapter_never_emits_bare_label(self):
        result = KalshiContractAdapter().run({
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]},
        })
        self.assertNotEqual(result.label, BARE_LABEL_FORBIDDEN)
        self.assertFalse(result.can_execute)


class TestInvariantNhighStationCodes(unittest.TestCase):
    """NHIGH station codes: CHI=KMDW, MIA=KMIA, LA=KLAX.  Banned codes must never be canonical."""

    EXPECTED = {
        "CHI":         "KMDW",
        "CHICAGO":     "KMDW",
        "MIA":         "KMIA",
        "MIAMI":       "KMIA",
        "LA":          "KLAX",
        "LOS ANGELES": "KLAX",
        "NYC":         "KNYC",
        "NEW YORK":    "KNYC",
        "AUS":         "KAUS",
    }

    BANNED = {"KORD": "CHI", "KPBI": "MIA", "PBI": "MIA", "KBUR": "LA", "BUR": "LA"}

    def test_all_canonical_mappings_resolve_correctly(self):
        for city, expected_stn in self.EXPECTED.items():
            with self.subTest(city=city):
                self.assertEqual(resolve_nhigh_station(city), expected_stn)

    def test_banned_stations_are_not_canonical(self):
        for banned_stn, correct_city in self.BANNED.items():
            canonical = resolve_nhigh_station(correct_city)
            with self.subTest(banned_stn=banned_stn, city=correct_city):
                self.assertNotEqual(canonical, banned_stn)

    def test_chi_kord_rejected_by_adapter(self):
        result = WeatherIntelAdapter().run({"weather_city": "CHI", "weather_station": "KORD"})
        codes = [b["code"] for b in result.blockers]
        self.assertTrue(any("NHIGH" in c for c in codes))
        self.assertFalse(result.can_execute)

    def test_mia_pbi_rejected_by_adapter(self):
        for stn in ("PBI", "KPBI"):
            with self.subTest(station=stn):
                result = WeatherIntelAdapter().run({"weather_city": "MIA",
                                                    "weather_station": stn})
                codes = [b["code"] for b in result.blockers]
                self.assertTrue(any("NHIGH" in c for c in codes))
                self.assertFalse(result.can_execute)

    def test_la_bur_rejected_by_adapter(self):
        for stn in ("BUR", "KBUR"):
            with self.subTest(station=stn):
                result = WeatherIntelAdapter().run({"weather_city": "LA",
                                                    "weather_station": stn})
                codes = [b["code"] for b in result.blockers]
                self.assertTrue(any("NHIGH" in c for c in codes))
                self.assertFalse(result.can_execute)

    def test_valid_stations_not_rejected(self):
        valid = [("CHI", "KMDW"), ("MIA", "KMIA"), ("LA", "KLAX"),
                 ("NYC", "KNYC"), ("AUS", "KAUS")]
        for city, stn in valid:
            with self.subTest(city=city, station=stn):
                result = WeatherIntelAdapter().run({"weather_city": city,
                                                    "weather_station": stn})
                codes = [b["code"] for b in result.blockers]
                self.assertFalse(any("NHIGH" in c for c in codes))
                self.assertFalse(result.can_execute)


class TestInvariantGaussianBracketBounds(unittest.TestCase):
    """Gaussian weather bracket probabilities normalize to [0.97, 1.03] sum."""

    def test_bracket_sum_in_range(self):
        adapter = WeatherIntelAdapter()
        result = adapter.run({
            "weather_city": "NYC", "weather_station": "KNYC",
            "weather_threshold_f": 72.0, "weather_sigma_f": 3.5,
        })
        bracket_finding = next(
            (f for f in result.findings if "gaussian_brackets" in f), None)
        self.assertIsNotNone(bracket_finding)
        total = bracket_finding["bracket_sum"]
        self.assertGreaterEqual(total, 0.97)
        self.assertLessEqual(total, 1.03)
        self.assertFalse(result.can_execute)

    def test_normalize_rescales_sum_above_1_03(self):
        probs = [0.12] * 10
        normalized = normalize_brackets(probs)
        self.assertAlmostEqual(sum(normalized), 1.0, places=5)

    def test_sum_in_range_not_rescaled(self):
        probs = [0.14, 0.15, 0.15, 0.14, 0.14, 0.14, 0.14]
        original_sum = sum(probs)
        normalized = normalize_brackets(probs)
        self.assertAlmostEqual(sum(normalized), original_sum, places=5)

    def test_all_sigma_values_produce_valid_brackets(self):
        for sigma in (2.0, 3.5, 5.0, 7.0):
            with self.subTest(sigma=sigma):
                result = WeatherIntelAdapter().run({
                    "weather_city": "NYC", "weather_station": "KNYC",
                    "weather_threshold_f": 80.0, "weather_sigma_f": sigma,
                })
                bracket_finding = next(
                    (f for f in result.findings if "gaussian_brackets" in f), None)
                if bracket_finding:
                    total = bracket_finding["bracket_sum"]
                    self.assertGreaterEqual(total, 0.97)
                    self.assertLessEqual(total, 1.03)


class TestInvariantComboSizeGates(unittest.TestCase):
    """Reliability Freeze: four-market Kalshi sports combo is a hard reject."""

    def test_four_or_more_markets_hard_reject_in_freeze(self):
        for n in (4, 5, 10):
            with self.subTest(n_markets=n):
                result = SkillOrchestrator().run({
                    "market_type": "kalshi_sports",
                    "reliability_freeze": True,
                    "kalshi_combo_markets": [f"m{i}" for i in range(n)],
                })
                self.assertTrue(result["stopped_early"])
                codes = [b["code"] for b in result["blockers"]]
                self.assertIn("RELIABILITY_FREEZE_COMBO_HARD_REJECT", codes)
                self.assertEqual(result["final_label"], SkillLabel.REJECT_BAD_RULES.value)
                self.assertFalse(result["can_execute"])

    def test_three_markets_not_rejected_in_freeze(self):
        result = SkillOrchestrator().run({
            "market_type": "kalshi_sports",
            "reliability_freeze": True,
            "kalshi_combo_markets": ["m1", "m2", "m3"],
            "kalshi_inventory_health": "INVENTORY_READY",
        })
        codes = [b["code"] for b in result["blockers"]]
        self.assertNotIn("RELIABILITY_FREEZE_COMBO_HARD_REJECT", codes)
        self.assertFalse(result["can_execute"])

    def test_four_markets_ok_without_freeze(self):
        result = SkillOrchestrator().run({
            "market_type": "kalshi_sports",
            "reliability_freeze": False,
            "kalshi_combo_markets": ["m1", "m2", "m3", "m4"],
            "kalshi_inventory_health": "INVENTORY_READY",
        })
        codes = [b["code"] for b in result["blockers"]]
        self.assertNotIn("RELIABILITY_FREEZE_COMBO_HARD_REJECT", codes)
        self.assertFalse(result["can_execute"])


class TestInvariantDuplicateCounting(unittest.TestCase):
    """Duplicate same-event/same-side entries count as one model/calibration observation."""

    def test_identical_legs_counted_as_one_unique(self):
        legs = [
            {"event_id": "g1", "side": "MORE", "player": "A", "prop_type": "pts"},
            {"event_id": "g1", "side": "MORE", "player": "A", "prop_type": "pts"},
        ]
        result = CorrelationSlipAdapter().run({
            "slip_legs": legs,
            "joint_probability": 0.35,
            "combo_breakeven_prob": 0.333,
        })
        summary = next((f for f in result.findings if "duplicate_count" in f), None)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["unique_legs"], 1)
        self.assertEqual(summary["duplicate_count"], 1)
        self.assertFalse(result.can_execute)

    def test_different_events_both_count(self):
        legs = [
            {"event_id": "g1", "side": "MORE", "player": "A", "prop_type": "pts"},
            {"event_id": "g2", "side": "MORE", "player": "B", "prop_type": "pts"},
        ]
        result = CorrelationSlipAdapter().run({
            "slip_legs": legs,
            "joint_probability": 0.35,
            "combo_breakeven_prob": 0.333,
        })
        summary = next((f for f in result.findings if "duplicate_count" in f), None)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["unique_legs"], 2)
        self.assertEqual(summary["duplicate_count"], 0)
        self.assertFalse(result.can_execute)

    def test_opposite_sides_same_event_both_count(self):
        legs = [
            {"event_id": "g1", "side": "MORE", "player": "A", "prop_type": "pts"},
            {"event_id": "g1", "side": "LESS", "player": "A", "prop_type": "pts"},
        ]
        result = CorrelationSlipAdapter().run({
            "slip_legs": legs,
            "joint_probability": 0.35,
            "combo_breakeven_prob": 0.333,
        })
        summary = next((f for f in result.findings if "duplicate_count" in f), None)
        if summary:
            self.assertEqual(summary["duplicate_count"], 0)
        self.assertFalse(result.can_execute)


class TestInvariantLowestCeilingPropagation(unittest.TestCase):
    """lower_ceiling always returns the more restrictive label; orchestrator enforces it."""

    def test_lower_ceiling_semantics(self):
        cases = [
            (SkillLabel.READY.value, SkillLabel.HOLD.value, SkillLabel.HOLD.value),
            (SkillLabel.HOLD.value, SkillLabel.READY.value, SkillLabel.HOLD.value),
            (SkillLabel.WATCH.value, SkillLabel.WATCH.value, SkillLabel.WATCH.value),
            (SkillLabel.READY.value, SkillLabel.DATA_UNOBTAINABLE.value,
             SkillLabel.DATA_UNOBTAINABLE.value),
            (SkillLabel.DATA_UNOBTAINABLE.value, SkillLabel.READY.value,
             SkillLabel.DATA_UNOBTAINABLE.value),
        ]
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(lower_ceiling(a, b), expected)

    def test_severity_order_is_monotone(self):
        ordered = [
            SkillLabel.READY, SkillLabel.WATCH, SkillLabel.SCOUT, SkillLabel.HOLD,
            SkillLabel.REJECT_BAD_RULES, SkillLabel.REJECT_DATA_QUALITY,
            SkillLabel.DATA_UNOBTAINABLE,
        ]
        for i in range(len(ordered) - 1):
            self.assertLess(label_severity(ordered[i].value),
                            label_severity(ordered[i + 1].value))

    def test_orchestrator_propagates_most_restrictive(self):
        result = SkillOrchestrator().run({"market_type": "player_prop"})
        self.assertGreaterEqual(
            label_severity(result["final_label"]),
            label_severity(SkillLabel.REJECT_DATA_QUALITY.value),
        )
        self.assertFalse(result["can_execute"])

    def test_downstream_ready_cannot_upgrade_upstream_hold(self):
        ceiling = SkillLabel.READY.value
        ceiling = lower_ceiling(ceiling, SkillLabel.HOLD.value)
        self.assertEqual(ceiling, SkillLabel.HOLD.value)
        ceiling = lower_ceiling(ceiling, SkillLabel.READY.value)
        self.assertEqual(ceiling, SkillLabel.HOLD.value)


class TestInvariantBankrollBlockedLane(unittest.TestCase):
    """No allocation when capital lane is blocked or upstream label is restrictive."""

    def test_capital_lane_blocked_zero_allocation(self):
        result = BankrollRiskAdapter().run({"capital_lane_blocked": True,
                                            "kelly_fraction": 0.25})
        alloc = next((f for f in result.findings if "allocation" in f), None)
        self.assertIsNotNone(alloc)
        self.assertEqual(alloc["allocation"], 0.0)
        self.assertIn("CAPITAL_LANE_BLOCKED",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_restrictive_upstream_labels_block_allocation(self):
        for lbl in (SkillLabel.HOLD, SkillLabel.REJECT_BAD_RULES,
                    SkillLabel.REJECT_DATA_QUALITY, SkillLabel.DATA_UNOBTAINABLE):
            with self.subTest(label=lbl.value):
                result = BankrollRiskAdapter().run({"upstream_final_label": lbl.value,
                                                    "kelly_fraction": 0.25})
                alloc = next((f for f in result.findings if "allocation" in f), None)
                self.assertIsNotNone(alloc)
                self.assertEqual(alloc["allocation"], 0.0)
                self.assertFalse(result.can_execute)

    def test_ready_upstream_non_blocked_allows_allocation(self):
        result = BankrollRiskAdapter().run({
            "upstream_final_label": SkillLabel.READY.value,
            "kelly_fraction": 0.20,
            "capital_lane_blocked": False,
        })
        alloc = next((f for f in result.findings if "allocation_units" in f), None)
        self.assertIsNotNone(alloc)
        self.assertGreater(alloc["allocation_units"], 0.0)
        self.assertFalse(result.can_execute)

    def test_reliability_freeze_caps_kelly_at_quarter(self):
        result = BankrollRiskAdapter().run({
            "upstream_final_label": SkillLabel.READY.value,
            "reliability_freeze": True,
            "kelly_fraction": 1.0,
            "capital_lane_blocked": False,
        })
        alloc = next((f for f in result.findings if "allocation_units" in f), None)
        self.assertIsNotNone(alloc)
        self.assertLessEqual(alloc["allocation_units"],
                             RELIABILITY_FREEZE_MAX_UNITS + 1e-9)
        self.assertFalse(result.can_execute)


class TestInvariantPsychologyCap(unittest.TestCase):
    """Sports psychology cap: ±3% limit; unsupported mental-state claims banned."""

    def test_cap_constant_equals_3pct(self):
        self.assertAlmostEqual(LOW_WEIGHT_CAP_ABS, 0.03)

    def test_exceeds_positive_cap_rejected(self):
        result = SportsPsychologyAdapter().run({"psychology_adjustment": 0.05})
        self.assertIn("PSYCHOLOGY_ADJUSTMENT_EXCEEDS_CAP",
                      [b["code"] for b in result.blockers])
        self.assertEqual(result.label, SkillLabel.REJECT_BAD_RULES.value)
        self.assertFalse(result.can_execute)

    def test_exceeds_negative_cap_rejected(self):
        result = SportsPsychologyAdapter().run({"psychology_adjustment": -0.05})
        self.assertIn("PSYCHOLOGY_ADJUSTMENT_EXCEEDS_CAP",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_within_positive_cap_accepted(self):
        result = SportsPsychologyAdapter().run({"psychology_adjustment": 0.02})
        self.assertNotIn("PSYCHOLOGY_ADJUSTMENT_EXCEEDS_CAP",
                         [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_within_negative_cap_accepted(self):
        result = SportsPsychologyAdapter().run({"psychology_adjustment": -0.02})
        self.assertNotIn("PSYCHOLOGY_ADJUSTMENT_EXCEEDS_CAP",
                         [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_unsupported_mental_state_claim_blocked(self):
        result = SportsPsychologyAdapter().run({
            "psychology_adjustment": 0.01,
            "mental_state_claim": "player is feeling motivated",
        })
        self.assertIn("PSYCHOLOGY_UNSUPPORTED_MENTAL_STATE",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)


class TestInvariantUnconfirmedUmpire(unittest.TestCase):
    """Ref/umpire skill: zero adjustment when assignment is unconfirmed."""

    def test_unconfirmed_yields_zero_adjustment(self):
        result = RefereeUmpireAdapter().run({
            "ref_assignment_confirmed": False,
            "official_name": "Unknown Ref",
        })
        self.assertEqual(result.findings[0].get("adjustment_applied", -1), 0.0)
        self.assertIn("REF_ASSIGNMENT_UNCONFIRMED",
                      [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_confirmed_may_apply_tendency(self):
        result = RefereeUmpireAdapter().run({
            "ref_assignment_confirmed": True,
            "official_name": "Ref A",
            "tendency_data": {"prob_adjustment": 0.02},
        })
        self.assertNotIn("REF_ASSIGNMENT_UNCONFIRMED",
                         [b["code"] for b in result.blockers])
        self.assertFalse(result.can_execute)

    def test_no_official_name_still_blocked(self):
        result = RefereeUmpireAdapter().run({"ref_assignment_confirmed": False})
        self.assertFalse(result.can_execute)


class TestInvariantRegistryIntegrity(unittest.TestCase):
    """Registry must have exactly 22 skills with no duplicates; validate_registry() clean."""

    def setUp(self):
        self._reg = SkillRegistry.get()

    def test_registry_has_22_skills(self):
        self.assertEqual(len(self._reg.all_skills()), 22)

    def test_validate_registry_returns_no_errors(self):
        errors = self._reg.validate_registry()
        self.assertEqual(errors, [], f"validate_registry() errors: {errors}")

    def test_no_duplicate_ids(self):
        ids = [s["id"] for s in self._reg.all_skills()]
        self.assertEqual(len(ids), len(set(ids)), f"Duplicate IDs found: {ids}")

    def test_all_skills_have_required_fields(self):
        for s in self._reg.all_skills():
            with self.subTest(skill_id=s.get("id")):
                self.assertIn("id", s)
                self.assertIn("name", s)
                self.assertIn("priority", s)

    def test_get_skill_returns_correct_entry(self):
        skill = self._reg.get_skill("wow.kalshi-contract-intelligence")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["id"], "wow.kalshi-contract-intelligence")

    def test_ordered_skills_are_priority_sorted(self):
        ordered = self._reg.ordered_skills()
        priorities = [s["priority"] for s in ordered]
        self.assertEqual(priorities, sorted(priorities))


class TestInvariantConfidenceClamping(unittest.TestCase):
    """SkillResult confidence is clamped to [0.0, 1.0]."""

    def test_above_one_clamped_to_one(self):
        r = SkillResult(skill_id="t", skill_version="1.0.0",
                        inputs_used={}, sources=[], findings=[], blockers=[],
                        label=SkillLabel.READY.value, confidence=5.0)
        self.assertAlmostEqual(r.confidence, 1.0)

    def test_below_zero_clamped_to_zero(self):
        r = SkillResult(skill_id="t", skill_version="1.0.0",
                        inputs_used={}, sources=[], findings=[], blockers=[],
                        label=SkillLabel.READY.value, confidence=-2.5)
        self.assertAlmostEqual(r.confidence, 0.0)

    def test_valid_confidence_unchanged(self):
        r = SkillResult(skill_id="t", skill_version="1.0.0",
                        inputs_used={}, sources=[], findings=[], blockers=[],
                        label=SkillLabel.READY.value, confidence=0.72)
        self.assertAlmostEqual(r.confidence, 0.72)

    def test_zero_and_one_not_clamped(self):
        for val in (0.0, 1.0):
            r = SkillResult(skill_id="t", skill_version="1.0.0",
                            inputs_used={}, sources=[], findings=[], blockers=[],
                            label=SkillLabel.READY.value, confidence=val)
            self.assertAlmostEqual(r.confidence, val)


class TestInvariantCanonicalIDIntegrity(unittest.TestCase):
    """Adapter SKILL_IDs must match registry IDs exactly — no aliases or orphans."""

    def setUp(self):
        self._reg = SkillRegistry.get()

    def test_every_registry_id_has_adapter(self):
        for s in self._reg.all_skills():
            with self.subTest(skill_id=s["id"]):
                self.assertIn(s["id"], ADAPTER_MAP,
                              f"Registry ID {s['id']!r} has no adapter")

    def test_every_adapter_key_in_registry(self):
        registry_ids = {s["id"] for s in self._reg.all_skills()}
        for key in ADAPTER_MAP:
            with self.subTest(adapter_key=key):
                self.assertIn(key, registry_ids,
                              f"Adapter key {key!r} not in registry")

    def test_adapter_and_registry_sets_equal(self):
        registry_ids = {s["id"] for s in self._reg.all_skills()}
        adapter_keys = set(ADAPTER_MAP.keys())
        self.assertEqual(registry_ids, adapter_keys)

    def test_every_result_skill_id_matches_invocation_id(self):
        for skill_id, adapter_cls in ADAPTER_MAP.items():
            with self.subTest(skill_id=skill_id):
                result = adapter_cls().run({})
                self.assertEqual(result.skill_id, skill_id)

    def test_downstream_handoffs_reference_only_registered_ids(self):
        registry_ids = {s["id"] for s in self._reg.all_skills()}
        for skill_id, adapter_cls in ADAPTER_MAP.items():
            with self.subTest(skill_id=skill_id):
                result = adapter_cls().run({})
                for downstream_id in result.downstream:
                    self.assertIn(downstream_id, registry_ids,
                                  f"{skill_id!r} has unregistered downstream: {downstream_id!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
