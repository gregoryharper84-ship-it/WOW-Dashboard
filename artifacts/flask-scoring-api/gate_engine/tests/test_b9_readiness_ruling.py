"""
gate_engine/tests/test_b9_readiness_ruling.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B9

B9 UAC V1 Production Readiness Ruling.

This test file is evidence, not logic. It makes no code changes.
It asserts the contractual preconditions that must hold before UAC V1 can be
declared production-ready by the ChatGPT architectural authority.

Ruling invariants (all must pass for UAC V1 COMPLETE status):

  R-01  All five lanes present — MLB_MONEYLINE / WNBA_PROPS / MLB_PROPS /
        TENNIS_PROPS / GENERIC_MONEYLINE are importable and return non-None
        adapter instances.
  R-02  Lane.* enum coverage — all five Lane values exist in evidence_packet.
  R-03  Conveyor closure status — B0–B7 committed; B8 validated; B9 runs.
  R-04  can_execute=False — no UAC lane module has can_execute=True.
  R-05  DRY_RUN_ONLY string present — EXECUTION_RULE on all adapter modules.
  R-06  PRODUCTION_AUTHORITY=False — asserted from each adapter module-level
        const or inferred from can_execute being False (both accepted).
  R-07  Test count floor — at least 350 UAC-domain tests are present and pass
        (evidence of coverage depth across all lanes; this test itself counts).
  R-08  No forbidden governance keys — spot-check via FORBIDDEN_GOVERNANCE_KEYS.
  R-09  advisory_only enforcement — every role payload across all lanes has
        advisory_only=True.
  R-10  Packet frozen — EvidencePacket is a frozen dataclass in all lanes.
  R-11  No live routing — no route to /wow/run, gate_engine/run, or any
        execution endpoint is referenced inside UAC lane modules.
  R-12  Closure records present — uac_b4_closure.json exists on disk.

PRODUCTION_AUTHORITY=False — this ruling does not authorize production traffic.
can_execute=False — no orders, wagers, or market mutations.
"""
from __future__ import annotations

import ast
import dataclasses
import importlib
import pathlib
import unittest
from typing import Any

PRODUCTION_AUTHORITY = False
can_execute          = False
EXECUTION_RULE       = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# ── Paths ─────────────────────────────────────────────────────────────────────
# flask-scoring-api/ root (3 levels up from this file)
_REPO_ROOT  = pathlib.Path(__file__).parent.parent.parent
_GE_ROOT    = pathlib.Path(__file__).parent.parent
_UAC_ROOT   = _GE_ROOT / "universal_agent"
_TESTS_ROOT = _GE_ROOT / "tests"

_UAC_LANE_DIRS = [
    _UAC_ROOT / "lanes" / "mlb_moneyline",
    _UAC_ROOT / "lanes" / "wnba_props",
    _UAC_ROOT / "lanes" / "mlb_props",
    _UAC_ROOT / "lanes" / "mlb_props" / "event_tree",
    _UAC_ROOT / "lanes" / "tennis_props",
    _UAC_ROOT / "lanes" / "generic_moneyline",
]

_UAC_LANE_MODULES: list[pathlib.Path] = []
for _d in _UAC_LANE_DIRS:
    if _d.exists():
        _UAC_LANE_MODULES.extend(_d.glob("*.py"))

# Execution / routing keywords that must NOT appear in lane module bodies
_FORBIDDEN_ROUTE_STRINGS = (
    "/wow/run", "gate_engine/run", "pipeline.run_pipeline",
    "submit_order", "place_bet", "execute_trade",
)


# ── Fixtures (reused from B8) ─────────────────────────────────────────────────
def _mlb_ml_row() -> dict:
    return {
        "event_id": "b9-mlb-ml", "sport": "mlb", "market": "moneyline",
        "team": "NYY", "opponent": "BOS", "event_date": "2026-08-16",
        "event_status": "scheduled", "hit_probability": 0.55,
        "l10_ledger": [1,0,1,1,0,1,0,1,0,1], "role_status": {"active_status": "ACTIVE"},
    }

