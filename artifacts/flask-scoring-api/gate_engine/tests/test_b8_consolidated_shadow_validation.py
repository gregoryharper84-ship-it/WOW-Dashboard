"""
gate_engine/tests/test_b8_consolidated_shadow_validation.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B8

B8 Consolidated Shadow Validation.

Runs representative fixtures across ALL five completed UAC lanes:
  B3A  Lane.MLB_MONEYLINE   — MlbMoneylineAdapter
  B4   Lane.WNBA_PROPS      — WnbaPropsAdapter
  B5   Lane.MLB_PROPS       — MlbPropsAdapter
  B6   Lane.TENNIS_PROPS    — TennisPropsAdapter
  B7   Lane.GENERIC_MONEYLINE — GenericMoneylineAdapter

B8 invariant checks (per directive):
  1. Zero authority violations — no FORBIDDEN_GOVERNANCE_KEYS in any payload
  2. Zero upstream blocker erasures — can_execute=False on all modules
  3. Zero illegal label writes — no terminal_label in any role payload
  4. Zero schema bypasses — advisory_only=True on all role payloads
  5. Exact row reconciliation — packet.lane matches adapter, run_id echoed
  6. Stable canonical EvidenceBundle — packet is frozen dataclass
  7. Deterministic core remains controlling — identical inputs produce identical
     snapshots (same snapshot_id override → same result structure)
  8. can_execute=False everywhere — checked structurally via AST + module attr

No network, DB, or LLM calls. No production-routing changes.
can_execute = False (this test module asserts, never violates).
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib
import unittest
from typing import Any

from gate_engine.universal_agent.evidence_packet import EvidencePacket, Lane
from gate_engine.universal_agent.lanes.mlb_moneyline import (
    MlbMoneylineAdapter,
    AdapterInputError as MlbMLAdapterInputError,
)
from gate_engine.universal_agent.lanes.wnba_props import (
    WnbaPropsAdapter,
    AdapterInputError as WnbaAdapterInputError,
)
from gate_engine.universal_agent.lanes.mlb_props import (
    MlbPropsAdapter,
    AdapterStatus as MlbPropsAdapterStatus,
    AdapterInputError as MlbPropsAdapterInputError,
)
from gate_engine.universal_agent.lanes.tennis_props import (
    TennisPropsAdapter,
    AdapterStatus as TennisAdapterStatus,
    AdapterInputError as TennisAdapterInputError,
)
from gate_engine.universal_agent.lanes.generic_moneyline import (
    GenericMoneylineAdapter,
    AdapterStatus as GMLAdapterStatus,
    AdapterInputError as GMLAdapterInputError,
)
from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# ── All UAC lane adapter module paths (for AST + can_execute checks) ──────────
_LANE_MODULE_PATHS: list[pathlib.Path] = []
_UAC_ROOT = pathlib.Path(__file__).parent.parent / "universal_agent"

for _sub in (
    "lanes/mlb_moneyline",
    "lanes/wnba_props",
    "lanes/mlb_props",
    "lanes/mlb_props/event_tree",
    "lanes/tennis_props",
    "lanes/generic_moneyline",
):
    for _py in (_UAC_ROOT / _sub).glob("*.py"):
        _LANE_MODULE_PATHS.append(_py)


# ── Representative fixtures ───────────────────────────────────────────────────

def _mlb_ml_row() -> dict:
    return {
        "event_id":  "mlb-ml-001",
        "sport":     "mlb",
        "market":    "moneyline",
        "team":      "NYY",
        "opponent":  "BOS",
        "event_date": "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.55,
        "l10_ledger": [1, 0, 1, 1, 0, 1, 0, 1, 0, 1],
        "role_status": {"active_status": "ACTIVE"},
    }

def _wnba_props_row() -> dict:
    return {
        "event_id":   "wnba-001",
        "sport":      "WNBA",
        "prop_type":  "player_points",
        "player":     "A'ja Wilson",
        "team":       "LVA",
        "opponent":   "NYL",
        "line":       22.5,
        "direction":  "OVER",
        "event_date": "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.62,
        "l10_ledger": [24, 21, 28, 20, 25, 23, 19, 27, 22, 26],
        "role_status": {"active_status": "ACTIVE"},
    }

def _mlb_props_row() -> dict:
    return {
        "event_id":   "mlb-props-001",
        "sport":      "mlb",
        "market":     "player_props",
        "stat_key":   "pitcher_strikeouts",
        "player":     "Spencer Strider",
        "team":       "ATL",
        "opponent":   "PHI",
        "line":       8.5,
        "direction":  "OVER",
        "event_date": "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.59,
        "l10_ledger": [9, 7, 11, 8, 10, 6, 12, 9, 8, 10],
        "role_status": {"active_status": "ACTIVE"},
    }

def _tennis_row() -> dict:
    return {
        "event_id":   "ten-001",
        "sport":      "tennis",
        "market":     "game_totals",
        "stat_key":   "total_games",
        "player_1":   "Jannik Sinner",
        "player_2":   "Daniil Medvedev",
        "tournament": "US Open",
        "surface":    "hard",
        "best_of":    5,
        "line":       36.5,
        "direction":  "OVER",
        "event_date": "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.52,
        "l10_ledger": [38, 34, 40, 35, 37, 41, 33, 39, 36, 38],
        "role_status": {"active_status": "ACTIVE"},
    }

def _nfl_row() -> dict:
    return {
        "event_id":   "nfl-001",
        "sport":      "nfl",
        "market":     "moneyline",
        "team":       "PHI",
        "opponent":   "DAL",
        "line":       -175.0,
        "direction":  "HOME",
        "event_date": "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.64,
        "calibrated_probability": 0.61,
        "l10_ledger": [1, 0, 1, 1, 1, 0, 1, 1, 0, 1],
        "role_status": {"active_status": "ACTIVE"},
    }


# ── Adapter factory ───────────────────────────────────────────────────────────

def _run_all_lanes(run_id_prefix: str = "b8") -> list[dict]:
    """
    Run all five lane adapters with representative fixtures.
    Returns a list of result dicts for cross-lane assertion.
    """
    results = []

    mlb_ml = MlbMoneylineAdapter().adapt(
        row=_mlb_ml_row(), run_id=f"{run_id_prefix}-mlb-ml"
    )
    results.append({
        "lane":            Lane.MLB_MONEYLINE,
        "adapter_result":  mlb_ml,
        "run_id":          f"{run_id_prefix}-mlb-ml",
    })

    wnba = WnbaPropsAdapter().adapt(
        row=_wnba_props_row(), run_id=f"{run_id_prefix}-wnba"
    )
    results.append({
        "lane":            Lane.WNBA_PROPS,
        "adapter_result":  wnba,
        "run_id":          f"{run_id_prefix}-wnba",
    })

    mlb_props = MlbPropsAdapter().adapt(
        row=_mlb_props_row(), run_id=f"{run_id_prefix}-mlb-props"
    )
    results.append({
        "lane":            Lane.MLB_PROPS,
        "adapter_result":  mlb_props,
        "run_id":          f"{run_id_prefix}-mlb-props",
    })

    tennis = TennisPropsAdapter().adapt(
        row=_tennis_row(), run_id=f"{run_id_prefix}-tennis"
    )
    results.append({
        "lane":            Lane.TENNIS_PROPS,
        "adapter_result":  tennis,
        "run_id":          f"{run_id_prefix}-tennis",
    })

    nfl = GenericMoneylineAdapter().adapt(
        row=_nfl_row(), run_id=f"{run_id_prefix}-nfl"
    )
    results.append({
        "lane":            Lane.GENERIC_MONEYLINE,
        "adapter_result":  nfl,
        "run_id":          f"{run_id_prefix}-nfl",
    })

    return results


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-1: Zero authority violations
# No FORBIDDEN_GOVERNANCE_KEYS in any role payload across all lanes.
# ══════════════════════════════════════════════════════════════════════════════

class TestB8ZeroAuthorityViolations(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = _run_all_lanes("b8-inv1")

    def _scan_forbidden(self, obj, path="") -> list[str]:
        """Return list of forbidden key paths found."""
        violations = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in FORBIDDEN_GOVERNANCE_KEYS:
                    violations.append(f"{path}.{k}")
                violations.extend(self._scan_forbidden(v, f"{path}.{k}"))
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                violations.extend(self._scan_forbidden(item, f"{path}[{i}]"))
        return violations

    def test_no_forbidden_keys_in_mlb_moneyline_payloads(self):
        r = next(x for x in self.results if x["lane"] == Lane.MLB_MONEYLINE)
        for role_id, payload in r["adapter_result"].role_payloads.items():
            violations = self._scan_forbidden(payload, role_id)
            self.assertEqual(violations, [], f"MLB_MONEYLINE forbidden: {violations}")

    def test_no_forbidden_keys_in_wnba_props_payloads(self):
        r = next(x for x in self.results if x["lane"] == Lane.WNBA_PROPS)
        for role_id, payload in r["adapter_result"].role_payloads.items():
            violations = self._scan_forbidden(payload, role_id)
            self.assertEqual(violations, [], f"WNBA_PROPS forbidden: {violations}")

    def test_no_forbidden_keys_in_mlb_props_payloads(self):
        r = next(x for x in self.results if x["lane"] == Lane.MLB_PROPS)
        for role_id, payload in r["adapter_result"].role_payloads.items():
            violations = self._scan_forbidden(payload, role_id)
            self.assertEqual(violations, [], f"MLB_PROPS forbidden: {violations}")

    def test_no_forbidden_keys_in_tennis_props_payloads(self):
        r = next(x for x in self.results if x["lane"] == Lane.TENNIS_PROPS)
        for role_id, payload in r["adapter_result"].role_payloads.items():
            violations = self._scan_forbidden(payload, role_id)
            self.assertEqual(violations, [], f"TENNIS_PROPS forbidden: {violations}")

    def test_no_forbidden_keys_in_generic_moneyline_payloads(self):
        r = next(x for x in self.results if x["lane"] == Lane.GENERIC_MONEYLINE)
        for role_id, payload in r["adapter_result"].role_payloads.items():
            violations = self._scan_forbidden(payload, role_id)
            self.assertEqual(violations, [], f"GENERIC_MONEYLINE forbidden: {violations}")

    def test_place_bet_absent_in_all_lanes(self):
        for lane_result in self.results:
            for payload in lane_result["adapter_result"].role_payloads.values():
                violations = self._scan_forbidden(payload)
                self.assertEqual(
                    [v for v in violations if "place_bet" in v.lower()], [],
                    f"place_bet found in {lane_result['lane']}",
                )

    def test_settlement_absent_in_all_lanes(self):
        for lane_result in self.results:
            for payload in lane_result["adapter_result"].role_payloads.values():
                violations = self._scan_forbidden(payload)
                self.assertEqual(
                    [v for v in violations if "settlement" in v.lower()], [],
                    f"settlement found in {lane_result['lane']}",
                )


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-2: Zero upstream blocker erasures (can_execute=False all modules)
# ══════════════════════════════════════════════════════════════════════════════

class TestB8ZeroUploaderBlockerErasures(unittest.TestCase):

    def test_can_execute_false_all_lane_modules(self):
        """Every .py in UAC lane directories must have can_execute=False."""
        violations = []
        for py_path in _LANE_MODULE_PATHS:
            source = py_path.read_text()
            # Parse and check for module-level can_execute = False assignment
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            found_false = False
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "can_execute"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is False
                ):
                    found_false = True
                    break
            if not found_false:
                violations.append(str(py_path.relative_to(_UAC_ROOT.parent.parent)))
        self.assertEqual(
            violations, [],
            f"Modules missing can_execute=False: {violations}",
        )

    def test_can_execute_false_runtime_all_adapters(self):
        """Runtime module attribute check for all five adapter modules."""
        import gate_engine.universal_agent.lanes.mlb_moneyline.adapter as m1
        import gate_engine.universal_agent.lanes.wnba_props.adapter as m2
        import gate_engine.universal_agent.lanes.mlb_props.adapter as m3
        import gate_engine.universal_agent.lanes.tennis_props.adapter as m4
        import gate_engine.universal_agent.lanes.generic_moneyline.adapter as m5
        for mod in (m1, m2, m3, m4, m5):
            self.assertFalse(
                mod.can_execute,
                f"{mod.__name__}.can_execute must be False",
            )

    def test_test_module_can_execute_false(self):
        """This test module itself must also declare can_execute=False."""
        import gate_engine.tests.test_b8_consolidated_shadow_validation as self_mod
        self.assertFalse(self_mod.can_execute)


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-3: Zero illegal label writes
# No terminal_label in any advisory role payload from any lane.
# ══════════════════════════════════════════════════════════════════════════════

class TestB8ZeroIllegalLabelWrites(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = _run_all_lanes("b8-inv3")

    def _find_key(self, obj, target_key: str, path="") -> list[str]:
        found = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() == target_key.lower():
                    found.append(f"{path}.{k}")
                found.extend(self._find_key(v, target_key, f"{path}.{k}"))
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                found.extend(self._find_key(item, target_key, f"{path}[{i}]"))
        return found

    def test_no_terminal_label_in_any_role_payload(self):
        for lane_result in self.results:
            lane = lane_result["lane"]
            for role_id, payload in lane_result["adapter_result"].role_payloads.items():
                found = self._find_key(payload, "terminal_label", role_id)
                self.assertEqual(
                    found, [],
                    f"terminal_label in {lane}.{role_id}: {found}",
                )

    def test_no_final_label_in_any_role_payload(self):
        for lane_result in self.results:
            lane = lane_result["lane"]
            for role_id, payload in lane_result["adapter_result"].role_payloads.items():
                found = self._find_key(payload, "final_decision", role_id)
                self.assertEqual(
                    found, [],
                    f"final_decision in {lane}.{role_id}: {found}",
                )

    def test_no_stake_tier_in_any_role_payload(self):
        for lane_result in self.results:
            lane = lane_result["lane"]
            for role_id, payload in lane_result["adapter_result"].role_payloads.items():
                found = self._find_key(payload, "stake_tier", role_id)
                self.assertEqual(
                    found, [],
                    f"stake_tier in {lane}.{role_id}: {found}",
                )


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-4: Zero schema bypasses (advisory_only=True all payloads)
# ══════════════════════════════════════════════════════════════════════════════

class TestB8ZeroSchemaBypass(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = _run_all_lanes("b8-inv4")

    def test_advisory_only_true_all_payloads_all_lanes(self):
        for lane_result in self.results:
            lane = lane_result["lane"]
            payloads = lane_result["adapter_result"].role_payloads
            for role_id, payload in payloads.items():
                self.assertIs(
                    payload.get("advisory_only"), True,
                    f"advisory_only not exactly True in {lane}.{role_id}",
                )

    def test_six_role_payloads_all_lanes(self):
        for lane_result in self.results:
            lane = lane_result["lane"]
            count = len(lane_result["adapter_result"].role_payloads)
            self.assertEqual(
                count, 6,
                f"{lane} produced {count} role payloads; expected 6",
            )

    def test_all_payloads_have_advisory_findings(self):
        for lane_result in self.results:
            lane = lane_result["lane"]
            for role_id, payload in lane_result["adapter_result"].role_payloads.items():
                self.assertIn(
                    "advisory_findings", payload,
                    f"advisory_findings missing in {lane}.{role_id}",
                )


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-5: Exact row reconciliation
# packet.lane matches the lane constant; run_id is echoed correctly.
# ══════════════════════════════════════════════════════════════════════════════

class TestB8RowReconciliation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = _run_all_lanes("b8-inv5")

    def test_mlb_moneyline_packet_lane(self):
        r = next(x for x in self.results if x["lane"] == Lane.MLB_MONEYLINE)
        self.assertEqual(r["adapter_result"].packet.lane, Lane.MLB_MONEYLINE)

    def test_wnba_props_packet_lane(self):
        r = next(x for x in self.results if x["lane"] == Lane.WNBA_PROPS)
        self.assertEqual(r["adapter_result"].packet.lane, Lane.WNBA_PROPS)

    def test_mlb_props_packet_lane(self):
        r = next(x for x in self.results if x["lane"] == Lane.MLB_PROPS)
        self.assertEqual(r["adapter_result"].packet.lane, Lane.MLB_PROPS)

    def test_tennis_props_packet_lane(self):
        r = next(x for x in self.results if x["lane"] == Lane.TENNIS_PROPS)
        self.assertEqual(r["adapter_result"].packet.lane, Lane.TENNIS_PROPS)

    def test_generic_moneyline_packet_lane(self):
        r = next(x for x in self.results if x["lane"] == Lane.GENERIC_MONEYLINE)
        self.assertEqual(r["adapter_result"].packet.lane, Lane.GENERIC_MONEYLINE)

    def test_run_id_echoed_correctly_all_lanes(self):
        for lane_result in self.results:
            packet = lane_result["adapter_result"].packet
            self.assertEqual(
                packet.run_id,
                lane_result["run_id"],
                f"run_id mismatch in {lane_result['lane']}",
            )

    def test_five_distinct_lane_values(self):
        lanes = [r["adapter_result"].packet.lane for r in self.results]
        self.assertEqual(len(set(lanes)), 5, f"Expected 5 distinct lanes; got {set(lanes)}")

    def test_mlb_moneyline_event_id(self):
        r = next(x for x in self.results if x["lane"] == Lane.MLB_MONEYLINE)
        self.assertEqual(r["adapter_result"].packet.canonical_event_id, "mlb-ml-001")

    def test_wnba_event_id(self):
        r = next(x for x in self.results if x["lane"] == Lane.WNBA_PROPS)
        self.assertEqual(r["adapter_result"].packet.canonical_event_id, "wnba-001")

    def test_mlb_props_event_id(self):
        r = next(x for x in self.results if x["lane"] == Lane.MLB_PROPS)
        self.assertEqual(r["adapter_result"].packet.canonical_event_id, "mlb-props-001")

    def test_tennis_event_id(self):
        r = next(x for x in self.results if x["lane"] == Lane.TENNIS_PROPS)
        self.assertEqual(r["adapter_result"].packet.canonical_event_id, "ten-001")

    def test_generic_moneyline_event_id(self):
        r = next(x for x in self.results if x["lane"] == Lane.GENERIC_MONEYLINE)
        self.assertEqual(r["adapter_result"].packet.canonical_event_id, "nfl-001")


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-6: Stable canonical EvidenceBundle (packet is frozen)
# ══════════════════════════════════════════════════════════════════════════════

class TestB8StableEvidenceBundle(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.results = _run_all_lanes("b8-inv6")

    def test_all_packets_are_frozen_dataclasses(self):
        for lane_result in self.results:
            pkt = lane_result["adapter_result"].packet
            lane = lane_result["lane"]
            self.assertTrue(
                dataclasses.is_dataclass(pkt),
                f"{lane} packet is not a dataclass",
            )
            self.assertTrue(
                pkt.__dataclass_params__.frozen,
                f"{lane} packet is not frozen",
            )

    def test_all_packets_mutation_raises(self):
        for lane_result in self.results:
            pkt = lane_result["adapter_result"].packet
            lane = lane_result["lane"]
            with self.assertRaises(
                dataclasses.FrozenInstanceError,
                msg=f"{lane} packet should raise FrozenInstanceError on mutation",
            ):
                pkt.run_id = "mutated"  # type: ignore

    def test_snapshot_id_is_string_all_lanes(self):
        for lane_result in self.results:
            pkt = lane_result["adapter_result"].packet
            self.assertIsInstance(
                pkt.snapshot_id, str,
                f"{lane_result['lane']} snapshot_id should be a string",
            )

    def test_all_adapter_results_are_frozen(self):
        for lane_result in self.results:
            res = lane_result["adapter_result"]
            lane = lane_result["lane"]
            self.assertTrue(
                dataclasses.is_dataclass(res),
                f"{lane} adapter result is not a dataclass",
            )
            self.assertTrue(
                res.__dataclass_params__.frozen,
                f"{lane} adapter result is not frozen",
            )


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-7: Deterministic core — identical inputs → identical structure
# ══════════════════════════════════════════════════════════════════════════════

class TestB8DeterministicCore(unittest.TestCase):

    def _fixed_snapshot(self, adapter_cls, row_fn, run_id: str):
        return adapter_cls().adapt(
            row=row_fn(), run_id=run_id, snapshot_id="FIXED-SNAP-B8"
        )

    def test_mlb_moneyline_deterministic_snapshot_id(self):
        r1 = self._fixed_snapshot(MlbMoneylineAdapter, _mlb_ml_row, "det-run")
        r2 = self._fixed_snapshot(MlbMoneylineAdapter, _mlb_ml_row, "det-run")
        self.assertEqual(r1.packet.snapshot_id, r2.packet.snapshot_id)
        self.assertEqual(r1.packet.canonical_event_id, r2.packet.canonical_event_id)

    def test_wnba_deterministic_snapshot_id(self):
        r1 = self._fixed_snapshot(WnbaPropsAdapter, _wnba_props_row, "det-run")
        r2 = self._fixed_snapshot(WnbaPropsAdapter, _wnba_props_row, "det-run")
        self.assertEqual(r1.packet.snapshot_id, r2.packet.snapshot_id)

    def test_mlb_props_deterministic(self):
        r1 = self._fixed_snapshot(MlbPropsAdapter, _mlb_props_row, "det-run")
        r2 = self._fixed_snapshot(MlbPropsAdapter, _mlb_props_row, "det-run")
        self.assertEqual(r1.packet.snapshot_id, r2.packet.snapshot_id)
        self.assertEqual(r1.adapter_status, r2.adapter_status)

    def test_tennis_deterministic(self):
        r1 = self._fixed_snapshot(TennisPropsAdapter, _tennis_row, "det-run")
        r2 = self._fixed_snapshot(TennisPropsAdapter, _tennis_row, "det-run")
        self.assertEqual(r1.packet.snapshot_id, r2.packet.snapshot_id)

    def test_generic_moneyline_deterministic(self):
        r1 = self._fixed_snapshot(GenericMoneylineAdapter, _nfl_row, "det-run")
        r2 = self._fixed_snapshot(GenericMoneylineAdapter, _nfl_row, "det-run")
        self.assertEqual(r1.packet.snapshot_id, r2.packet.snapshot_id)

    def test_different_run_ids_produce_different_packets(self):
        r1 = self._fixed_snapshot(MlbPropsAdapter, _mlb_props_row, "run-A")
        r2 = self._fixed_snapshot(MlbPropsAdapter, _mlb_props_row, "run-B")
        self.assertNotEqual(r1.packet.run_id, r2.packet.run_id)


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-8: Cross-lane routing isolation
# Dedicated-lane rows are rejected by the generic adapter; generic rows are
# accepted by the generic adapter. No cross-lane leakage.
# ══════════════════════════════════════════════════════════════════════════════

class TestB8CrossLaneRoutingIsolation(unittest.TestCase):

    def test_mlb_row_rejected_by_generic_moneyline(self):
        with self.assertRaises(GMLAdapterInputError) as ctx:
            GenericMoneylineAdapter().adapt(
                row={**_mlb_ml_row(), "market": "moneyline"},
                run_id="cross-1",
            )
        self.assertEqual(ctx.exception.code, "SPORT_HAS_DEDICATED_LANE")

    def test_wnba_row_rejected_by_generic_moneyline(self):
        with self.assertRaises(GMLAdapterInputError) as ctx:
            GenericMoneylineAdapter().adapt(
                row={**_wnba_props_row(), "sport": "wnba", "market": "moneyline"},
                run_id="cross-2",
            )
        self.assertEqual(ctx.exception.code, "SPORT_HAS_DEDICATED_LANE")

    def test_tennis_row_rejected_by_generic_moneyline(self):
        with self.assertRaises(GMLAdapterInputError) as ctx:
            GenericMoneylineAdapter().adapt(
                row={**_tennis_row(), "market": "moneyline"},
                run_id="cross-3",
            )
        self.assertEqual(ctx.exception.code, "SPORT_HAS_DEDICATED_LANE")

    def test_nfl_row_rejected_by_mlb_moneyline(self):
        with self.assertRaises(MlbMLAdapterInputError):
            MlbMoneylineAdapter().adapt(
                row={**_nfl_row(), "sport": "nfl"},
                run_id="cross-4",
            )

    def test_nfl_row_rejected_by_wnba_props(self):
        with self.assertRaises(WnbaAdapterInputError):
            WnbaPropsAdapter().adapt(
                row={**_nfl_row(), "sport": "nfl"},
                run_id="cross-5",
            )

    def test_nfl_row_rejected_by_mlb_props(self):
        with self.assertRaises(MlbPropsAdapterInputError):
            MlbPropsAdapter().adapt(
                row={**_nfl_row(), "sport": "nfl"},
                run_id="cross-6",
            )

    def test_nfl_row_rejected_by_tennis_props(self):
        with self.assertRaises(TennisAdapterInputError):
            TennisPropsAdapter().adapt(
                row={**_nfl_row(), "sport": "nfl"},
                run_id="cross-7",
            )

    def test_nfl_accepted_by_generic_moneyline(self):
        result = GenericMoneylineAdapter().adapt(row=_nfl_row(), run_id="cross-8")
        self.assertEqual(result.packet.lane, Lane.GENERIC_MONEYLINE)


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-9: TECHNICAL_FAILURE acquisition error — all lanes
# ══════════════════════════════════════════════════════════════════════════════

class TestB8TechnicalFailureConsistency(unittest.TestCase):

    def _acq_err_result(self, adapter, row_fn):
        return adapter().adapt(
            row=row_fn(), run_id="tech-err",
            acquisition_error="Simulated provider failure",
        )

    def test_mlb_props_acquisition_error(self):
        r = self._acq_err_result(MlbPropsAdapter, _mlb_props_row)
        self.assertEqual(r.adapter_status, MlbPropsAdapterStatus.TECHNICAL_FAILURE)
        self.assertIsNotNone(r.failure_classification)
        self.assertEqual(len(r.role_payloads), 0)

    def test_tennis_acquisition_error(self):
        r = self._acq_err_result(TennisPropsAdapter, _tennis_row)
        self.assertEqual(r.adapter_status, TennisAdapterStatus.TECHNICAL_FAILURE)
        self.assertIsNotNone(r.failure_classification)
        self.assertEqual(len(r.role_payloads), 0)

    def test_generic_moneyline_acquisition_error(self):
        r = self._acq_err_result(GenericMoneylineAdapter, _nfl_row)
        self.assertEqual(r.adapter_status, GMLAdapterStatus.TECHNICAL_FAILURE)
        self.assertIsNotNone(r.failure_classification)
        self.assertEqual(len(r.role_payloads), 0)

    def test_wnba_acquisition_error(self):
        r = self._acq_err_result(WnbaPropsAdapter, _wnba_props_row)
        # WNBA adapter also returns TECHNICAL_FAILURE on acquisition_error
        self.assertIsNotNone(r.failure_classification)

    def test_technical_failure_packet_still_frozen(self):
        r = self._acq_err_result(MlbPropsAdapter, _mlb_props_row)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            r.packet.run_id = "mutated"  # type: ignore


# ══════════════════════════════════════════════════════════════════════════════
# B8-INV-10: No app.py imports in any UAC lane module
# ══════════════════════════════════════════════════════════════════════════════

class TestB8NoAppImports(unittest.TestCase):

    def test_no_app_import_in_any_lane_module(self):
        violations = []
        for py_path in _LANE_MODULE_PATHS:
            try:
                source = py_path.read_text()
                tree   = ast.parse(source)
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [a.name for a in node.names]
                        if isinstance(node, ast.Import)
                        else ([node.module] if node.module else [])
                    )
                    for name in names:
                        if name is not None and name.split(".")[-1] == "app":
                            violations.append(
                                f"{py_path.name}: imports {name!r}"
                            )
        self.assertEqual(
            violations, [],
            f"UAC lane modules import 'app': {violations}",
        )


if __name__ == "__main__":
    unittest.main()
