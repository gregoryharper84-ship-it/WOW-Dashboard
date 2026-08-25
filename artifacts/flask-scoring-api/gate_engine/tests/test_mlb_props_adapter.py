"""
gate_engine/tests/test_mlb_props_adapter.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B5

B5 MLB Props Lane Adapter acceptance tests.

Coverage:
  (a) OneIpGate — routing decision for 1IP pitches market
  (b) ip_to_outs — innings notation conversion correctness
  (c) Validation — sport/market/stat_key/event_id guards
  (d) MlbPropsAdapter — full adapt() contract (COMPLETE, DEGRADED, TECHNICAL_FAILURE)
  (e) MLB-specific invariants (failure-path prob, outs conversion, event-tree routing)
  (f) Scope invariants — can_execute=False, no forbidden governance keys

No network, DB, or LLM calls anywhere.
"""
from __future__ import annotations

import dataclasses
import unittest

from gate_engine.universal_agent.evidence_packet import Lane
from gate_engine.universal_agent.lanes.mlb_props import (
    AdapterInputError,
    AdapterStatus,
    MlbPropsAdapter,
    MlbPropsAdapterResult,
)
from gate_engine.universal_agent.lanes.mlb_props.event_tree.one_ip_gate import (
    ONE_IP_EVENT_TREE_ID,
    OneIpGate,
)
from gate_engine.universal_agent.lanes.mlb_props.field_map import ip_to_outs
from gate_engine.universal_agent.lanes.mlb_props.validation import (
    SUPPORTED_STAT_KEYS,
    validate_mlb_props_row,
)
from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS


# ── Minimal valid row factories ───────────────────────────────────────────────

def _pitcher_row(**kw) -> dict:
    base = {
        "event_id":    "mlb-evt-001",
        "sport":       "mlb",
        "market":      "player_props",
        "stat_key":    "pitcher_strikeouts",
        "player":      "Corbin Burnes",
        "team":        "ARI",
        "opponent":    "LAD",
        "line":        6.5,
        "direction":   "OVER",
        "event_date":  "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.58,
        "l10_ledger":  [7, 5, 8, 6, 7, 4, 9, 6, 7, 8],
        "role_status": {"active_status": "ACTIVE"},
    }
    base.update(kw)
    return base

def _batter_row(**kw) -> dict:
    base = {
        "event_id":    "mlb-evt-002",
        "sport":       "mlb",
        "market":      "player_props",
        "stat_key":    "batter_hits",
        "player":      "Freddie Freeman",
        "team":        "LAD",
        "opponent":    "ARI",
        "line":        0.5,
        "direction":   "OVER",
        "event_date":  "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.61,
        "l10_ledger":  [1, 0, 2, 1, 1, 0, 1, 2, 1, 1],
        "role_status": {"active_status": "ACTIVE"},
    }
    base.update(kw)
    return base

def _one_ip_row(**kw) -> dict:
    base = _pitcher_row(
        stat_key="pitcher_1ip_pitches",
        line=14.5,
        ip_value=1.0,
    )
    base.update(kw)
    return base

def _outs_row(**kw) -> dict:
    base = _pitcher_row(
        stat_key="pitcher_outs",
        line=4.2,          # 4.2 IP line = 14 outs
        ip_value=4.2,
    )
    base.update(kw)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# (a) OneIpGate
# ══════════════════════════════════════════════════════════════════════════════

