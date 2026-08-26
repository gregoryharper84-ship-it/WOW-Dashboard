"""
gate_engine/tests/test_tennis_props_adapter.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B6

B6 Tennis Props Lane Adapter acceptance tests.

Coverage:
  (a) Field map helpers — surface extraction, stat_key detection, simplex
  (b) Validation — sport/market/stat_key/event_id guards
  (c) TennisPropsAdapter — full adapt() contract (COMPLETE, DEGRADED,
      TECHNICAL_FAILURE)
  (d) Tennis-specific invariants (Markov chain routing, first-set market
      gate, simplex full-precision storage, surface missing)
  (e) Scope invariants — can_execute=False, no forbidden governance keys

No network, DB, or LLM calls anywhere.
"""
from __future__ import annotations

import dataclasses
import unittest

from gate_engine.universal_agent.evidence_packet import Lane
from gate_engine.universal_agent.lanes.tennis_props import (
    AdapterInputError,
    AdapterStatus,
    TennisPropsAdapter,
    TennisPropsAdapterResult,
)
from gate_engine.universal_agent.lanes.tennis_props.field_map import (
    extract_simplex_probabilities,
    extract_surface,
    is_first_set_market,
    is_markov_chain_required,
    MARKOV_CHAIN_STAT_KEYS,
)
from gate_engine.universal_agent.lanes.tennis_props.validation import (
    SUPPORTED_STAT_KEYS,
    validate_tennis_props_row,
)
from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS


# ── Row factories ─────────────────────────────────────────────────────────────

def _total_games_row(**kw) -> dict:
    base = {
        "event_id":     "ten-evt-001",
        "sport":        "tennis",
        "market":       "game_totals",
        "stat_key":     "total_games",
        "player_1":     "Novak Djokovic",
        "player_2":     "Carlos Alcaraz",
        "tournament":   "Wimbledon",
        "round":        "Final",
        "surface":      "grass",
        "best_of":      5,
        "line":         38.5,
        "direction":    "OVER",
        "event_date":   "2026-08-16",
        "event_status": "scheduled",
        "hit_probability": 0.54,
        "l10_ledger":   [38, 42, 35, 40, 37, 44, 39, 41, 36, 43],
        "role_status":  {"active_status": "ACTIVE"},
    }
    base.update(kw)
    return base

def _first_set_row(**kw) -> dict:
    base = _total_games_row(
        stat_key="first_set_winner",
        line=None,
        direction="PLAYER_1",
    )
    base.update(kw)
    return base

def _first_set_games_row(**kw) -> dict:
    base = _total_games_row(
        stat_key="first_set_games",
        line=10.5,
    )
    base.update(kw)
    return base

def _match_winner_row(**kw) -> dict:
    base = _total_games_row(
        stat_key="match_winner",
        line=None,
        direction="PLAYER_1",
        market="moneyline",
    )
    base.update(kw)
    return base

def _simplex_row(**kw) -> dict:
    base = _total_games_row(
        simplex_under=0.3333333333333333,
        simplex_exact=0.3333333333333334,
        simplex_over=0.3333333333333333,
    )
    base.update(kw)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# (a) Field map helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestFieldMapHelpers(unittest.TestCase):

    def test_extract_surface_grass(self):
        self.assertEqual(extract_surface({"surface": "Grass"}), "grass")

    def test_extract_surface_clay(self):
        self.assertEqual(extract_surface({"surface": "CLAY"}), "clay")

    def test_extract_surface_missing_returns_unknown(self):
        self.assertEqual(extract_surface({}), "UNKNOWN")

    def test_extract_surface_from_matchup(self):
        self.assertEqual(
            extract_surface({"matchup": {"surface": "hard"}}), "hard"
        )

    def test_is_markov_chain_required_total_games(self):
        self.assertTrue(is_markov_chain_required({"stat_key": "total_games"}))

    def test_is_markov_chain_required_set_games(self):
        self.assertTrue(is_markov_chain_required({"stat_key": "set_games"}))

    def test_is_markov_chain_required_first_set_games(self):
        self.assertTrue(is_markov_chain_required({"stat_key": "first_set_games"}))

    def test_is_markov_chain_not_required_match_winner(self):
        self.assertFalse(is_markov_chain_required({"stat_key": "match_winner"}))

    def test_is_first_set_market_first_set_winner(self):
        self.assertTrue(is_first_set_market({"stat_key": "first_set_winner"}))

    def test_is_first_set_market_first_set_games(self):
        self.assertTrue(is_first_set_market({"stat_key": "first_set_games"}))

    def test_is_not_first_set_market_total_games(self):
        self.assertFalse(is_first_set_market({"stat_key": "total_games"}))

    def test_simplex_valid_when_sums_to_one(self):
        c = {"simplex_under": 0.35, "simplex_exact": 0.30, "simplex_over": 0.35}
        result = extract_simplex_probabilities(c)
        self.assertIsNotNone(result)
        self.assertTrue(result["simplex_valid"])

    def test_simplex_raw_floats_not_rounded(self):
        """Full-precision floats must be preserved — not rounded to 6dp."""
        c = {
            "simplex_under": 0.3333333333333333,
            "simplex_exact": 0.3333333333333334,
            "simplex_over":  0.3333333333333333,
        }
        result = extract_simplex_probabilities(c)
        self.assertEqual(result["under"], 0.3333333333333333)
        self.assertEqual(result["exact"], 0.3333333333333334)

    def test_simplex_invalid_when_does_not_sum(self):
        c = {"simplex_under": 0.4, "simplex_exact": 0.4, "simplex_over": 0.4}
        result = extract_simplex_probabilities(c)
        self.assertFalse(result["simplex_valid"])

    def test_simplex_none_when_all_absent(self):
        result = extract_simplex_probabilities({"stat_key": "total_games"})
        self.assertIsNone(result)

    def test_markov_chain_stat_keys_not_empty(self):
        self.assertGreater(len(MARKOV_CHAIN_STAT_KEYS), 0)
        self.assertIn("total_games", MARKOV_CHAIN_STAT_KEYS)


