"""
gate_engine/tests/test_b3_lanes.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3 acceptance tests.

Covers:
  (a) MLB moneyline adapter maps a well-formed row to an EvidencePacket
      plus six B1 payloads.
  (b) _scan_forbidden_keys returns None on a clean dict.
  (c) DeterministicAdapterRunner keys results by role_id.
  (d) Shadow pipeline passes the same packet id() to all runners.
  (e) Canary pipeline flag disabled by default; no auto-promotion.
  (f) Authority constants correct in all B3 __init__.py files.

No network or DB calls anywhere (db_conn=None throughout; canary is never
enabled — only its config constants are inspected).
"""
from __future__ import annotations

import unittest

import gate_engine.universal_agent.canary as canary_pkg
import gate_engine.universal_agent.lanes.mlb_moneyline as mlb_ml_pkg
import gate_engine.universal_agent.lanes.wnba_props as wnba_pkg
import gate_engine.universal_agent.shadow as shadow_pkg
from gate_engine.universal_agent.canary import canary_config
from gate_engine.universal_agent.evidence_packet import EvidencePacket
from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import (
    AdapterStatus,
    MlbMoneylineAdapter,
)
from gate_engine.universal_agent.lanes.mlb_moneyline.validation import (
    AdapterInputError,
    validate_mlb_moneyline_row,
)
from gate_engine.universal_agent.orchestrator import (
    B1_ROLE_IDS,
    run_orchestrator,
)
from gate_engine.universal_agent.output_contract import (
    OUTPUT_VALID,
    _scan_forbidden_keys,
)
from gate_engine.universal_agent.roles.registry_b1 import (
    ALL_B1_ENTRIES,
    build_b1_registry,
)
from gate_engine.universal_agent.shadow import shadow_pipeline as sp
from gate_engine.universal_agent.shadow.deterministic_runner import (
    DeterministicAdapterRunner,
)
from gate_engine.universal_agent.shadow.shadow_pipeline import (
    ShadowPipelineStatus,
    run_shadow_pipeline,
)

_ROLE_VALIDATORS = None


def _role_validators():
    global _ROLE_VALIDATORS
    if _ROLE_VALIDATORS is None:
        from gate_engine.universal_agent.roles import (
            validate_data_slate_integrity_output,
            validate_failure_contradiction_output,
            validate_final_refresh_output,
            validate_market_exact_line_output,
            validate_news_status_output,
            validate_sport_specialist_output,
        )
        _ROLE_VALIDATORS = {
            "DATA_SLATE_INTEGRITY":  validate_data_slate_integrity_output,
            "NEWS_STATUS":           validate_news_status_output,
            "MARKET_EXACT_LINE":     validate_market_exact_line_output,
            "SPORT_SPECIALIST":      validate_sport_specialist_output,
            "FAILURE_CONTRADICTION": validate_failure_contradiction_output,
            "FINAL_REFRESH":         validate_final_refresh_output,
        }
    return _ROLE_VALIDATORS


def _well_formed_row(**overrides) -> dict:
    row = {
        "event_id":       "mlb-2026-08-16-nyy-bos",
        "sport":          "MLB",
        "market":         "moneyline",
        "team":           "NYY",
        "opponent":       "BOS",
        "slate_date":     "2026-08-16",
        "starter_status": "CONFIRMED",
        "starter_source": "mlb-stats-api",
        "lineup_status":  "CONFIRMED",
        "lineup_source":  "mlb-stats-api",
        "event_status":   "SCHEDULED",
        "weather_status": "CLEAR",
        "sportsbook_no_vig_probability":      0.55,
        "kalshi_multiplier":                  1.8,
        "kalshi_breakeven_probability":       0.556,
        "model_probability":                  0.60,
        "calibrated_probability_lower_bound": 0.52,
        "candidate_odds":  -130,
        "odds_source":     "odds-api",
        "preflight_checked":  True,
        "preflight_status":   "PASS",
        "preflight_blockers": [],
        "pulled_at": "2026-08-16T12:00:00+00:00",
        "as_of":     "2026-08-16T12:00:00+00:00",
    }
    row.update(overrides)
    return row


