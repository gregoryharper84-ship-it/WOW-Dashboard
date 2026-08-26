"""
gate_engine/tests/test_generic_moneyline_adapter.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B7

B7 Generic Moneyline Lane Adapter acceptance tests.

Coverage:
  (a) Validation — dedicated-lane rejection, market guards, sport acceptance
  (b) GenericMoneylineAdapter — full adapt() contract (COMPLETE, DEGRADED,
      TECHNICAL_FAILURE)
  (c) Generic moneyline invariants — no probability fabrication, no generic
      fallback, LLP specialist reference, probability_status classification
  (d) Scope invariants — can_execute=False, no forbidden governance keys,
      no app.py import

No network, DB, or LLM calls anywhere.
"""
from __future__ import annotations

import dataclasses
import unittest

from gate_engine.universal_agent.evidence_packet import Lane
from gate_engine.universal_agent.lanes.generic_moneyline import (
    AdapterInputError,
    AdapterStatus,
    GenericMoneylineAdapter,
    GenericMoneylineAdapterResult,
)
from gate_engine.universal_agent.lanes.generic_moneyline.role_inputs import (
    LLP_PROBABILITY_SPECIALIST_REF,
)
from gate_engine.universal_agent.lanes.generic_moneyline.validation import (
    DEDICATED_LANE_SPORTS,
    MONEYLINE_MARKET_KEYS,
    validate_generic_moneyline_row,
)
from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS


# ── Row factories ─────────────────────────────────────────────────────────────

def _nfl_row(**kw) -> dict:
    base = {
        "event_id":     "nfl-evt-001",
        "sport":        "nfl",
        "market":       "moneyline",
        "team":         "KC",
        "opponent":     "BUF",
        "line":         -150.0,
        "direction":    "HOME",
        "event_date":   "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.60,
        "calibrated_probability": 0.58,
        "l10_ledger":   [1, 0, 1, 1, 0, 1, 1, 0, 1, 0],
        "role_status":  {"active_status": "ACTIVE"},
    }
    base.update(kw)
    return base

def _nhl_row(**kw) -> dict:
    base = _nfl_row(
        event_id="nhl-evt-001",
        sport="nhl",
        team="BOS",
        opponent="NYR",
        line=-120.0,
    )
    base.update(kw)
    return base

def _soccer_row(**kw) -> dict:
    base = _nfl_row(
        event_id="soccer-evt-001",
        sport="soccer",
        market="1x2",
        team="MAN CITY",
        opponent="ARSENAL",
        line=-130.0,
    )
    base.update(kw)
    return base

def _no_prob_row(**kw) -> dict:
    """Row with no probability evidence at all."""
    base = _nfl_row()
    del base["hit_probability"]
    del base["calibrated_probability"]
    base.update(kw)
    return base