# ══════════════════════════════════════════════════════════════════════════════
# (b) Validation
# ══════════════════════════════════════════════════════════════════════════════

class TestValidation(unittest.TestCase):

    def test_valid_total_games_passes(self):
        validate_tennis_props_row(_total_games_row())

    def test_valid_first_set_passes(self):
        validate_tennis_props_row(_first_set_row())

    def test_valid_match_winner_passes(self):
        validate_tennis_props_row(_match_winner_row())

    def test_not_a_dict_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_tennis_props_row("not a dict")
        self.assertEqual(ctx.exception.code, "NOT_A_DICT")

    def test_missing_sport_raises(self):
        row = _total_games_row()
        del row["sport"]
        with self.assertRaises(AdapterInputError) as ctx:
            validate_tennis_props_row(row)
        self.assertEqual(ctx.exception.code, "MISSING_SPORT")

    def test_mlb_sport_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_tennis_props_row(_total_games_row(sport="mlb"))
        self.assertEqual(ctx.exception.code, "SPORT_MISMATCH")

    def test_atp_sport_passes(self):
        validate_tennis_props_row(_total_games_row(sport="atp"))

    def test_wta_sport_passes(self):
        validate_tennis_props_row(_total_games_row(sport="wta"))

    def test_wrong_market_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_tennis_props_row(_total_games_row(market="futures"))
        self.assertEqual(ctx.exception.code, "MARKET_MISMATCH")

    def test_missing_market_passes(self):
        row = _total_games_row()
        del row["market"]
        validate_tennis_props_row(row)

    def test_unsupported_stat_key_raises(self):
        with self.assertRaises(AdapterInputError) as ctx:
            validate_tennis_props_row(_total_games_row(stat_key="aces"))
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_STAT_KEY")

    def test_missing_event_id_raises(self):
        row = _total_games_row()
        del row["event_id"]
        with self.assertRaises(AdapterInputError) as ctx:
            validate_tennis_props_row(row)
        self.assertEqual(ctx.exception.code, "MISSING_EVENT_ID")

    def test_all_supported_stat_keys_pass(self):
        for sk in sorted(SUPPORTED_STAT_KEYS):
            row = _total_games_row(stat_key=sk)
            validate_tennis_props_row(row)


# ══════════════════════════════════════════════════════════════════════════════
# (c) TennisPropsAdapter — full adapt() contract
# ══════════════════════════════════════════════════════════════════════════════

