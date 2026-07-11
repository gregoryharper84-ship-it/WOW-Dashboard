"""
gate_engine/tests/test_skills_regression.py
Regression tests for patches touched by the skills layer integration.

Tests are deterministic (no live API calls, no app.py imports).
"""
from __future__ import annotations

import sys
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from skills.contracts import SkillResult, SkillLabel, DRY_RUN_LABEL, BARE_LABEL_FORBIDDEN
from skills.adapters.kalshi_contract import KalshiContractAdapter
from skills.adapters.weather_intel import WeatherIntelAdapter, resolve_nhigh_station
from skills.adapters.bankroll_risk import BankrollRiskAdapter, RELIABILITY_FREEZE_MAX_UNITS
from skills.orchestrator import SkillOrchestrator


class TestRegressionKalshiDryRunLabel(unittest.TestCase):
    """Kalshi adapter must never emit bare LLP_PLAYABLE_LIMIT_ONLY."""

    def test_kalshi_result_is_dry_run_not_bare(self):
        adapter = KalshiContractAdapter()
        ctx = {
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_orderbook": {"yes_bids": [0.60], "no_bids": [0.40]},
        }
        result = adapter.run(ctx)
        # If result carries LLP label, it must be the DRY_RUN variant
        if "LLP_PLAYABLE" in result.label:
            self.assertEqual(result.label, DRY_RUN_LABEL,
                             f"Bare label detected: {result.label!r}")
        self.assertNotEqual(result.label, BARE_LABEL_FORBIDDEN)

    def test_any_result_with_bare_label_auto_normalized(self):
        """Even if an adapter accidentally sets the bare label, contracts.py normalizes it."""
        r = SkillResult(
            skill_id="test", skill_version="1.0.0",
            inputs_used={}, sources=[], findings=[], blockers=[],
            label=BARE_LABEL_FORBIDDEN, confidence=0.5,
        )
        self.assertEqual(r.label, DRY_RUN_LABEL)


class TestRegressionNhighStationMapping(unittest.TestCase):
    """NHIGH station mappings must not regress after any patch."""

    EXPECTED = {
        "CHI":     "KMDW",
        "CHICAGO": "KMDW",
        "MIA":     "KMIA",
        "MIAMI":   "KMIA",
        "LA":      "KLAX",
        "LOS ANGELES": "KLAX",
        "NYC":     "KNYC",
        "NEW YORK": "KNYC",
        "AUS":     "KAUS",
    }

    def test_all_canonical_mappings(self):
        for city, expected_stn in self.EXPECTED.items():
            with self.subTest(city=city):
                self.assertEqual(resolve_nhigh_station(city), expected_stn,
                                 f"City {city!r} → expected {expected_stn!r}")

    def test_banned_stations_not_canonical(self):
        banned = {"KORD": "CHI", "KPBI": "MIA", "PBI": "MIA", "KBUR": "LA", "BUR": "LA"}
        for banned_stn, correct_city in banned.items():
            canonical = resolve_nhigh_station(correct_city)
            self.assertNotEqual(canonical, banned_stn,
                                f"Banned station {banned_stn!r} should not map to {correct_city!r}")


class TestRegressionReliabilityFreezeCapInSkills(unittest.TestCase):
    """Reliability Freeze cap must be enforced in bankroll adapter."""

    def test_freeze_caps_kelly_at_quarter(self):
        adapter = BankrollRiskAdapter()
        ctx = {
            "upstream_final_label": SkillLabel.READY.value,
            "reliability_freeze": True,
            "kelly_fraction": 1.0,  # full Kelly
            "capital_lane_blocked": False,
        }
        result = adapter.run(ctx)
        alloc = next((f for f in result.findings if "allocation_units" in f), None)
        self.assertIsNotNone(alloc)
        self.assertLessEqual(alloc["allocation_units"], RELIABILITY_FREEZE_MAX_UNITS + 1e-9)

    def test_no_freeze_full_kelly_allowed(self):
        adapter = BankrollRiskAdapter()
        ctx = {
            "upstream_final_label": SkillLabel.READY.value,
            "reliability_freeze": False,
            "kelly_fraction": 0.50,
            "capital_lane_blocked": False,
        }
        result = adapter.run(ctx)
        alloc = next((f for f in result.findings if "allocation_units" in f), None)
        self.assertIsNotNone(alloc)
        self.assertAlmostEqual(alloc["allocation_units"], 0.50)


class TestRegressionOrchestratorCanExecuteFalse(unittest.TestCase):
    """Orchestrator result can_execute must always be False."""

    def test_orchestrator_can_execute_is_false(self):
        orch = SkillOrchestrator()
        for market_type in ("player_prop", "kalshi_weather", "lottery", "team_winner"):
            with self.subTest(market_type=market_type):
                ctx = {"market_type": market_type}
                result = orch.run(ctx)
                self.assertFalse(result["can_execute"])

    def test_orchestrator_freeze_reject_can_execute_false(self):
        orch = SkillOrchestrator()
        ctx = {
            "market_type": "kalshi_sports",
            "reliability_freeze": True,
            "kalshi_combo_markets": ["m1", "m2", "m3", "m4"],
        }
        result = orch.run(ctx)
        self.assertFalse(result["can_execute"])