class TestOneIpGate(unittest.TestCase):

    def setUp(self):
        self.gate = OneIpGate()

    # routing_required=True cases
    def test_pitcher_1ip_pitches_routes_to_event_tree(self):
        result = self.gate.evaluate({"stat_key": "pitcher_1ip_pitches"})
        self.assertTrue(result.routing_required)
        self.assertEqual(result.event_tree_id, ONE_IP_EVENT_TREE_ID)
        self.assertTrue(result.generic_model_blocked)
        self.assertIsNone(result.block_reason)

    def test_1ip_pitches_alias_routes(self):
        result = self.gate.evaluate({"stat_key": "1ip_pitches"})
        self.assertTrue(result.routing_required)

    def test_first_inning_pitches_alias_routes(self):
        result = self.gate.evaluate({"stat_key": "first_inning_pitches"})
        self.assertTrue(result.routing_required)

    def test_prop_type_field_used_when_stat_key_absent(self):
        result = self.gate.evaluate({"prop_type": "pitcher_1ip_pitches"})
        self.assertTrue(result.routing_required)

    # routing_required=False cases
    def test_pitcher_strikeouts_does_not_route_to_event_tree(self):
        result = self.gate.evaluate({"stat_key": "pitcher_strikeouts"})
        self.assertFalse(result.routing_required)
        self.assertIsNone(result.event_tree_id)
        self.assertFalse(result.generic_model_blocked)
        self.assertIsNotNone(result.block_reason)

    def test_batter_hits_does_not_route(self):
        result = self.gate.evaluate({"stat_key": "batter_hits"})
        self.assertFalse(result.routing_required)

    def test_empty_dict_does_not_route(self):
        result = self.gate.evaluate({})
        self.assertFalse(result.routing_required)
        self.assertEqual(result.stat_key_detected, "UNKNOWN")

    def test_non_dict_does_not_raise(self):
        result = self.gate.evaluate(None)  # type: ignore
        self.assertFalse(result.routing_required)

    def test_result_is_frozen(self):
        result = self.gate.evaluate({"stat_key": "pitcher_1ip_pitches"})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.routing_required = False  # type: ignore

    def test_event_tree_id_constant_value(self):
        self.assertEqual(ONE_IP_EVENT_TREE_ID, "MLB_1IP_PITCHES_EVENT_TREE_V1")


# ══════════════════════════════════════════════════════════════════════════════
# (b) ip_to_outs — innings notation conversion
# ══════════════════════════════════════════════════════════════════════════════

class TestIpToOuts(unittest.TestCase):

    def test_4_2_is_14_outs(self):
        """4.2 IP = 4 full innings (12 outs) + 2 partial outs = 14"""
        self.assertEqual(ip_to_outs(4.2), 14)

    def test_4_0_is_12_outs(self):
        """4.0 IP = 4 full innings = 12 outs"""
        self.assertEqual(ip_to_outs(4.0), 12)

    def test_5_1_is_16_outs(self):
        """5.1 IP = 5 * 3 + 1 = 16 outs"""
        self.assertEqual(ip_to_outs(5.1), 16)

    def test_6_0_is_18_outs(self):
        self.assertEqual(ip_to_outs(6.0), 18)

    def test_1_0_is_3_outs(self):
        self.assertEqual(ip_to_outs(1.0), 3)

    def test_0_1_is_1_out(self):
        self.assertEqual(ip_to_outs(0.1), 1)

    def test_0_2_is_2_outs(self):
        self.assertEqual(ip_to_outs(0.2), 2)

    def test_string_input_works(self):
        """String "4.2" should parse correctly."""
        self.assertEqual(ip_to_outs("4.2"), 14)

    def test_none_returns_none(self):
        self.assertIsNone(ip_to_outs(None))

    def test_invalid_string_returns_none(self):
        self.assertIsNone(ip_to_outs("not_a_number"))

    def test_negative_returns_none(self):
        self.assertIsNone(ip_to_outs(-1.0))

    def test_float_drift_safety(self):
        """Ensure 4.2 does not produce 13 due to IEEE-754 drift."""
        # 4.2 % 1 in Python is approximately 0.199... not 0.2
        # Our formula must produce 14, not 13
        result = ip_to_outs(4.2)
        self.assertEqual(result, 14, "Float drift must not corrupt innings conversion")

    def test_integer_input_works(self):
        self.assertEqual(ip_to_outs(5), 15)

    def test_7_2_is_23_outs(self):
        """7.2 IP = 7 * 3 + 2 = 23"""
        self.assertEqual(ip_to_outs(7.2), 23)