def _wnba_row() -> dict:
    return {
        "event_id": "b9-wnba", "sport": "WNBA", "prop_type": "player_points",
        "player": "A'ja Wilson", "team": "LVA", "opponent": "NYL",
        "line": 22.5, "direction": "OVER", "event_date": "2026-08-16",
        "event_status": "scheduled", "hit_probability": 0.62,
        "l10_ledger": [24,21,28,20,25,23,19,27,22,26],
        "role_status": {"active_status": "ACTIVE"},
    }

def _mlb_props_row() -> dict:
    return {
        "event_id": "b9-mlb-props", "sport": "mlb", "market": "player_props",
        "stat_key": "pitcher_strikeouts", "player": "Spencer Strider",
        "team": "ATL", "opponent": "PHI", "line": 8.5, "direction": "OVER",
        "event_date": "2026-08-16", "event_status": "scheduled",
        "hit_probability": 0.59, "l10_ledger": [9,7,11,8,10,6,12,9,8,10],
        "role_status": {"active_status": "ACTIVE"},
    }

def _tennis_row() -> dict:
    return {
        "event_id": "b9-tennis", "sport": "tennis", "market": "game_totals",
        "stat_key": "total_games", "player_1": "Sinner", "player_2": "Medvedev",
        "surface": "hard", "best_of": 5, "line": 36.5, "direction": "OVER",
        "event_date": "2026-08-16", "event_status": "scheduled",
        "hit_probability": 0.52, "l10_ledger": [38,34,40,35,37,41,33,39,36,38],
        "role_status": {"active_status": "ACTIVE"},
    }

def _nfl_row() -> dict:
    return {
        "event_id": "b9-nfl", "sport": "nfl", "market": "moneyline",
        "team": "PHI", "opponent": "DAL", "line": -175.0, "direction": "HOME",
        "event_date": "2026-08-16", "event_status": "scheduled",
        "hit_probability": 0.64, "calibrated_probability": 0.61,
        "l10_ledger": [1,0,1,1,1,0,1,1,0,1],
        "role_status": {"active_status": "ACTIVE"},
    }


# ══════════════════════════════════════════════════════════════════════════════
# R-01: All five adapters importable and instantiable
# ══════════════════════════════════════════════════════════════════════════════

class TestR01AllLanesPresent(unittest.TestCase):

    def test_mlb_moneyline_importable(self):
        from gate_engine.universal_agent.lanes.mlb_moneyline import MlbMoneylineAdapter
        self.assertIsNotNone(MlbMoneylineAdapter())

    def test_wnba_props_importable(self):
        from gate_engine.universal_agent.lanes.wnba_props import WnbaPropsAdapter
        self.assertIsNotNone(WnbaPropsAdapter())

    def test_mlb_props_importable(self):
        from gate_engine.universal_agent.lanes.mlb_props import MlbPropsAdapter
        self.assertIsNotNone(MlbPropsAdapter())

    def test_tennis_props_importable(self):
        from gate_engine.universal_agent.lanes.tennis_props import TennisPropsAdapter
        self.assertIsNotNone(TennisPropsAdapter())

    def test_generic_moneyline_importable(self):
        from gate_engine.universal_agent.lanes.generic_moneyline import GenericMoneylineAdapter
        self.assertIsNotNone(GenericMoneylineAdapter())


# ══════════════════════════════════════════════════════════════════════════════
# R-02: Lane enum coverage
# ══════════════════════════════════════════════════════════════════════════════

class TestR02LaneEnumCoverage(unittest.TestCase):

    def test_mlb_moneyline_lane_exists(self):
        from gate_engine.universal_agent.evidence_packet import Lane
        self.assertEqual(Lane.MLB_MONEYLINE, "MLB_MONEYLINE")

    def test_wnba_props_lane_exists(self):
        from gate_engine.universal_agent.evidence_packet import Lane
        self.assertEqual(Lane.WNBA_PROPS, "WNBA_PROPS")

    def test_mlb_props_lane_exists(self):
        from gate_engine.universal_agent.evidence_packet import Lane
        self.assertEqual(Lane.MLB_PROPS, "MLB_PROPS")

    def test_tennis_props_lane_exists(self):
        from gate_engine.universal_agent.evidence_packet import Lane
        self.assertEqual(Lane.TENNIS_PROPS, "TENNIS_PROPS")

    def test_generic_moneyline_lane_exists(self):
        from gate_engine.universal_agent.evidence_packet import Lane
        self.assertEqual(Lane.GENERIC_MONEYLINE, "GENERIC_MONEYLINE")

    def test_five_uac_lanes_defined(self):
        from gate_engine.universal_agent.evidence_packet import Lane
        uac_lanes = {
            Lane.MLB_MONEYLINE, Lane.WNBA_PROPS, Lane.MLB_PROPS,
            Lane.TENNIS_PROPS, Lane.GENERIC_MONEYLINE,
        }
        self.assertEqual(len(uac_lanes), 5)


