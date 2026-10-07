from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "v17" / "ecosystem_conductor.py"
REGISTRY_PATH = ROOT / "v17" / "ecosystem-registry.json"

spec = importlib.util.spec_from_file_location("ecosystem_conductor", MODULE_PATH)
assert spec and spec.loader
conductor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conductor)


def load_registry():
    return json.loads(REGISTRY_PATH.read_text())


def all_green_observed(registry):
    return {
        "components": {name: "PASS" for name in registry["components"]},
        "handoffs": {name: "PASS" for name in registry["handoffs"]},
        "invariants": {
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
            "specialist_ownership_preserved": True,
            "typed_failures_preserved": True,
            "self_verification_detected": False,
        },
    }


class EcosystemConductorTests(unittest.TestCase):
    def test_registry_is_valid_and_authority_is_separated(self):
        registry = load_registry()

        self.assertEqual([], conductor.validate_registry(registry))
        self.assertFalse(registry["can_execute"])
        self.assertFalse(
            registry["invariants"]["conductor_probability_authority"]
        )
        self.assertTrue(
            registry["components"]["SYSTEMS_INTELLIGENCE_RELIABILITY"][
                "safe_hold_authority"
            ]
        )
        self.assertTrue(
            registry["components"]["V17_TERMINAL_REDUCER"]["terminal_authority"]
        )

    def test_all_green_state_proves_all_three_user_paths_ready(self):
        registry = load_registry()
        result = conductor.evaluate_ecosystem(
            registry, all_green_observed(registry)
        )

        self.assertEqual("READY", result["ecosystem_status"])
        self.assertFalse(result["safe_hold_required"])
        self.assertFalse(result["false_green_detected"])
        self.assertEqual(
            {"KALSHI_WEATHER", "LLP_TEAM_EVENT", "WOW_PROP"},
            set(result["golden_paths"]),
        )
        for path in result["golden_paths"].values():
            self.assertEqual("READY", path["status"])

    def test_broken_persistence_handoff_prevents_false_green_prop_and_llp(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        observed["handoffs"]["PERSISTENCE_TO_PRODUCT_ORCHESTRATION"] = "FAIL"

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        self.assertTrue(result["safe_hold_required"])
        self.assertTrue(result["false_green_detected"])
        self.assertEqual(
            "NOT_READY", result["golden_paths"]["WOW_PROP"]["status"]
        )
        self.assertEqual(
            "NOT_READY", result["golden_paths"]["LLP_TEAM_EVENT"]["status"]
        )
        self.assertEqual(
            "READY", result["golden_paths"]["KALSHI_WEATHER"]["status"]
        )
        self.assertIn(
            "PERSISTENCE_TO_PRODUCT_ORCHESTRATION",
            {item["handoff_id"] for item in result["broken_handoffs"]},
        )

    def test_missing_runtime_invariant_fails_closed(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        del observed["invariants"]["typed_failures_preserved"]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        self.assertTrue(result["safe_hold_required"])
        self.assertIn(
            "typed_failures_preserved",
            {item["invariant"] for item in result["invariant_violations"]},
        )

    def test_only_systems_intelligence_may_own_safe_hold_authority(self):
        registry = load_registry()
        mutated = copy.deepcopy(registry)
        mutated["components"]["ENGINEERING_CLOSURE"]["safe_hold_authority"] = True

        errors = conductor.validate_registry(mutated)

        self.assertTrue(
            any("ENGINEERING_CLOSURE may not own ecosystem SAFE_HOLD authority" in e for e in errors)
        )

    def test_conductor_cannot_acquire_probability_authority(self):
        registry = load_registry()
        mutated = copy.deepcopy(registry)
        mutated["invariants"]["conductor_probability_authority"] = True

        errors = conductor.validate_registry(mutated)

        self.assertIn(
            "invariants.conductor_probability_authority must be false",
            errors,
        )

    def test_kalshi_market_price_cannot_become_weather_probability_authority(self):
        registry = load_registry()
        mutated = copy.deepcopy(registry)
        mutated["invariants"][
            "kalshi_market_price_cannot_mutate_weather_probability"
        ] = False

        errors = conductor.validate_registry(mutated)

        self.assertIn(
            "invariants.kalshi_market_price_cannot_mutate_weather_probability must be true",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