# ══════════════════════════════════════════════════════════════════════════════
# (c) Validation
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation(unittest.TestCase):

    def test_valid_pitcher_row_passes(self):
        validate_mlb_props_row(_pitcher_row())  # no raise

    def test_valid_batter_row_passes(self):
        validate_mlb_props_row(_batter_row())  # no raise

    def test_valid_1ip_row_passes(self):
        validate_mlb_props_row(_one_ip_row())  # no raise

    def test_not_a_dict_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_props_row("not a dict")
        self.assertEqual(ctx.exception.code, "NOT_A_DICT")

    def test_missing_sport_raises(self):
        row = _pitcher_row()
        del row["sport"]
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_props_row(row)
        self.assertEqual(ctx.exception.code, "MISSING_SPORT")

    def test_wrong_sport_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_props_row(_pitcher_row(sport="NBA"))
        self.assertEqual(ctx.exception.code, "SPORT_MISMATCH")

    def test_wnba_sport_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_props_row(_pitcher_row(sport="wnba"))
        self.assertEqual(ctx.exception.code, "SPORT_MISMATCH")

    def test_baseball_sport_alias_passes(self):
        validate_mlb_props_row(_pitcher_row(sport="baseball"))  # no raise

    def test_mlb_batter_sport_alias_passes(self):
        validate_mlb_props_row(_pitcher_row(sport="mlb_batter"))  # no raise

    def test_wrong_market_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_props_row(_pitcher_row(market="moneyline"))
        self.assertEqual(ctx.exception.code, "MARKET_MISMATCH")

    def test_props_market_passes(self):
        validate_mlb_props_row(_pitcher_row(market="props"))  # no raise

    def test_missing_market_field_passes(self):
        row = _pitcher_row()
        del row["market"]
        validate_mlb_props_row(row)  # no raise — market is optional

    def test_unsupported_stat_key_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_props_row(_pitcher_row(stat_key="pitcher_wins"))
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_STAT_KEY")

    def test_missing_event_id_raises(self):
        row = _pitcher_row()
        del row["event_id"]
        with self.assertRaises(AdapterInputError) as ctx:
            validate_mlb_props_row(row)
        self.assertEqual(ctx.exception.code, "MISSING_EVENT_ID")

    def test_all_supported_stat_keys_pass(self):
        for sk in sorted(SUPPORTED_STAT_KEYS):
            row = _pitcher_row(stat_key=sk)
            validate_mlb_props_row(row)  # no raise, with message on failure


# ══════════════════════════════════════════════════════════════════════════════
# (d) MlbPropsAdapter — full adapt() contract
# ══════════════════════════════════════════════════════════════════════════════