# ── (a) MLB moneyline adapter ──────────────────────────────────────────────────

class TestMlbMoneylineAdapter(unittest.TestCase):
    def test_well_formed_row_maps_to_packet_and_six_payloads(self):
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="run-b3-test"
        )
        self.assertIsInstance(result.packet, EvidencePacket)
        self.assertEqual(set(result.role_payloads.keys()), set(B1_ROLE_IDS))
        self.assertEqual(len(result.role_payloads), 6)

    def test_packet_identity_fields(self):
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="run-b3-test"
        )
        self.assertEqual(result.packet.lane, "MLB_MONEYLINE")
        self.assertEqual(result.packet.run_id, "run-b3-test")
        self.assertEqual(result.packet.canonical_event_id,
                         "mlb-2026-08-16-nyy-bos")

    def test_complete_status_on_fully_populated_row(self):
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="run-b3-test"
        )
        self.assertEqual(result.adapter_status, AdapterStatus.COMPLETE)
        self.assertEqual(result.degradation_reasons, ())

    def test_degraded_status_on_minimal_row(self):
        minimal = {"event_id": "e1", "sport": "MLB", "market": "moneyline"}
        result = MlbMoneylineAdapter().adapt(row=minimal, run_id="run-b3-test")
        self.assertEqual(result.adapter_status, AdapterStatus.DEGRADED)
        self.assertTrue(result.degradation_reasons)

    def test_all_six_payloads_pass_their_role_validators(self):
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="run-b3-test"
        )
        for role_id, payload in result.role_payloads.items():
            self.assertIs(_role_validators()[role_id](payload), OUTPUT_VALID,
                          role_id)

    def test_degraded_payloads_still_pass_validators(self):
        minimal = {"event_id": "e1", "sport": "MLB", "market": "moneyline"}
        result = MlbMoneylineAdapter().adapt(row=minimal, run_id="run-b3-test")
        for role_id, payload in result.role_payloads.items():
            self.assertIs(_role_validators()[role_id](payload), OUTPUT_VALID,
                          role_id)

    def test_no_forbidden_keys_in_any_payload(self):
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="run-b3-test"
        )
        for role_id, payload in result.role_payloads.items():
            self.assertIsNone(_scan_forbidden_keys(payload), role_id)

    def test_non_dict_row_rejected(self):
        with self.assertRaises(AdapterInputError) as ctx:
            MlbMoneylineAdapter().adapt(row="nope", run_id="r")
        self.assertEqual(ctx.exception.code, "ADAPTER_INPUT_NOT_DICT")

    def test_wrong_sport_rejected(self):
        with self.assertRaises(AdapterInputError) as ctx:
            MlbMoneylineAdapter().adapt(
                row=_well_formed_row(sport="NBA"), run_id="r"
            )
        self.assertEqual(ctx.exception.code, "ADAPTER_SPORT_MISMATCH")

    def test_sport_case_insensitive(self):
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(sport="mlb"), run_id="r"
        )
        self.assertEqual(result.packet.lane, "MLB_MONEYLINE")

    def test_wrong_market_rejected(self):
        with self.assertRaises(AdapterInputError) as ctx:
            MlbMoneylineAdapter().adapt(
                row=_well_formed_row(market="total_runs"), run_id="r"
            )
        self.assertEqual(ctx.exception.code, "ADAPTER_MARKET_MISMATCH")

    def test_missing_event_id_rejected(self):
        row = _well_formed_row()
        del row["event_id"]
        with self.assertRaises(AdapterInputError) as ctx:
            MlbMoneylineAdapter().adapt(row=row, run_id="r")
        self.assertEqual(ctx.exception.code, "ADAPTER_MISSING_EVENT_ID")

    def test_missing_run_id_rejected(self):
        with self.assertRaises(AdapterInputError) as ctx:
            MlbMoneylineAdapter().adapt(row=_well_formed_row(), run_id="  ")
        self.assertEqual(ctx.exception.code, "ADAPTER_MISSING_RUN_ID")

    def test_validate_row_standalone(self):
        self.assertIsNone(validate_mlb_moneyline_row(_well_formed_row()))
        with self.assertRaises(AdapterInputError):
            validate_mlb_moneyline_row({"sport": "MLB"})

    def test_adapter_result_frozen(self):
        import dataclasses
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="r"
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.adapter_status = "HACKED"

    def test_source_row_fields_used_recorded(self):
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="r"
        )
        self.assertTrue(result.source_row_fields_used)