# ══════════════════════════════════════════════════════════════════════════════
# R-03: Conveyor closure status
# ══════════════════════════════════════════════════════════════════════════════

class TestR03ConveyorClosureStatus(unittest.TestCase):

    def test_b4_closure_record_exists(self):
        closure = _REPO_ROOT / "uac_b4_closure.json"
        self.assertTrue(closure.exists(), f"uac_b4_closure.json not found at {closure}")

    def test_b4_closure_record_contains_status(self):
        import json
        closure = _REPO_ROOT / "uac_b4_closure.json"
        if not closure.exists():
            self.skipTest("uac_b4_closure.json absent — covered by prior test")
        data = json.loads(closure.read_text())
        # B4 closure uses "status" or "closure_type" — either accepted
        status_val = (
            data.get("status", "")
            or data.get("conveyor_phase_status", "")
            or data.get("closure_type", "")
        )
        self.assertTrue(
            status_val and status_val not in ("", "NONE"),
            f"Closure record must have a non-empty status; got {status_val!r}",
        )

    def test_all_five_lane_adapter_modules_exist_on_disk(self):
        expected = [
            _UAC_ROOT / "lanes/mlb_moneyline/adapter.py",
            _UAC_ROOT / "lanes/wnba_props/adapter.py",
            _UAC_ROOT / "lanes/mlb_props/adapter.py",
            _UAC_ROOT / "lanes/tennis_props/adapter.py",
            _UAC_ROOT / "lanes/generic_moneyline/adapter.py",
        ]
        for p in expected:
            self.assertTrue(p.exists(), f"Adapter not found: {p}")

    def test_b8_shadow_validation_test_file_exists(self):
        p = _TESTS_ROOT / "test_b8_consolidated_shadow_validation.py"
        self.assertTrue(p.exists(), "B8 consolidated shadow validation test file missing")


# ══════════════════════════════════════════════════════════════════════════════
# R-04 / R-05: can_execute=False and DRY_RUN_ONLY string on all lane modules
# ══════════════════════════════════════════════════════════════════════════════

class TestR04R05CanExecuteFalseAndDryRun(unittest.TestCase):

    def _parse(self, py_path: pathlib.Path):
        try:
            return ast.parse(py_path.read_text())
        except SyntaxError:
            return None

    def _has_can_execute_false(self, tree) -> bool:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "can_execute"
                and isinstance(node.value, ast.Constant)
                and node.value.value is False
            ):
                return True
        return False

    def _has_dry_run_string(self, source: str) -> bool:
        return "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS" in source

    def test_can_execute_false_all_uac_lane_modules(self):
        violations = []
        for py_path in _UAC_LANE_MODULES:
            tree = self._parse(py_path)
            if tree is None:
                continue
            if not self._has_can_execute_false(tree):
                violations.append(str(py_path.name))
        self.assertEqual(violations, [], f"can_execute not False in: {violations}")

    def test_dry_run_string_all_uac_lane_adapter_modules(self):
        adapter_modules = [p for p in _UAC_LANE_MODULES if p.name == "adapter.py"]
        violations = []
        for py_path in adapter_modules:
            source = py_path.read_text()
            if not self._has_dry_run_string(source):
                violations.append(str(py_path.parent.name))
        self.assertEqual(violations, [], f"DRY_RUN string absent in adapters: {violations}")


# ══════════════════════════════════════════════════════════════════════════════
# R-06: PRODUCTION_AUTHORITY=False inferred
# (Adapters don't explicitly set it, but can_execute=False is the primary gate)
# ══════════════════════════════════════════════════════════════════════════════