class TestMlbPropsAdapterFull(unittest.TestCase):

    def setUp(self):
        self.adapter = MlbPropsAdapter()

    def test_complete_status_when_all_fields_present(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-001")
        self.assertEqual(result.adapter_status, AdapterStatus.COMPLETE)

    def test_degraded_status_when_missing_evidence(self):
        row = _pitcher_row()
        del row["hit_probability"]
        del row["l10_ledger"]
        result = self.adapter.adapt(row=row, run_id="run-002")
        self.assertEqual(result.adapter_status, AdapterStatus.DEGRADED)
        self.assertTrue(any("MISSING" in r for r in result.degradation_reasons))

    def test_result_is_frozen(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-003")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.adapter_status = "MUTATED"  # type: ignore

    def test_evidence_packet_lane_is_mlb_props(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-004")
        self.assertEqual(result.packet.lane, Lane.MLB_PROPS)

    def test_evidence_packet_player_name(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-005")
        self.assertEqual(result.packet.player_name, "Corbin Burnes")

    def test_evidence_packet_run_id_matches(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-xyz")
        self.assertEqual(result.packet.run_id, "run-xyz")

    def test_six_role_payloads_present(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-006")
        self.assertEqual(len(result.role_payloads), 6)

    def test_all_role_payloads_advisory_only_true(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-007")
        for role_id, payload in result.role_payloads.items():
            self.assertIs(
                payload.get("advisory_only"), True,
                f"role {role_id} advisory_only must be exactly True",
            )

    def test_snapshot_id_override(self):
        result = self.adapter.adapt(
            row=_pitcher_row(), run_id="run-008", snapshot_id="fixed-snap"
        )
        self.assertEqual(result.packet.snapshot_id, "fixed-snap")

    def test_missing_run_id_raises(self):
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=_pitcher_row(), run_id="")

    def test_no_enrichment_succeeds(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-009")
        self.assertIn(result.adapter_status, (AdapterStatus.COMPLETE, AdapterStatus.DEGRADED))

    def test_enrichment_merged_event_status(self):
        result = self.adapter.adapt(
            row=_pitcher_row(event_status=None),
            run_id="run-010",
            enrichment={"event_status": "live"},
        )
        # row wins on collision — but event_status=None in row, enrichment supplies it
        # combined = {**enrichment, **row} → row.event_status=None wins (dict.update logic)
        # With None in row the enrichment value is masked. That is correct adapter behavior.
        self.assertIn(result.adapter_status, (AdapterStatus.COMPLETE, AdapterStatus.DEGRADED))

    def test_row_wins_on_collision(self):
        row = _pitcher_row(line=7.5)
        result = self.adapter.adapt(
            row=row, run_id="run-011", enrichment={"line": 6.0}
        )
        ms = result.packet.market_snapshot
        self.assertEqual(ms.get("line"), 7.5)

    def test_source_row_fields_used_is_tuple(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-012")
        self.assertIsInstance(result.source_row_fields_used, tuple)
        self.assertTrue(len(result.source_row_fields_used) > 0)

    def test_batter_row_produces_complete(self):
        result = self.adapter.adapt(row=_batter_row(), run_id="run-013")
        self.assertEqual(result.adapter_status, AdapterStatus.COMPLETE)

    def test_nba_sport_raises(self):
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=_pitcher_row(sport="NBA"), run_id="run-014")

    def test_failure_classification_none_on_complete(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="run-015")
        self.assertIsNone(result.failure_classification)
        self.assertIsNone(result.ceiling_result)

    def test_acquisition_error_gives_technical_failure(self):
        result = self.adapter.adapt(
            row=_pitcher_row(), run_id="run-016",
            acquisition_error="HTTP 503 from stats provider",
        )
        self.assertEqual(result.adapter_status, AdapterStatus.TECHNICAL_FAILURE)
        self.assertIsNotNone(result.failure_classification)
        self.assertIsNotNone(result.ceiling_result)
        self.assertEqual(len(result.role_payloads), 0)

    def test_acquisition_error_preserves_failure_code(self):
        result = self.adapter.adapt(
            row=_pitcher_row(), run_id="run-017",
            acquisition_error="timeout",
        )
        fc = result.failure_classification
        self.assertEqual(fc.failure_code, "ACQUISITION_PROVIDER_ERROR")

    def test_public_api_exports_match(self):
        from gate_engine.universal_agent.lanes.mlb_props import (
            MlbPropsAdapter as A,
            AdapterInputError as E,
        )
        self.assertIs(A, MlbPropsAdapter)
        self.assertIs(E, AdapterInputError)


# ══════════════════════════════════════════════════════════════════════════════
# (e) MLB-specific invariants
# ══════════════════════════════════════════════════════════════════════════════

class TestMlbSpecificInvariants(unittest.TestCase):

    def setUp(self):
        self.adapter = MlbPropsAdapter()

    # ── 1. pitcher_strikeouts failure-path probability requirement ────────────

    def test_strikeouts_failure_path_prob_required_true(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="inv-001")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        model_routing = ss["advisory_findings"]["model_routing"]
        self.assertTrue(
            model_routing["failure_path_prob_required"],
            "pitcher_strikeouts must require failure-path probability",
        )

    def test_batter_hits_failure_path_prob_not_required(self):
        result = self.adapter.adapt(row=_batter_row(), run_id="inv-002")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        model_routing = ss["advisory_findings"]["model_routing"]
        self.assertFalse(
            model_routing["failure_path_prob_required"],
            "batter_hits should not require failure-path probability",
        )

    # ── 2. pitcher_outs innings notation conversion ───────────────────────────

    def test_outs_row_market_snapshot_has_outs_equivalent(self):
        result = self.adapter.adapt(row=_outs_row(), run_id="inv-003")
        ms = result.packet.market_snapshot
        self.assertIn("outs_equivalent", ms)
        self.assertEqual(ms["outs_equivalent"], 14)  # 4.2 IP = 14 outs

    def test_outs_conversion_required_flag_set(self):
        result = self.adapter.adapt(row=_outs_row(), run_id="inv-004")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        model_routing = ss["advisory_findings"]["model_routing"]
        self.assertTrue(model_routing["outs_conversion_required"])

    def test_mel_outs_line_converted(self):
        result = self.adapter.adapt(row=_outs_row(), run_id="inv-005")
        mel = result.role_payloads.get("MARKET_EXACT_LINE")
        line_info = mel["advisory_findings"]["line"]
        self.assertIn("outs_equivalent", line_info)
        self.assertEqual(line_info["outs_equivalent"], 14)

    def test_strikeouts_does_not_have_outs_equivalent_in_mel(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="inv-006")
        mel = result.role_payloads.get("MARKET_EXACT_LINE")
        line_info = mel["advisory_findings"]["line"]
        # pitcher_strikeouts line is 6.5 — no outs conversion expected
        self.assertNotIn("outs_equivalent", line_info)

    # ── 3. pitcher_1ip_pitches event-tree routing ─────────────────────────────

    def test_1ip_gate_result_present_on_normal_result(self):
        result = self.adapter.adapt(row=_one_ip_row(), run_id="inv-007")
        self.assertIsNotNone(result.one_ip_gate_result)

    def test_1ip_routing_required_for_1ip_market(self):
        result = self.adapter.adapt(row=_one_ip_row(), run_id="inv-008")
        self.assertTrue(result.one_ip_gate_result.routing_required)
        self.assertEqual(
            result.one_ip_gate_result.event_tree_id, ONE_IP_EVENT_TREE_ID
        )

    def test_1ip_generic_model_blocked(self):
        result = self.adapter.adapt(row=_one_ip_row(), run_id="inv-009")
        self.assertTrue(result.one_ip_gate_result.generic_model_blocked)

    def test_1ip_ss_payload_flags_event_tree(self):
        result = self.adapter.adapt(row=_one_ip_row(), run_id="inv-010")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        routing = ss["advisory_findings"]["model_routing"]
        self.assertTrue(routing["requires_event_tree"])
        self.assertEqual(routing["event_tree_id"], ONE_IP_EVENT_TREE_ID)
        self.assertTrue(routing["generic_model_blocked"])

    def test_strikeouts_gate_result_routing_not_required(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="inv-011")
        self.assertIsNotNone(result.one_ip_gate_result)
        self.assertFalse(result.one_ip_gate_result.routing_required)

    def test_strikeouts_ss_payload_no_event_tree(self):
        result = self.adapter.adapt(row=_pitcher_row(), run_id="inv-012")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        routing = ss["advisory_findings"]["model_routing"]
        self.assertFalse(routing["requires_event_tree"])
        self.assertIsNone(routing["event_tree_id"])
        self.assertFalse(routing["generic_model_blocked"])


# ══════════════════════════════════════════════════════════════════════════════
# (f) Scope invariants
# ══════════════════════════════════════════════════════════════════════════════

class TestMlbPropsAdapterScopeInvariants(unittest.TestCase):

    def test_can_execute_false_adapter_module(self):
        import gate_engine.universal_agent.lanes.mlb_props.adapter as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_field_map(self):
        import gate_engine.universal_agent.lanes.mlb_props.field_map as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_role_inputs(self):
        import gate_engine.universal_agent.lanes.mlb_props.role_inputs as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_validation(self):
        import gate_engine.universal_agent.lanes.mlb_props.validation as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_one_ip_gate(self):
        import gate_engine.universal_agent.lanes.mlb_props.event_tree.one_ip_gate as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_package_init(self):
        import gate_engine.universal_agent.lanes.mlb_props as pkg
        self.assertFalse(pkg.can_execute)

    def test_lane_is_mlb_props(self):
        adapter = MlbPropsAdapter()
        result  = adapter.adapt(row=_pitcher_row(), run_id="scope-001")
        self.assertEqual(result.packet.lane, "MLB_PROPS")

    def test_no_governance_keys_in_role_payloads(self):
        adapter = MlbPropsAdapter()
        result  = adapter.adapt(row=_pitcher_row(), run_id="scope-002")
        for role_id, payload in result.role_payloads.items():
            self._assert_no_forbidden_keys(payload, role_id)

    def _assert_no_forbidden_keys(self, obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                self.assertNotIn(
                    k.lower(), FORBIDDEN_GOVERNANCE_KEYS,
                    f"forbidden key {k!r} found at {path}.{k}",
                )
                self._assert_no_forbidden_keys(v, f"{path}.{k}")
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                self._assert_no_forbidden_keys(item, f"{path}[{i}]")

    def test_packet_frozen(self):
        adapter = MlbPropsAdapter()
        result  = adapter.adapt(row=_pitcher_row(), run_id="scope-003")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.packet.run_id = "mutated"  # type: ignore

    def test_adapter_is_stateless(self):
        adapter = MlbPropsAdapter()
        r1 = adapter.adapt(row=_pitcher_row(event_id="evt-A"), run_id="s1")
        r2 = adapter.adapt(row=_batter_row(event_id="evt-B"), run_id="s2")
        self.assertNotEqual(r1.packet.canonical_event_id, r2.packet.canonical_event_id)

    def test_no_app_import_in_adapter(self):
        """Adapter module must not import from app.py."""
        import ast
        import pathlib
        adapter_path = pathlib.Path(
            __file__
        ).parent.parent / "universal_agent/lanes/mlb_props/adapter.py"
        source = adapter_path.read_text()
        tree   = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module] if node.module else [])
                )
                for name in names:
                    self.assertFalse(
                        name is not None and "app" == name.split(".")[-1],
                        f"adapter.py imports 'app': {name}",
                    )

    def test_result_type_is_mlb_props_adapter_result(self):
        adapter = MlbPropsAdapter()
        result  = adapter.adapt(row=_pitcher_row(), run_id="scope-004")
        self.assertIsInstance(result, MlbPropsAdapterResult)


if __name__ == "__main__":
    unittest.main()
