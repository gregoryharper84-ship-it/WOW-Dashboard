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
        "work_items": [],
    }


def work_item(
    registry,
    *,
    work_item_id="work-1",
    current_owner="WOW_BETTING_INTELLIGENCE",
    next_owner="WOW_BETTING_ENGINE",
    state="ROUTED",
    blocking_reason=None,
    terminal_state=None,
    change_class="NONE",
    verification_state="NOT_REQUIRED",
    required_verifier="NONE",
    promotion_state="NOT_APPLICABLE",
):
    return {
        "work_item_id": work_item_id,
        "request_id": "request-1",
        "objective_id": "objective-1",
        "candidate_id": "candidate-1",
        "source": "USER",
        "current_owner": current_owner,
        "next_owner": next_owner,
        "state": state,
        "blocking_reason": blocking_reason,
        "evidence_refs": ["receipt://evidence-1"],
        "specialist_route": "WOW_PROP",
        "verification_state": verification_state,
        "terminal_state": terminal_state,
        "authority_domain": registry["components"][current_owner]["authority_domain"],
        "change_class": change_class,
        "decision_right": "ROUTE_CANDIDATE",
        "required_verifier": required_verifier,
        "promotion_state": promotion_state,
    }


class EcosystemConductorTests(unittest.TestCase):
    def test_registry_is_valid_and_authority_is_separated(self):
        registry = load_registry()

        self.assertEqual([], conductor.validate_registry(registry))
        self.assertFalse(registry["can_execute"])
        self.assertFalse(registry["invariants"]["conductor_probability_authority"])
        self.assertTrue(
            registry["components"]["SYSTEMS_INTELLIGENCE_RELIABILITY"][
                "safe_hold_authority"
            ]
        )
        self.assertFalse(
            registry["components"]["WOW_ECOSYSTEM_CONDUCTOR"]["safe_hold_authority"]
        )
        self.assertTrue(
            registry["components"]["V17_TERMINAL_REDUCER"]["terminal_authority"]
        )

    def test_golden_paths_explicitly_traverse_neutral_conductor(self):
        registry = load_registry()

        for path_name, handoffs in registry["golden_paths"].items():
            specs = [registry["handoffs"][handoff] for handoff in handoffs]
            self.assertEqual("USER", specs[0]["source"], path_name)
            self.assertEqual("USER", specs[-1]["target"], path_name)
            self.assertTrue(
                any(
                    spec["source"] == "WOW_ECOSYSTEM_CONDUCTOR"
                    or spec["target"] == "WOW_ECOSYSTEM_CONDUCTOR"
                    for spec in specs
                ),
                path_name,
            )

    def test_all_green_state_proves_all_three_user_paths_ready(self):
        registry = load_registry()
        result = conductor.evaluate_ecosystem(
            registry, all_green_observed(registry)
        )

        self.assertEqual("READY", result["ecosystem_status"])
        self.assertFalse(result["safe_hold_required"])
        self.assertFalse(result["false_green_detected"])
        self.assertTrue(result["work_conservation"]["work_conservation_pass"])
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

        self.assertIn(
            "exactly SYSTEMS_INTELLIGENCE_RELIABILITY must own ecosystem SAFE_HOLD authority",
            errors,
        )

    def test_conductor_cannot_acquire_probability_or_terminal_authority(self):
        registry = load_registry()
        mutated = copy.deepcopy(registry)
        mutated["components"]["WOW_ECOSYSTEM_CONDUCTOR"]["probability_authority"] = True
        mutated["components"]["WOW_ECOSYSTEM_CONDUCTOR"]["terminal_authority"] = True

        errors = conductor.validate_registry(mutated)

        self.assertIn(
            "WOW_ECOSYSTEM_CONDUCTOR may not own probability authority", errors
        )
        self.assertIn(
            "WOW_ECOSYSTEM_CONDUCTOR may not own terminal authority", errors
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

    def test_valid_active_work_item_is_conserved(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        observed["work_items"] = [work_item(registry)]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("READY", result["ecosystem_status"])
        self.assertTrue(result["work_conservation"]["work_conservation_pass"])
        self.assertEqual(1, result["work_conservation"]["items_active"])
        self.assertEqual(0, result["work_conservation"]["items_terminal"])

    def test_terminal_work_item_requires_attributable_terminal_state(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        item = work_item(
            registry,
            state="TERMINATED",
            next_owner=None,
            terminal_state=None,
        )
        observed["work_items"] = [item]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        errors = result["work_conservation"]["invalid_items"][0]["errors"]
        self.assertTrue(any("terminal_state is required" in error for error in errors))

    def test_blocked_work_item_requires_exact_blocking_reason(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        observed["work_items"] = [
            work_item(
                registry,
                state="BLOCKED",
                next_owner="ENGINEERING_CLOSURE",
                blocking_reason=None,
            )
        ]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        errors = result["work_conservation"]["invalid_items"][0]["errors"]
        self.assertTrue(any("blocking_reason is required" in error for error in errors))

    def test_duplicate_work_item_ids_fail_conservation(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        observed["work_items"] = [
            work_item(registry, work_item_id="same"),
            work_item(registry, work_item_id="same"),
        ]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        self.assertEqual(
            ["same"], result["work_conservation"]["duplicate_work_item_ids"]
        )

    def test_authority_domain_must_match_current_owner(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        item = work_item(registry)
        item["authority_domain"] = "ENGINEERING_IMPLEMENTATION"
        observed["work_items"] = [item]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        errors = result["work_conservation"]["invalid_items"][0]["errors"]
        self.assertTrue(any("authority_domain must match" in error for error in errors))

    def test_engineering_cannot_self_verify(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        observed["work_items"] = [
            work_item(
                registry,
                current_owner="ENGINEERING_CLOSURE",
                next_owner="INDEPENDENT_VERIFICATION",
                change_class="A",
                verification_state="VERIFIED",
                required_verifier="ENGINEERING_CLOSURE",
                promotion_state="NOT_APPLICABLE",
            )
        ]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        errors = result["work_conservation"]["invalid_items"][0]["errors"]
        self.assertTrue(any("may not self-verify" in error for error in errors))

    def test_class_c_cannot_promote_without_independent_verification(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        observed["work_items"] = [
            work_item(
                registry,
                current_owner="ENGINEERING_CLOSURE",
                next_owner="INDEPENDENT_VERIFICATION",
                change_class="C",
                verification_state="PENDING",
                required_verifier="INDEPENDENT_VERIFICATION",
                promotion_state="PROMOTED",
            )
        ]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("SAFE_HOLD", result["ecosystem_status"])
        errors = result["work_conservation"]["invalid_items"][0]["errors"]
        self.assertTrue(
            any("Class C promotion requires VERIFIED" in error for error in errors)
        )

    def test_fixed_and_verified_requires_independent_verification(self):
        registry = load_registry()
        observed = all_green_observed(registry)
        observed["work_items"] = [
            work_item(
                registry,
                current_owner="ENGINEERING_CLOSURE",
                next_owner=None,
                state="TERMINATED",
                terminal_state="FIXED_AND_VERIFIED",
                change_class="A",
                verification_state="VERIFIED",
                required_verifier="INDEPENDENT_VERIFICATION",
                promotion_state="NOT_APPLICABLE",
            )
        ]

        result = conductor.evaluate_ecosystem(registry, observed)

        self.assertEqual("READY", result["ecosystem_status"])
        self.assertEqual(1, result["work_conservation"]["items_terminal"])
        self.assertEqual(1.0, result["work_conservation"]["terminal_disposition_rate"])


if __name__ == "__main__":
    unittest.main()