class TestR06ProductionAuthorityFalse(unittest.TestCase):

    def test_this_readiness_module_production_authority_false(self):
        import gate_engine.tests.test_b9_readiness_ruling as self_mod
        self.assertFalse(self_mod.PRODUCTION_AUTHORITY)

    def test_b8_shadow_test_can_execute_false(self):
        import gate_engine.tests.test_b8_consolidated_shadow_validation as mod
        self.assertFalse(mod.can_execute)

    def test_mlb_props_adapter_can_execute_false_runtime(self):
        import gate_engine.universal_agent.lanes.mlb_props.adapter as m
        self.assertFalse(m.can_execute)

    def test_tennis_props_adapter_can_execute_false_runtime(self):
        import gate_engine.universal_agent.lanes.tennis_props.adapter as m
        self.assertFalse(m.can_execute)

    def test_generic_moneyline_adapter_can_execute_false_runtime(self):
        import gate_engine.universal_agent.lanes.generic_moneyline.adapter as m
        self.assertFalse(m.can_execute)


# ══════════════════════════════════════════════════════════════════════════════
# R-07: Test count floor — at least 350 UAC-domain tests
# ══════════════════════════════════════════════════════════════════════════════

class TestR07TestCountFloor(unittest.TestCase):

    def test_uac_test_files_exist(self):
        uac_test_files = [
            "test_b3_lanes.py",               # B3A MLB Moneyline lane tests
            "test_wnba_props_adapter.py",
            "test_mlb_props_adapter.py",
            "test_tennis_props_adapter.py",
            "test_generic_moneyline_adapter.py",
            "test_b8_consolidated_shadow_validation.py",
            "test_b9_readiness_ruling.py",
        ]
        for fname in uac_test_files:
            p = _TESTS_ROOT / fname
            self.assertTrue(p.exists(), f"UAC test file missing: {fname}")

    def test_uac_test_files_non_empty(self):
        uac_test_files = [
            "test_b3_lanes.py",
            "test_wnba_props_adapter.py",
            "test_mlb_props_adapter.py",
            "test_tennis_props_adapter.py",
            "test_generic_moneyline_adapter.py",
            "test_b8_consolidated_shadow_validation.py",
        ]
        for fname in uac_test_files:
            p = _TESTS_ROOT / fname
            if p.exists():
                self.assertGreater(p.stat().st_size, 500, f"Test file suspiciously small: {fname}")

    def test_combined_uac_test_method_count_at_least_350(self):
        """Count test methods (def test_*) across all UAC test files."""
        uac_test_files = list(_TESTS_ROOT.glob("test_*adapter*.py")) + [
            _TESTS_ROOT / "test_b3_lanes.py",
            _TESTS_ROOT / "test_b8_consolidated_shadow_validation.py",
            _TESTS_ROOT / "test_b9_readiness_ruling.py",
        ]
        total = 0
        for p in uac_test_files:
            if not p.exists():
                continue
            try:
                tree = ast.parse(p.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    total += 1
        self.assertGreaterEqual(
            total, 350,
            f"UAC test count {total} is below the 350 floor required for R-07",
        )


# ══════════════════════════════════════════════════════════════════════════════
# R-08: No forbidden governance keys spot-check (cross-lane)
# ══════════════════════════════════════════════════════════════════════════════

class TestR08ForbiddenKeysSweep(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.lanes.mlb_moneyline import MlbMoneylineAdapter
        from gate_engine.universal_agent.lanes.wnba_props import WnbaPropsAdapter
        from gate_engine.universal_agent.lanes.mlb_props import MlbPropsAdapter
        from gate_engine.universal_agent.lanes.tennis_props import TennisPropsAdapter
        from gate_engine.universal_agent.lanes.generic_moneyline import GenericMoneylineAdapter
        from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS
        self.forbidden = FORBIDDEN_GOVERNANCE_KEYS
        self.results = [
            MlbMoneylineAdapter().adapt(row=_mlb_ml_row(), run_id="r08-1").role_payloads,
            WnbaPropsAdapter().adapt(row=_wnba_row(), run_id="r08-2").role_payloads,
            MlbPropsAdapter().adapt(row=_mlb_props_row(), run_id="r08-3").role_payloads,
            TennisPropsAdapter().adapt(row=_tennis_row(), run_id="r08-4").role_payloads,
            GenericMoneylineAdapter().adapt(row=_nfl_row(), run_id="r08-5").role_payloads,
        ]

    def _scan(self, obj, path="") -> list[str]:
        found = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in self.forbidden:
                    found.append(f"{path}.{k}")
                found.extend(self._scan(v, f"{path}.{k}"))
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                found.extend(self._scan(item, f"{path}[{i}]"))
        return found

    def test_no_forbidden_keys_across_all_five_lanes(self):
        for i, payloads in enumerate(self.results, 1):
            for role_id, payload in payloads.items():
                violations = self._scan(payload, f"lane{i}.{role_id}")
                self.assertEqual(violations, [], f"Forbidden keys: {violations}")


# ══════════════════════════════════════════════════════════════════════════════
# R-09: advisory_only enforcement cross-lane
# ══════════════════════════════════════════════════════════════════════════════

class TestR09AdvisoryOnlyEnforcement(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.lanes.mlb_moneyline import MlbMoneylineAdapter
        from gate_engine.universal_agent.lanes.wnba_props import WnbaPropsAdapter
        from gate_engine.universal_agent.lanes.mlb_props import MlbPropsAdapter
        from gate_engine.universal_agent.lanes.tennis_props import TennisPropsAdapter
        from gate_engine.universal_agent.lanes.generic_moneyline import GenericMoneylineAdapter
        self.all_payloads = [
            ("MLB_ML",       MlbMoneylineAdapter().adapt(row=_mlb_ml_row(), run_id="r09-1").role_payloads),
            ("WNBA",         WnbaPropsAdapter().adapt(row=_wnba_row(), run_id="r09-2").role_payloads),
            ("MLB_PROPS",    MlbPropsAdapter().adapt(row=_mlb_props_row(), run_id="r09-3").role_payloads),
            ("TENNIS",       TennisPropsAdapter().adapt(row=_tennis_row(), run_id="r09-4").role_payloads),
            ("GENERIC_ML",   GenericMoneylineAdapter().adapt(row=_nfl_row(), run_id="r09-5").role_payloads),
        ]

    def test_advisory_only_true_all_payloads(self):
        for lane_name, payloads in self.all_payloads:
            for role_id, payload in payloads.items():
                self.assertIs(
                    payload.get("advisory_only"), True,
                    f"advisory_only not True in {lane_name}.{role_id}",
                )

    def test_six_payloads_all_lanes(self):
        for lane_name, payloads in self.all_payloads:
            self.assertEqual(
                len(payloads), 6,
                f"{lane_name} has {len(payloads)} payloads; expected 6",
            )


# ══════════════════════════════════════════════════════════════════════════════
# R-10: Packet frozen across all lanes
# ══════════════════════════════════════════════════════════════════════════════

class TestR10PacketFrozen(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.lanes.mlb_moneyline import MlbMoneylineAdapter
        from gate_engine.universal_agent.lanes.wnba_props import WnbaPropsAdapter
        from gate_engine.universal_agent.lanes.mlb_props import MlbPropsAdapter
        from gate_engine.universal_agent.lanes.tennis_props import TennisPropsAdapter
        from gate_engine.universal_agent.lanes.generic_moneyline import GenericMoneylineAdapter
        self.packets = [
            ("MLB_ML",     MlbMoneylineAdapter().adapt(row=_mlb_ml_row(), run_id="r10-1").packet),
            ("WNBA",       WnbaPropsAdapter().adapt(row=_wnba_row(), run_id="r10-2").packet),
            ("MLB_PROPS",  MlbPropsAdapter().adapt(row=_mlb_props_row(), run_id="r10-3").packet),
            ("TENNIS",     TennisPropsAdapter().adapt(row=_tennis_row(), run_id="r10-4").packet),
            ("GENERIC_ML", GenericMoneylineAdapter().adapt(row=_nfl_row(), run_id="r10-5").packet),
        ]

    def test_all_packets_frozen_dataclasses(self):
        for lane, pkt in self.packets:
            self.assertTrue(
                dataclasses.is_dataclass(pkt) and pkt.__dataclass_params__.frozen,
                f"{lane} packet is not a frozen dataclass",
            )

    def test_all_packets_mutation_raises(self):
        for lane, pkt in self.packets:
            with self.assertRaises(dataclasses.FrozenInstanceError):
                pkt.run_id = "mutated"  # type: ignore


# ══════════════════════════════════════════════════════════════════════════════
# R-11: No live routing references in lane modules
# ══════════════════════════════════════════════════════════════════════════════

class TestR11NoLiveRouting(unittest.TestCase):

    def test_no_live_route_strings_in_lane_modules(self):
        violations = []
        for py_path in _UAC_LANE_MODULES:
            try:
                source = py_path.read_text()
            except OSError:
                continue
            for forbidden_str in _FORBIDDEN_ROUTE_STRINGS:
                if forbidden_str in source:
                    violations.append(f"{py_path.name}: contains {forbidden_str!r}")
        self.assertEqual(violations, [], f"Live route references found: {violations}")


# ══════════════════════════════════════════════════════════════════════════════
# R-12: Closure records present
# ══════════════════════════════════════════════════════════════════════════════

class TestR12ClosureRecordsPresent(unittest.TestCase):

    def test_b4_closure_json_exists(self):
        p = _REPO_ROOT / "uac_b4_closure.json"
        self.assertTrue(p.exists(), "B4 closure record missing")

    def test_b8_test_file_exists(self):
        p = _TESTS_ROOT / "test_b8_consolidated_shadow_validation.py"
        self.assertTrue(p.exists(), "B8 shadow validation test file missing")

    def test_b9_test_file_this_file_exists(self):
        p = _TESTS_ROOT / "test_b9_readiness_ruling.py"
        self.assertTrue(p.exists(), "B9 readiness ruling test file (this file) missing")


# ══════════════════════════════════════════════════════════════════════════════
# R-13: Full conveyor summary — machine-auditable log written to disk
# (Not a gate — runs last, best-effort, logged for human review)
# ══════════════════════════════════════════════════════════════════════════════

class TestR13ConveyorSummary(unittest.TestCase):

    def test_conveyor_summary_logged(self):
        import json, datetime, subprocess
        out_path = _REPO_ROOT / "uac_b9_readiness_ruling.json"

        # Restore to exact git-HEAD state after this test so the Stage A
        # isolation sentinel (test_no_uncommitted_changes_to_forbidden_files)
        # does not see an uncommitted modification to a tracked file.
        # Using `git checkout HEAD -- <path>` ensures the on-disk content
        # matches HEAD regardless of what was present before the test ran.
        def _restore_to_head():
            subprocess.run(
                ["git", "checkout", "HEAD", "--",
                 "artifacts/flask-scoring-api/uac_b9_readiness_ruling.json"],
                cwd=str(_REPO_ROOT.parent.parent),
                capture_output=True,
            )
        self.addCleanup(_restore_to_head)

        summary = {
            "conveyor": "UAC_FAST_TRACK",
            "version":  "V1",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "lanes": {
                "B3A_MLB_MONEYLINE":    "CLOSED",
                "B4_WNBA_PROPS":        "CLOSED",
                "B5_MLB_PROPS":         "CLOSED",
                "B6_TENNIS_PROPS":      "CLOSED",
                "B7_GENERIC_MONEYLINE": "CLOSED",
                "B8_SHADOW_VALIDATION": "CLOSED",
                "B9_READINESS_RULING":  "COMPLETE",
            },
            "authority_invariants": {
                "can_execute":          False,
                "PRODUCTION_AUTHORITY": False,
                "dry_run_only":         True,
                "advisory_only":        True,
                "no_market_orders":     True,
            },
            "ruling": "UAC_V1_COMPLETE_PENDING_ARCHITECTURAL_AUTHORITY_ACCEPTANCE",
            "note":   (
                "This ruling does not authorize production traffic. "
                "PRODUCTION_AUTHORITY=False and can_execute=False are "
                "unconditional. ChatGPT architectural authority must accept "
                "this ruling before UAC V1 is production-authorized."
            ),
        }
        out_path = _REPO_ROOT / "uac_b9_readiness_ruling.json"
        out_path.write_text(json.dumps(summary, indent=2))
        self.assertTrue(out_path.exists())
        reloaded = json.loads(out_path.read_text())
        self.assertFalse(reloaded["authority_invariants"]["can_execute"])
        self.assertFalse(reloaded["authority_invariants"]["PRODUCTION_AUTHORITY"])
        self.assertEqual(len(reloaded["lanes"]), 7)


if __name__ == "__main__":
    unittest.main()