def _raw_only_row(**kw) -> dict:
    """Row with hit_probability but no calibrated_probability."""
    base = _nfl_row()
    del base["calibrated_probability"]
    base.update(kw)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# (a) Validation
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation(unittest.TestCase):

    def test_valid_nfl_passes(self):
        validate_generic_moneyline_row(_nfl_row())

    def test_valid_nhl_passes(self):
        validate_generic_moneyline_row(_nhl_row())

    def test_valid_soccer_passes(self):
        validate_generic_moneyline_row(_soccer_row())

    def test_not_a_dict_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row("not a dict")
        self.assertEqual(ctx.exception.code, "NOT_A_DICT")

    def test_missing_sport_raises(self):
        row = _nfl_row()
        del row["sport"]
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row(row)
        self.assertEqual(ctx.exception.code, "MISSING_SPORT")

    def test_mlb_rejected_has_dedicated_lane(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row(_nfl_row(sport="mlb"))
        self.assertEqual(ctx.exception.code, "SPORT_HAS_DEDICATED_LANE")

    def test_wnba_rejected_has_dedicated_lane(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row(_nfl_row(sport="wnba"))
        self.assertEqual(ctx.exception.code, "SPORT_HAS_DEDICATED_LANE")

    def test_nba_rejected_has_dedicated_lane(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row(_nfl_row(sport="nba"))
        self.assertEqual(ctx.exception.code, "SPORT_HAS_DEDICATED_LANE")

    def test_tennis_rejected_has_dedicated_lane(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row(_nfl_row(sport="tennis"))
        self.assertEqual(ctx.exception.code, "SPORT_HAS_DEDICATED_LANE")

    def test_baseball_rejected_has_dedicated_lane(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row(_nfl_row(sport="baseball"))
        self.assertEqual(ctx.exception.code, "SPORT_HAS_DEDICATED_LANE")

    def test_all_dedicated_lane_sports_rejected(self):
        for sport in sorted(DEDICATED_LANE_SPORTS):
            with self.assertRaises(AdapterInputError) as ctx:
                validate_generic_moneyline_row(_nfl_row(sport=sport))
            self.assertEqual(
                ctx.exception.code, "SPORT_HAS_DEDICATED_LANE",
                f"sport={sport!r} should be rejected as having a dedicated lane",
            )

    def test_props_market_rejected(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row(_nfl_row(market="player_props"))
        self.assertEqual(ctx.exception.code, "MARKET_NOT_MONEYLINE")

    def test_winner_market_passes(self):
        validate_generic_moneyline_row(_nfl_row(market="winner"))

    def test_h2h_market_passes(self):
        validate_generic_moneyline_row(_nfl_row(market="h2h"))

    def test_missing_market_passes(self):
        row = _nfl_row()
        del row["market"]
        validate_generic_moneyline_row(row)

    def test_missing_event_id_raises(self):
        row = _nfl_row()
        del row["event_id"]
        with self.assertRaises(AdapterInputError) as ctx:
            validate_generic_moneyline_row(row)
        self.assertEqual(ctx.exception.code, "MISSING_EVENT_ID")

    def test_moneyline_market_keys_not_empty(self):
        self.assertIn("moneyline", MONEYLINE_MARKET_KEYS)
        self.assertIn("h2h", MONEYLINE_MARKET_KEYS)
        self.assertIn("winner", MONEYLINE_MARKET_KEYS)


# ══════════════════════════════════════════════════════════════════════════════
# (b) GenericMoneylineAdapter — full adapt() contract
# ══════════════════════════════════════════════════════════════════════════════

class TestGenericMoneylineAdapterFull(unittest.TestCase):

    def setUp(self):
        self.adapter = GenericMoneylineAdapter()

    def test_complete_status_when_all_fields_present(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="run-001")
        self.assertEqual(result.adapter_status, AdapterStatus.COMPLETE)

    def test_degraded_when_hit_prob_missing(self):
        row = _nfl_row()
        del row["hit_probability"]
        del row["calibrated_probability"]
        result = self.adapter.adapt(row=row, run_id="run-002")
        self.assertEqual(result.adapter_status, AdapterStatus.DEGRADED)

    def test_result_frozen(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="run-003")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.adapter_status = "MUTATED"  # type: ignore

    def test_packet_lane_is_generic_moneyline(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="run-004")
        self.assertEqual(result.packet.lane, Lane.GENERIC_MONEYLINE)

    def test_six_role_payloads_present(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="run-005")
        self.assertEqual(len(result.role_payloads), 6)

    def test_all_role_payloads_advisory_only_true(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="run-006")
        for role_id, payload in result.role_payloads.items():
            self.assertIs(payload.get("advisory_only"), True, role_id)

    def test_nhl_row_succeeds(self):
        result = self.adapter.adapt(row=_nhl_row(), run_id="run-007")
        self.assertIn(result.adapter_status, (AdapterStatus.COMPLETE, AdapterStatus.DEGRADED))

    def test_soccer_row_succeeds(self):
        result = self.adapter.adapt(row=_soccer_row(), run_id="run-008")
        self.assertIn(result.adapter_status, (AdapterStatus.COMPLETE, AdapterStatus.DEGRADED))

    def test_mlb_sport_raises(self):
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=_nfl_row(sport="mlb"), run_id="run-009")

    def test_acquisition_error_gives_technical_failure(self):
        result = self.adapter.adapt(
            row=_nfl_row(), run_id="run-010",
            acquisition_error="HTTP 503",
        )
        self.assertEqual(result.adapter_status, AdapterStatus.TECHNICAL_FAILURE)
        self.assertIsNotNone(result.failure_classification)
        self.assertEqual(
            result.failure_classification.failure_code,
            "ACQUISITION_PROVIDER_ERROR",
        )
        self.assertEqual(len(result.role_payloads), 0)

    def test_failure_classification_none_on_complete(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="run-011")
        self.assertIsNone(result.failure_classification)
        self.assertIsNone(result.ceiling_result)

    def test_snapshot_id_override(self):
        result = self.adapter.adapt(
            row=_nfl_row(), run_id="run-012", snapshot_id="fixed-snap"
        )
        self.assertEqual(result.packet.snapshot_id, "fixed-snap")

    def test_row_wins_on_enrichment_collision(self):
        row = _nfl_row(line=-150.0)
        result = self.adapter.adapt(
            row=row, run_id="run-013", enrichment={"line": -200.0}
        )
        ms = result.packet.market_snapshot
        self.assertEqual(ms.get("line"), -150.0)

    def test_missing_run_id_raises(self):
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=_nfl_row(), run_id="")

    def test_result_type(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="run-014")
        self.assertIsInstance(result, GenericMoneylineAdapterResult)

    def test_source_row_fields_used_is_tuple(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="run-015")
        self.assertIsInstance(result.source_row_fields_used, tuple)

    def test_adapter_stateless(self):
        adapter = GenericMoneylineAdapter()
        r1 = adapter.adapt(row=_nfl_row(event_id="A"), run_id="s1")
        r2 = adapter.adapt(row=_nhl_row(event_id="B"), run_id="s2")
        self.assertNotEqual(r1.packet.canonical_event_id, r2.packet.canonical_event_id)


# ══════════════════════════════════════════════════════════════════════════════
# (c) Generic moneyline invariants
# ══════════════════════════════════════════════════════════════════════════════

class TestGenericMoneylineInvariants(unittest.TestCase):

    def setUp(self):
        self.adapter = GenericMoneylineAdapter()

    # ── No probability fabrication ────────────────────────────────────────────

    def test_probability_fabrication_flag_always_false(self):
        for row in (_nfl_row(), _no_prob_row(), _raw_only_row()):
            result = self.adapter.adapt(row=row, run_id="inv-fab")
            ss = result.role_payloads.get("SPORT_SPECIALIST")
            routing = ss["advisory_findings"]["model_routing"]
            self.assertFalse(
                routing["probability_fabrication_flag"],
                "probability_fabrication_flag must always be False",
            )

    def test_generic_fallback_blocked_always_true(self):
        result = self.adapter.adapt(row=_no_prob_row(), run_id="inv-fb")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        routing = ss["advisory_findings"]["model_routing"]
        self.assertTrue(routing["generic_fallback_blocked"])

    # ── Probability status classification ─────────────────────────────────────

    def test_probability_status_available_when_calibrated(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="inv-ps-1")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertEqual(
            ss["advisory_findings"]["model_routing"]["probability_status"],
            "AVAILABLE",
        )

    def test_probability_status_raw_only_when_no_calibrated(self):
        result = self.adapter.adapt(row=_raw_only_row(), run_id="inv-ps-2")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertEqual(
            ss["advisory_findings"]["model_routing"]["probability_status"],
            "RAW_ONLY",
        )

    def test_probability_status_unavailable_when_all_absent(self):
        result = self.adapter.adapt(row=_no_prob_row(), run_id="inv-ps-3")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertEqual(
            ss["advisory_findings"]["model_routing"]["probability_status"],
            "PROBABILITY_UNAVAILABLE",
        )

    def test_probability_status_implied_only(self):
        row = _no_prob_row(implied_probability=0.52)
        result = self.adapter.adapt(row=row, run_id="inv-ps-4")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertEqual(
            ss["advisory_findings"]["model_routing"]["probability_status"],
            "IMPLIED_ONLY",
        )

    # ── LLP probability specialist reference ──────────────────────────────────

    def test_llp_specialist_ref_in_ss_payload(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="inv-ref-1")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertEqual(
            ss["advisory_findings"]["llp_probability_specialist_ref"],
            LLP_PROBABILITY_SPECIALIST_REF,
        )

    def test_llp_specialist_ref_constant_value(self):
        self.assertEqual(
            LLP_PROBABILITY_SPECIALIST_REF,
            "wow.llp-moneyline-probability-expert",
        )

    def test_llp_specialist_ref_present_even_when_prob_unavailable(self):
        result = self.adapter.adapt(row=_no_prob_row(), run_id="inv-ref-2")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertIn("llp_probability_specialist_ref", ss["advisory_findings"])

    # ── Sport echoed correctly ─────────────────────────────────────────────────

    def test_sport_uppercase_in_ss_payload(self):
        result = self.adapter.adapt(row=_nfl_row(), run_id="inv-sp-1")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertEqual(ss["advisory_findings"]["sport"], "NFL")

    def test_sport_echoed_in_market_snapshot(self):
        result = self.adapter.adapt(row=_soccer_row(), run_id="inv-sp-2")
        ms = result.packet.market_snapshot
        self.assertEqual(ms.get("sport"), "soccer")

    # ── Dedicated lane exclusion reflected in evidence ─────────────────────────

    def test_nfl_not_in_dedicated_lane_sports(self):
        self.assertNotIn("nfl", DEDICATED_LANE_SPORTS)

    def test_nhl_not_in_dedicated_lane_sports(self):
        self.assertNotIn("nhl", DEDICATED_LANE_SPORTS)

    def test_mlb_in_dedicated_lane_sports(self):
        self.assertIn("mlb", DEDICATED_LANE_SPORTS)

    def test_tennis_in_dedicated_lane_sports(self):
        self.assertIn("tennis", DEDICATED_LANE_SPORTS)


# ══════════════════════════════════════════════════════════════════════════════
# (d) Scope invariants
# ══════════════════════════════════════════════════════════════════════════════

class TestGenericMoneylineScopeInvariants(unittest.TestCase):

    def test_can_execute_false_adapter(self):
        import gate_engine.universal_agent.lanes.generic_moneyline.adapter as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_field_map(self):
        import gate_engine.universal_agent.lanes.generic_moneyline.field_map as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_role_inputs(self):
        import gate_engine.universal_agent.lanes.generic_moneyline.role_inputs as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_validation(self):
        import gate_engine.universal_agent.lanes.generic_moneyline.validation as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_package_init(self):
        import gate_engine.universal_agent.lanes.generic_moneyline as pkg
        self.assertFalse(pkg.can_execute)

    def test_no_governance_keys_in_role_payloads(self):
        adapter = GenericMoneylineAdapter()
        result  = adapter.adapt(row=_nfl_row(), run_id="scope-001")
        for role_id, payload in result.role_payloads.items():
            self._assert_no_forbidden_keys(payload, role_id)

    def _assert_no_forbidden_keys(self, obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                self.assertNotIn(
                    k.lower(), FORBIDDEN_GOVERNANCE_KEYS,
                    f"forbidden key {k!r} at {path}.{k}",
                )
                self._assert_no_forbidden_keys(v, f"{path}.{k}")
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                self._assert_no_forbidden_keys(item, f"{path}[{i}]")

    def test_packet_frozen(self):
        adapter = GenericMoneylineAdapter()
        result  = adapter.adapt(row=_nfl_row(), run_id="scope-002")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.packet.run_id = "mutated"  # type: ignore

    def test_lane_value_is_generic_moneyline(self):
        adapter = GenericMoneylineAdapter()
        result  = adapter.adapt(row=_nfl_row(), run_id="scope-003")
        self.assertEqual(result.packet.lane, "GENERIC_MONEYLINE")

    def test_no_app_import_in_adapter(self):
        import ast, pathlib
        p = (
            pathlib.Path(__file__).parent.parent
            / "universal_agent/lanes/generic_moneyline/adapter.py"
        )
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module] if node.module else [])
                )
                for name in names:
                    self.assertFalse(
                        name is not None and "app" == name.split(".")[-1],
                        f"adapter.py imports 'app': {name}",
                    )

    def test_result_type_is_generic_moneyline_adapter_result(self):
        adapter = GenericMoneylineAdapter()
        result  = adapter.adapt(row=_nfl_row(), run_id="scope-004")
        self.assertIsInstance(result, GenericMoneylineAdapterResult)


if __name__ == "__main__":
    unittest.main()