class TestRegressionKalshiFreshnessExactBoundary(unittest.TestCase):
    """Price exactly at 600 seconds (10 min) is still valid; 601 is stale."""

    def test_exactly_600_seconds_valid(self):
        adapter = KalshiContractAdapter()
        ctx = {
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_price_age_seconds": 600,   # exactly 10 min
            "kalshi_orderbook": {"yes_bids": [0.5], "no_bids": [0.5]},
        }
        result = adapter.run(ctx)
        self.assertNotEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)

    def test_601_seconds_is_stale(self):
        adapter = KalshiContractAdapter()
        ctx = {
            "kalshi_inventory_health": "INVENTORY_READY",
            "kalshi_price_age_seconds": 601,
            "kalshi_orderbook": {"yes_bids": [0.5], "no_bids": [0.5]},
        }
        result = adapter.run(ctx)
        self.assertEqual(result.label, SkillLabel.DATA_UNOBTAINABLE.value)


class TestCanonicalIDIntegrity(unittest.TestCase):
    """
    Six-part canonicalization suite — registry IDs must be the single source of
    truth across adapters, orchestrator results, Flask responses, and handoffs.
    """

    def setUp(self):
        from skills.registry import SkillRegistry
        from skills.adapters import ADAPTER_MAP
        self._reg = SkillRegistry.get()
        self._adapter_map = ADAPTER_MAP

    # ── Test 1 ──────────────────────────────────────────────────────────────
    def test_every_registry_id_has_exactly_one_adapter(self):
        """Each of the 21 registry IDs maps to exactly one adapter class."""
        registry_ids = {s["id"] for s in self._reg.all_skills()}
        for skill_id in registry_ids:
            with self.subTest(skill_id=skill_id):
                self.assertIn(
                    skill_id, self._adapter_map,
                    f"Registry ID {skill_id!r} has no adapter in ADAPTER_MAP.",
                )

    # ── Test 2 ──────────────────────────────────────────────────────────────
    def test_every_adapter_key_exists_in_registry(self):
        """Every key in ADAPTER_MAP must appear in the registry (no orphans)."""
        registry_ids = {s["id"] for s in self._reg.all_skills()}
        for adapter_key in self._adapter_map:
            with self.subTest(adapter_key=adapter_key):
                self.assertIn(
                    adapter_key, registry_ids,
                    f"Adapter key {adapter_key!r} is not in the registry (orphan).",
                )

    # ── Test 3 ──────────────────────────────────────────────────────────────
    def test_no_adapter_aliases_or_orphan_ids(self):
        """Registry and ADAPTER_MAP must have the same set — no extras either way."""
        registry_ids = {s["id"] for s in self._reg.all_skills()}
        adapter_keys = set(self._adapter_map.keys())
        self.assertEqual(
            registry_ids, adapter_keys,
            f"Mismatch — registry only: {registry_ids - adapter_keys!r}  "
            f"adapter only: {adapter_keys - registry_ids!r}",
        )

    # ── Test 4 ──────────────────────────────────────────────────────────────
    def test_every_result_skill_id_equals_registry_id_used_to_invoke_it(self):
        """Each adapter's run() emits the exact SKILL_ID constant declared in that module."""
        ctx = {}
        for skill_id, adapter_cls in self._adapter_map.items():
            with self.subTest(skill_id=skill_id):
                adapter = adapter_cls()
                result = adapter.run(ctx)
                self.assertEqual(
                    result.skill_id, skill_id,
                    f"Adapter invoked as {skill_id!r} but result.skill_id={result.skill_id!r}",
                )

    # ── Test 5 ──────────────────────────────────────────────────────────────
    def test_registry_endpoint_and_run_endpoint_use_same_canonical_ids(self):
        """
        GET /wow/skills/registry IDs and the skill_ids emitted by the orchestrator
        must be drawn from the same canonical set.
        """
        registry_ids = {s["id"] for s in self._reg.ordered_skills()}
        orch = SkillOrchestrator()
        result = orch.run({"market_type": "player_prop"})
        returned_ids = {r["skill_id"] for r in result.get("skill_results", [])}
        unexpected = returned_ids - registry_ids
        self.assertFalse(
            unexpected,
            f"Orchestrator returned non-registry skill_ids: {unexpected!r}",
        )

    # ── Test 6 ──────────────────────────────────────────────────────────────
    def test_downstream_handoffs_reference_only_registered_ids(self):
        """
        Every downstream handoff ID in every adapter result must be a registered ID.
        """
        registry_ids = {s["id"] for s in self._reg.all_skills()}
        ctx = {}
        for skill_id, adapter_cls in self._adapter_map.items():
            with self.subTest(skill_id=skill_id):
                result = adapter_cls().run(ctx)
                for downstream_id in result.downstream:
                    self.assertIn(
                        downstream_id, registry_ids,
                        f"{skill_id!r} has downstream handoff {downstream_id!r} "
                        f"which is not in the registry.",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