class TestTennisPropsAdapterFull(unittest.TestCase):

    def setUp(self):
        self.adapter = TennisPropsAdapter()

    def test_complete_status_when_all_fields_present(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-001")
        self.assertEqual(result.adapter_status, AdapterStatus.COMPLETE)

    def test_degraded_when_surface_missing(self):
        row = _total_games_row()
        del row["surface"]
        result = self.adapter.adapt(row=row, run_id="run-002")
        self.assertEqual(result.adapter_status, AdapterStatus.DEGRADED)
        self.assertTrue(any("surface" in r.lower() for r in result.degradation_reasons))

    def test_degraded_when_hit_probability_missing(self):
        row = _total_games_row()
        del row["hit_probability"]
        result = self.adapter.adapt(row=row, run_id="run-003")
        self.assertEqual(result.adapter_status, AdapterStatus.DEGRADED)

    def test_result_is_frozen(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-004")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.adapter_status = "MUTATED"  # type: ignore

    def test_packet_lane_is_tennis_props(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-005")
        self.assertEqual(result.packet.lane, Lane.TENNIS_PROPS)

    def test_packet_player_name(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-006")
        self.assertIn("Djokovic", result.packet.player_name)

    def test_six_role_payloads_present(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-007")
        self.assertEqual(len(result.role_payloads), 6)

    def test_all_role_payloads_advisory_only_true(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-008")
        for role_id, payload in result.role_payloads.items():
            self.assertIs(payload.get("advisory_only"), True, role_id)

    def test_snapshot_id_override(self):
        result = self.adapter.adapt(
            row=_total_games_row(), run_id="run-009", snapshot_id="snap-fixed"
        )
        self.assertEqual(result.packet.snapshot_id, "snap-fixed")

    def test_missing_run_id_raises(self):
        with self.assertRaises(AdapterInputError):
            self.adapter.adapt(row=_total_games_row(), run_id="")

    def test_enrichment_does_not_override_row(self):
        row = _total_games_row(surface="grass")
        result = self.adapter.adapt(
            row=row, run_id="run-010", enrichment={"surface": "clay"}
        )
        # row wins → surface should be grass
        ms = result.packet.market_snapshot
        self.assertEqual(ms.get("surface"), "grass")

    def test_acquisition_error_gives_technical_failure(self):
        result = self.adapter.adapt(
            row=_total_games_row(), run_id="run-011",
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
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-012")
        self.assertIsNone(result.failure_classification)
        self.assertIsNone(result.ceiling_result)

    def test_source_row_fields_used_is_tuple(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-013")
        self.assertIsInstance(result.source_row_fields_used, tuple)
        self.assertGreater(len(result.source_row_fields_used), 0)

    def test_result_type(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="run-014")
        self.assertIsInstance(result, TennisPropsAdapterResult)

    def test_first_set_row_succeeds(self):
        result = self.adapter.adapt(row=_first_set_row(), run_id="run-015")
        self.assertIn(result.adapter_status, (AdapterStatus.COMPLETE, AdapterStatus.DEGRADED))

    def test_match_winner_row_succeeds(self):
        result = self.adapter.adapt(row=_match_winner_row(), run_id="run-016")
        self.assertIn(result.adapter_status, (AdapterStatus.COMPLETE, AdapterStatus.DEGRADED))


# ══════════════════════════════════════════════════════════════════════════════
# (d) Tennis-specific invariants
# ══════════════════════════════════════════════════════════════════════════════

class TestTennisSpecificInvariants(unittest.TestCase):

    def setUp(self):
        self.adapter = TennisPropsAdapter()

    # ── 1. Markov chain routing ───────────────────────────────────────────────

    def test_total_games_requires_markov_chain(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="inv-001")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        routing = ss["advisory_findings"]["model_routing"]
        self.assertTrue(routing["requires_markov_chain"])
        self.assertTrue(routing["monte_carlo_blocked"])

    def test_first_set_games_requires_markov_chain(self):
        result = self.adapter.adapt(row=_first_set_games_row(), run_id="inv-002")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        routing = ss["advisory_findings"]["model_routing"]
        self.assertTrue(routing["requires_markov_chain"])

    def test_match_winner_does_not_require_markov_chain(self):
        result = self.adapter.adapt(row=_match_winner_row(), run_id="inv-003")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        routing = ss["advisory_findings"]["model_routing"]
        self.assertFalse(routing["requires_markov_chain"])
        self.assertFalse(routing["monte_carlo_blocked"])

    def test_market_snapshot_has_markov_chain_required(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="inv-004")
        ms = result.packet.market_snapshot
        self.assertTrue(ms.get("markov_chain_required"))

    def test_market_snapshot_has_markov_chain_false_for_winner(self):
        result = self.adapter.adapt(row=_match_winner_row(), run_id="inv-005")
        ms = result.packet.market_snapshot
        self.assertFalse(ms.get("markov_chain_required"))

    # ── 2. First-set market gate ──────────────────────────────────────────────

    def test_first_set_winner_flagged(self):
        result = self.adapter.adapt(row=_first_set_row(), run_id="inv-006")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        routing = ss["advisory_findings"]["model_routing"]
        self.assertTrue(routing["is_first_set_market"])

    def test_total_games_not_first_set_market(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="inv-007")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        routing = ss["advisory_findings"]["model_routing"]
        self.assertFalse(routing["is_first_set_market"])

    def test_market_snapshot_first_set_flag(self):
        result = self.adapter.adapt(row=_first_set_row(), run_id="inv-008")
        ms = result.packet.market_snapshot
        self.assertTrue(ms.get("is_first_set_market"))

    # ── 3. Simplex full-precision storage ─────────────────────────────────────

    def test_simplex_stored_in_ss_payload(self):
        result = self.adapter.adapt(row=_simplex_row(), run_id="inv-009")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertIn("simplex", ss["advisory_findings"])

    def test_simplex_full_precision_not_rounded(self):
        result = self.adapter.adapt(row=_simplex_row(), run_id="inv-010")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        simplex = ss["advisory_findings"]["simplex"]
        # Must preserve 16-digit precision (not round to 6dp)
        self.assertEqual(simplex["under"], 0.3333333333333333)
        self.assertEqual(simplex["exact"], 0.3333333333333334)

    def test_simplex_stored_in_market_snapshot(self):
        result = self.adapter.adapt(row=_simplex_row(), run_id="inv-011")
        ms = result.packet.market_snapshot
        self.assertIn("simplex", ms)

    def test_no_simplex_in_payload_when_absent(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="inv-012")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertNotIn("simplex", ss["advisory_findings"])

    # ── 4. Surface type ───────────────────────────────────────────────────────

    def test_surface_in_ss_payload(self):
        result = self.adapter.adapt(row=_total_games_row(), run_id="inv-013")
        ss = result.role_payloads.get("SPORT_SPECIALIST")
        self.assertEqual(ss["advisory_findings"]["surface"], "grass")

    def test_missing_surface_degrades_not_fails(self):
        row = _total_games_row()
        del row["surface"]
        result = self.adapter.adapt(row=row, run_id="inv-014")
        self.assertIn(result.adapter_status, (AdapterStatus.DEGRADED,))
        # Should still have 6 role payloads
        self.assertEqual(len(result.role_payloads), 6)

    def test_surface_missing_flag_in_dsi_payload(self):
        row = _total_games_row()
        del row["surface"]
        result = self.adapter.adapt(row=row, run_id="inv-015")
        dsi = result.role_payloads.get("DATA_SLATE_INTEGRITY")
        event_ctx = dsi["advisory_findings"]["event_context"]
        self.assertTrue(event_ctx.get("surface_missing"))


# ══════════════════════════════════════════════════════════════════════════════
# (e) Scope invariants
# ══════════════════════════════════════════════════════════════════════════════

class TestTennisPropsAdapterScopeInvariants(unittest.TestCase):

    def test_can_execute_false_adapter(self):
        import gate_engine.universal_agent.lanes.tennis_props.adapter as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_field_map(self):
        import gate_engine.universal_agent.lanes.tennis_props.field_map as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_role_inputs(self):
        import gate_engine.universal_agent.lanes.tennis_props.role_inputs as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_validation(self):
        import gate_engine.universal_agent.lanes.tennis_props.validation as mod
        self.assertFalse(mod.can_execute)

    def test_can_execute_false_package_init(self):
        import gate_engine.universal_agent.lanes.tennis_props as pkg
        self.assertFalse(pkg.can_execute)

    def test_no_governance_keys_in_role_payloads(self):
        adapter = TennisPropsAdapter()
        result  = adapter.adapt(row=_total_games_row(), run_id="scope-001")
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
        adapter = TennisPropsAdapter()
        result  = adapter.adapt(row=_total_games_row(), run_id="scope-002")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.packet.run_id = "mutated"  # type: ignore

    def test_adapter_stateless(self):
        adapter = TennisPropsAdapter()
        r1 = adapter.adapt(row=_total_games_row(event_id="evt-A"), run_id="s1")
        r2 = adapter.adapt(row=_first_set_row(event_id="evt-B"), run_id="s2")
        self.assertNotEqual(r1.packet.canonical_event_id, r2.packet.canonical_event_id)

    def test_lane_is_tennis_props(self):
        adapter = TennisPropsAdapter()
        result  = adapter.adapt(row=_total_games_row(), run_id="scope-003")
        self.assertEqual(result.packet.lane, "TENNIS_PROPS")

    def test_no_app_import_in_adapter(self):
        import ast, pathlib
        p = (
            pathlib.Path(__file__).parent.parent
            / "universal_agent/lanes/tennis_props/adapter.py"
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


if __name__ == "__main__":
    unittest.main()