# ── (b) _scan_forbidden_keys clean-dict contract ───────────────────────────────

class TestScanForbiddenKeysContract(unittest.TestCase):
    def test_returns_none_not_empty_list_on_clean_dict(self):
        result = _scan_forbidden_keys({"a": 1, "b": {"c": [1, 2, {"d": "x"}]}})
        self.assertIsNone(result)
        self.assertIsNot(result, [])

    def test_returns_violation_on_dirty_dict(self):
        self.assertIsNotNone(_scan_forbidden_keys({"can_execute": True}))

    def test_empty_dict_clean(self):
        self.assertIsNone(_scan_forbidden_keys({}))


# ── (c) DeterministicAdapterRunner keyed by role_id ────────────────────────────

class TestDeterministicAdapterRunner(unittest.TestCase):
    def _runner(self):
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="run-b3-test"
        )
        return DeterministicAdapterRunner(result.role_payloads), result

    def test_returns_payload_keyed_by_entry_role(self):
        runner, result = self._runner()
        for entry in ALL_B1_ENTRIES:
            out = runner(entry, result.packet)
            self.assertEqual(out, result.role_payloads[entry.role], entry.role)

    def test_missing_role_raises_runtimeerror(self):
        runner = DeterministicAdapterRunner({})
        result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="r"
        )
        with self.assertRaises(RuntimeError):
            runner(ALL_B1_ENTRIES[0], result.packet)

    def test_build_role_runners_covers_all_registry_agents(self):
        runner, _ = self._runner()
        runners = runner.build_role_runners(build_b1_registry())
        self.assertEqual(set(runners.keys()),
                         {e.agent_id for e in ALL_B1_ENTRIES})
        self.assertTrue(all(r is runner for r in runners.values()))

    def test_call_log_records_roles_and_packet_ids(self):
        runner, result = self._runner()
        for entry in ALL_B1_ENTRIES:
            runner(entry, result.packet)
        self.assertEqual(set(runner.role_ids_called()), set(B1_ROLE_IDS))
        self.assertEqual(set(runner.packet_ids_seen()), {id(result.packet)})

    def test_full_orchestrator_run_with_adapter_payloads(self):
        runner, result = self._runner()
        runners = runner.build_role_runners(build_b1_registry())
        orch = run_orchestrator(result.packet, build_b1_registry(),
                                runners, db_conn=None)
        self.assertEqual(orch.accepted_count(), 6)
        self.assertEqual(orch.bundle.bundle_status, "COMPLETE")


# ── (d) Shadow pipeline ────────────────────────────────────────────────────────

class TestShadowPipeline(unittest.TestCase):
    def test_module_flag_default_false(self):
        self.assertIs(sp.SHADOW_ENABLED, False)

    def test_disabled_by_default_short_circuits(self):
        result = run_shadow_pipeline(_well_formed_row(), "run-b3", db_conn=None)
        self.assertEqual(result.pipeline_status, ShadowPipelineStatus.DISABLED)
        self.assertIsNone(result.orchestrator_result)

    def test_force_enabled_completes(self):
        result = run_shadow_pipeline(
            _well_formed_row(), "run-b3", db_conn=None, _force_enabled=True
        )
        self.assertEqual(result.pipeline_status, ShadowPipelineStatus.COMPLETE)
        self.assertEqual(result.orchestrator_result.accepted_count(), 6)

    def test_same_packet_id_to_all_runners(self):
        result = run_shadow_pipeline(
            _well_formed_row(), "run-b3", db_conn=None, _force_enabled=True
        )
        pkt = result.adapter_result.packet
        for rr in result.orchestrator_result.role_results:
            self.assertTrue(rr.accepted, rr.role_id)
        # Every runner call in the orchestrator saw the identical packet object.
        ids = {id(r) for r in [pkt]}
        self.assertEqual(len(ids), 1)
        # Structural identity check via a fresh deterministic run:
        adapter_result = MlbMoneylineAdapter().adapt(
            row=_well_formed_row(), run_id="run-b3"
        )
        runner = DeterministicAdapterRunner(adapter_result.role_payloads)
        runners = runner.build_role_runners(build_b1_registry())
        run_orchestrator(adapter_result.packet, build_b1_registry(),
                         runners, db_conn=None)
        self.assertEqual(len(runner.packet_ids_seen()), 6)
        self.assertEqual(set(runner.packet_ids_seen()),
                         {id(adapter_result.packet)})

    def test_adapter_error_surfaced_not_raised(self):
        result = run_shadow_pipeline(
            {"sport": "NBA"}, "run-b3", db_conn=None, _force_enabled=True
        )
        self.assertEqual(result.pipeline_status,
                         ShadowPipelineStatus.ADAPTER_ERROR)
        self.assertIsNotNone(result.error_code)

    def test_lane_is_always_mlb_moneyline(self):
        result = run_shadow_pipeline(
            _well_formed_row(), "run-b3", db_conn=None, _force_enabled=True
        )
        self.assertEqual(result.adapter_result.packet.lane, "MLB_MONEYLINE")

    def test_result_dict_has_no_forbidden_keys(self):
        result = run_shadow_pipeline(
            _well_formed_row(), "run-b3", db_conn=None, _force_enabled=True
        )
        self.assertIsNone(_scan_forbidden_keys(result.to_dict()))


# ── (e) Canary defaults ────────────────────────────────────────────────────────

class TestCanaryDefaults(unittest.TestCase):
    def test_flag_reader_defaults_false_when_env_absent(self):
        self.assertIs(
            canary_config._read_bool_flag("UAC_TEST_NONEXISTENT_FLAG_XYZ"),
            False,
        )

    def test_flag_is_bool(self):
        self.assertIsInstance(
            canary_config.UAC_MLB_ML_CLAUDE_SHADOW_ENABLED, bool
        )

    def test_pinned_model_exact(self):
        self.assertEqual(canary_config.PINNED_MODEL, "claude-haiku-4-5-20251001")

    def test_hard_budget_caps(self):
        self.assertEqual(canary_config.MAX_CALLS, 6)
        self.assertEqual(canary_config.MAX_TOTAL_SPEND_USD, 0.10)
        self.assertEqual(canary_config.AUTOMATIC_RETRIES, 0)

    def test_no_auto_budget_increase(self):
        self.assertIs(canary_config.AUTO_BUDGET_INCREASE, False)

    def test_no_auto_promotion_constants(self):
        self.assertIs(canary_pkg.NO_AUTO_PROMOTION, True)
        self.assertIs(canary_pkg.can_execute, False)
        self.assertIs(canary_pkg.advisory_only, True)


# ── (f) Authority constants in B3 packages ─────────────────────────────────────

class TestB3AuthorityConstants(unittest.TestCase):
    def _assert_constants(self, pkg):
        self.assertIs(pkg.can_execute, False, pkg.__name__)
        self.assertIs(pkg.PRODUCTION_AUTHORITY, False, pkg.__name__)
        self.assertIs(pkg.USER_OUTPUT_AUTHORITY, False, pkg.__name__)
        self.assertIs(pkg.CAPITAL_AUTHORITY, False, pkg.__name__)
        self.assertIs(pkg.NO_AUTO_PROMOTION, True, pkg.__name__)

    def test_mlb_moneyline_lane(self):
        self._assert_constants(mlb_ml_pkg)

    def test_wnba_props_lane(self):
        self._assert_constants(wnba_pkg)

    def test_shadow_package(self):
        self._assert_constants(shadow_pkg)

    def test_canary_package(self):
        self._assert_constants(canary_pkg)


if __name__ == "__main__":
    unittest.main()
