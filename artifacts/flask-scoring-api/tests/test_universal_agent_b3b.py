"""
tests/test_universal_agent_b3b.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3B

Focused tests for the B3B offline MLB Moneyline shadow integration.

Test classes
------------
1.  TestDeterministicAdapterRunnerBasics     — runner construction, dispatch, fail-closed
2.  TestDeterministicAdapterRunnerCallLog    — packet identity, call_log inspection
3.  TestDeterministicAdapterRunnerBuildRunners — build_role_runners() wiring
4.  TestShadowPipelineDisabled               — default-off behavior
5.  TestShadowPipelineAdapterError           — AdapterInputError → ADAPTER_ERROR
6.  TestShadowPipelineFullPass               — clean row → COMPLETE bundle
7.  TestShadowPipelineDegradedRow            — evidence gaps → DEGRADED adapter, COMPLETE or PARTIAL bundle
8.  TestShadowPipelineForbiddenGovernanceKey — governance key in payload → GOVERNANCE_REJECTED → PARTIAL
9.  TestShadowPipelineMissingRunner          — NO_RUNNER for omitted agent → PARTIAL/FAILED
10. TestShadowPipelineContradiction          — HIGH contradiction (RULE-1) → PARTIAL
11. TestShadowPipelinePacketIdentity         — same packet id() to all six runners
12. TestShadowPipelinePersistencePath        — persisted flag wired to db_conn presence
13. TestShadowPipelineClassInterface         — ShadowPipeline.run() mirrors function
14. TestShadowPipelineNoProductionImports    — no app.py/Flask/Anthropic/OpenAI in shadow
15. TestShadowPipelineInvariants             — can_execute=False, SHADOW_ENABLED=False default

No live LLM/API calls, no app.py imports, no production route wiring.
"""
from __future__ import annotations

import unittest
from typing import Any


# ── Shared fixtures ─────────────────────────────────────────────────────────

def _full_row(**kwargs) -> dict:
    """Full-coverage MLB moneyline evidence row (post-preflight PASS)."""
    row = {
        "sport":                        "MLB",
        "market":                       "moneyline",
        "event_id":                     "mlb-2026-yankees-v-redsox-001",
        "team":                         "New York Yankees",
        "opponent":                     "Boston Red Sox",
        "team_id":                      "NYY",
        "opponent_team_id":             "BOS",
        "event_date":                   "2026-08-10",
        "starter_status":               "CONFIRMED",
        "starter_source":               "official_lineup",
        "lineup_status":                "CONFIRMED",
        "lineup_source":                "official_lineup",
        "event_status":                 "SCHEDULED",
        "weather_status":               "CLEAR",
        "sportsbook_no_vig_probability": 0.54,
        "kalshi_multiplier":            1.08,
        "breakeven":                    0.52,
        "model_probability":            0.56,
        "calibrated_probability_lower_bound": 0.53,
        "preflight_status":             "PASS",
        "hard_blockers":                [],
        "watch_blockers":               [],
        "odds_timestamp":               "2026-08-10T12:00:00Z",
        "lineup_timestamp":             "2026-08-10T11:30:00Z",
        "data_freshness_status":        "FRESH",
        "source_provenance":            {"sportsbook": "draftkings", "lineup": "mlb_stats_api"},
        "source_failures":              [],
        "source_conflicts":             [],
        "expected_strikeouts":          7.5,
        "starting_pitcher":             "Gerrit Cole",
        "opp_k_pct":                    0.22,
        "era_rolling":                  3.10,
        "bullpen_era":                  3.50,
        "park_factor":                  1.02,
        "inning_leash":                 "STANDARD",
        "pitcher_recent_ip":            5.8,
        "home_away_split":              "HOME",
        "run_line_movement":            "STABLE",
        "public_betting_pct":           0.58,
    }
    row.update(kwargs)
    return row


def _degraded_row(**kwargs) -> dict:
    """Minimal valid row — many optional fields absent."""
    row = {
        "sport":    "MLB",
        "market":   "moneyline",
        "event_id": "mlb-2026-degraded-001",
        "team":     "Chicago Cubs",
    }
    row.update(kwargs)
    return row


def _scratched_row(**kwargs) -> dict:
    """Row where starter is SCRATCHED — triggers RULE-1 contradiction when SS has data."""
    row = _full_row(
        event_id="mlb-2026-scratched-001",
        starter_status="SCRATCHED",
        preflight_status="FAIL",
        hard_blockers=["STARTER_SCRATCHED"],
    )
    row.update(kwargs)
    return row


# ── 1. DeterministicAdapterRunner basics ────────────────────────────────────

class TestDeterministicAdapterRunnerBasics(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.shadow.deterministic_runner import DeterministicAdapterRunner
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        self.DeterministicAdapterRunner = DeterministicAdapterRunner
        adapter = MlbMoneylineAdapter()
        result = adapter.adapt(row=_full_row(), run_id="run-b3b-basics")
        self.role_payloads = result.role_payloads

    def test_can_execute_false(self):
        from gate_engine.universal_agent.shadow import deterministic_runner as mod
        self.assertFalse(mod.can_execute)

    def test_init_with_dict(self):
        runner = self.DeterministicAdapterRunner(self.role_payloads)
        self.assertIsInstance(runner, self.DeterministicAdapterRunner)

    def test_init_non_dict_raises(self):
        with self.assertRaises(TypeError):
            self.DeterministicAdapterRunner(["not", "a", "dict"])

    def test_call_returns_payload_for_role(self):
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        runner = self.DeterministicAdapterRunner(self.role_payloads)
        registry = build_b1_registry()
        adapter_result = MlbMoneylineAdapter().adapt(row=_full_row(), run_id="x")
        packet = adapter_result.packet
        for entry in registry.all_agents():
            result = runner(entry, packet)
            self.assertIsInstance(result, dict)
            self.assertIn("advisory_findings", result)

    def test_missing_role_raises_runtime_error(self):
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        # Remove one role from payloads
        payloads = dict(self.role_payloads)
        del payloads["DATA_SLATE_INTEGRITY"]
        runner = self.DeterministicAdapterRunner(payloads)
        registry = build_b1_registry()
        adapter_result = MlbMoneylineAdapter().adapt(row=_full_row(), run_id="x")
        packet = adapter_result.packet
        # Find DSI entry
        dsi_entry = next(e for e in registry.all_agents() if e.role == "DATA_SLATE_INTEGRITY")
        with self.assertRaises(RuntimeError):
            runner(dsi_entry, packet)

    def test_call_log_starts_empty(self):
        runner = self.DeterministicAdapterRunner(self.role_payloads)
        self.assertEqual(runner.call_log, [])

    def test_call_log_records_each_call(self):
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        runner = self.DeterministicAdapterRunner(self.role_payloads)
        registry = build_b1_registry()
        adapter_result = MlbMoneylineAdapter().adapt(row=_full_row(), run_id="x")
        packet = adapter_result.packet
        for entry in registry.all_agents():
            runner(entry, packet)
        self.assertEqual(len(runner.call_log), 6)

    def test_error_message_names_missing_role(self):
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        payloads = {}  # empty → all roles missing
        runner = self.DeterministicAdapterRunner(payloads)
        registry = build_b1_registry()
        adapter_result = MlbMoneylineAdapter().adapt(row=_full_row(), run_id="x")
        packet = adapter_result.packet
        first_entry = list(registry.all_agents())[0]
        with self.assertRaises(RuntimeError) as ctx:
            runner(first_entry, packet)
        self.assertIn(first_entry.role, str(ctx.exception))


# ── 2. DeterministicAdapterRunner call_log / packet identity ────────────────

class TestDeterministicAdapterRunnerCallLog(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.shadow.deterministic_runner import DeterministicAdapterRunner
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        adapter = MlbMoneylineAdapter()
        result = adapter.adapt(row=_full_row(), run_id="run-id-log", snapshot_id="snap-log-001")
        self.runner = DeterministicAdapterRunner(result.role_payloads)
        self.registry = build_b1_registry()
        self.packet = result.packet
        # Exercise all six roles
        for entry in self.registry.all_agents():
            self.runner(entry, self.packet)

    def test_six_calls_recorded(self):
        self.assertEqual(len(self.runner.call_log), 6)

    def test_all_packet_ids_identical(self):
        ids = self.runner.packet_ids_seen()
        self.assertEqual(len(set(ids)), 1, msg=f"Expected one unique id, got: {ids}")

    def test_packet_id_matches_actual_packet(self):
        for pid in self.runner.packet_ids_seen():
            self.assertEqual(pid, id(self.packet))

    def test_snapshot_ids_all_correct(self):
        for snap in self.runner.snapshot_ids_seen():
            self.assertEqual(snap, "snap-log-001")

    def test_role_ids_called_are_six_b1_roles(self):
        called = set(self.runner.role_ids_called())
        expected = {
            "DATA_SLATE_INTEGRITY", "NEWS_STATUS", "MARKET_EXACT_LINE",
            "SPORT_SPECIALIST", "FAILURE_CONTRADICTION", "FINAL_REFRESH",
        }
        self.assertEqual(called, expected)

    def test_agent_ids_called_are_six_b1_agents(self):
        called = set(self.runner.agent_ids_called())
        expected = {e.agent_id for e in self.registry.all_agents()}
        self.assertEqual(called, expected)


# ── 3. build_role_runners wiring ─────────────────────────────────────────────

class TestDeterministicAdapterRunnerBuildRunners(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.shadow.deterministic_runner import DeterministicAdapterRunner
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        adapter = MlbMoneylineAdapter()
        result = adapter.adapt(row=_full_row(), run_id="run-build-runners")
        self.runner = DeterministicAdapterRunner(result.role_payloads)
        self.registry = build_b1_registry()
        self.role_runners = self.runner.build_role_runners(self.registry)

    def test_returns_dict(self):
        self.assertIsInstance(self.role_runners, dict)

    def test_six_entries(self):
        self.assertEqual(len(self.role_runners), 6)

    def test_keyed_by_agent_id(self):
        expected_agent_ids = {e.agent_id for e in self.registry.all_agents()}
        self.assertEqual(set(self.role_runners.keys()), expected_agent_ids)

    def test_all_values_are_same_runner_instance(self):
        for agent_id, runner in self.role_runners.items():
            self.assertIs(runner, self.runner, msg=f"Runner mismatch for {agent_id}")

    def test_runners_are_callable(self):
        for runner in self.role_runners.values():
            self.assertTrue(callable(runner))


# ── 4. Shadow pipeline disabled (default-off) ────────────────────────────────

class TestShadowPipelineDisabled(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import (
            run_shadow_pipeline, SHADOW_ENABLED, ShadowPipelineStatus
        )
        self.run_shadow_pipeline = run_shadow_pipeline
        self.SHADOW_ENABLED = SHADOW_ENABLED
        self.ShadowPipelineStatus = ShadowPipelineStatus

    def test_shadow_enabled_is_false_by_default(self):
        self.assertFalse(self.SHADOW_ENABLED)

    def test_default_returns_disabled_without_force(self):
        result = self.run_shadow_pipeline(_full_row(), run_id="run-disabled")
        self.assertEqual(result.pipeline_status, self.ShadowPipelineStatus.DISABLED)

    def test_disabled_result_shadow_enabled_false(self):
        result = self.run_shadow_pipeline(_full_row(), run_id="run-disabled-2")
        self.assertFalse(result.shadow_enabled)

    def test_disabled_result_adapter_result_none(self):
        result = self.run_shadow_pipeline(_full_row(), run_id="run-disabled-3")
        self.assertIsNone(result.adapter_result)

    def test_disabled_result_orchestrator_result_none(self):
        result = self.run_shadow_pipeline(_full_row(), run_id="run-disabled-4")
        self.assertIsNone(result.orchestrator_result)

    def test_disabled_result_run_id_echoed(self):
        result = self.run_shadow_pipeline(_full_row(), run_id="my-run-001")
        self.assertEqual(result.run_id, "my-run-001")

    def test_disabled_result_lane_is_mlb_moneyline(self):
        result = self.run_shadow_pipeline(_full_row(), run_id="run-lane")
        self.assertEqual(result.lane, "MLB_MONEYLINE")

    def test_disabled_result_no_error_code(self):
        result = self.run_shadow_pipeline(_full_row(), run_id="run-no-err")
        self.assertIsNone(result.error_code)

    def test_disabled_result_is_frozen(self):
        result = self.run_shadow_pipeline(_full_row(), run_id="run-frozen")
        with self.assertRaises(Exception):
            result.pipeline_status = "HACKED"  # type: ignore[misc]

    def test_disabled_on_invalid_row_does_not_raise(self):
        # Even a non-dict row returns DISABLED when not enabled (never reaches adapter)
        result = self.run_shadow_pipeline("not-a-dict", run_id="run-invalid-disabled")
        self.assertEqual(result.pipeline_status, self.ShadowPipelineStatus.DISABLED)


# ── 5. Adapter error path ─────────────────────────────────────────────────────

class TestShadowPipelineAdapterError(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import (
            run_shadow_pipeline, ShadowPipelineStatus,
        )
        self.run = run_shadow_pipeline
        self.Status = ShadowPipelineStatus

    def test_missing_event_id_gives_adapter_error(self):
        row = _full_row()
        del row["event_id"]
        result = self.run(row, run_id="r1", _force_enabled=True)
        self.assertEqual(result.pipeline_status, self.Status.ADAPTER_ERROR)

    def test_wrong_sport_gives_adapter_error(self):
        row = _full_row(sport="NFL")
        result = self.run(row, run_id="r2", _force_enabled=True)
        self.assertEqual(result.pipeline_status, self.Status.ADAPTER_ERROR)

    def test_wrong_market_gives_adapter_error(self):
        row = _full_row(market="player_strikeouts")
        result = self.run(row, run_id="r3", _force_enabled=True)
        self.assertEqual(result.pipeline_status, self.Status.ADAPTER_ERROR)

    def test_adapter_error_shadow_enabled_true(self):
        row = _full_row()
        del row["event_id"]
        result = self.run(row, run_id="r4", _force_enabled=True)
        self.assertTrue(result.shadow_enabled)

    def test_adapter_error_has_error_code(self):
        row = _full_row()
        del row["event_id"]
        result = self.run(row, run_id="r5", _force_enabled=True)
        self.assertIsNotNone(result.error_code)
        self.assertIn("ADAPTER", result.error_code)

    def test_adapter_error_has_error_message(self):
        row = _full_row()
        del row["event_id"]
        result = self.run(row, run_id="r6", _force_enabled=True)
        self.assertIsNotNone(result.error_message)

    def test_adapter_error_orchestrator_result_is_none(self):
        row = _full_row()
        del row["event_id"]
        result = self.run(row, run_id="r7", _force_enabled=True)
        self.assertIsNone(result.orchestrator_result)

    def test_adapter_error_adapter_result_is_none(self):
        row = _full_row()
        del row["event_id"]
        result = self.run(row, run_id="r8", _force_enabled=True)
        self.assertIsNone(result.adapter_result)

    def test_non_dict_row_gives_adapter_error(self):
        result = self.run("not-a-dict", run_id="r9", _force_enabled=True)
        self.assertEqual(result.pipeline_status, self.Status.ADAPTER_ERROR)


# ── 6. Full pass — clean row → COMPLETE ──────────────────────────────────────

class TestShadowPipelineFullPass(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import (
            run_shadow_pipeline, ShadowPipelineStatus,
        )
        self.result = run_shadow_pipeline(
            _full_row(), run_id="run-pass-001",
            snapshot_id="snap-pass-001",
            _force_enabled=True,
        )
        self.Status = ShadowPipelineStatus

    def test_pipeline_status_complete(self):
        self.assertEqual(self.result.pipeline_status, self.Status.COMPLETE)

    def test_shadow_enabled_true(self):
        self.assertTrue(self.result.shadow_enabled)

    def test_no_error_code(self):
        self.assertIsNone(self.result.error_code)

    def test_no_error_message(self):
        self.assertIsNone(self.result.error_message)

    def test_adapter_result_present(self):
        self.assertIsNotNone(self.result.adapter_result)

    def test_adapter_result_complete(self):
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import AdapterStatus
        self.assertEqual(self.result.adapter_result.adapter_status, AdapterStatus.COMPLETE)

    def test_orchestrator_result_present(self):
        self.assertIsNotNone(self.result.orchestrator_result)

    def test_six_roles_accepted(self):
        self.assertEqual(self.result.orchestrator_result.accepted_count(), 6)

    def test_zero_roles_failed(self):
        self.assertEqual(self.result.orchestrator_result.failed_count(), 0)

    def test_bundle_status_complete(self):
        from gate_engine.universal_agent.bundle_assembler import BundleStatus
        self.assertEqual(
            self.result.orchestrator_result.bundle.bundle_status,
            BundleStatus.COMPLETE,
        )

    def test_no_contradictions_on_clean_row(self):
        self.assertEqual(len(self.result.orchestrator_result.contradictions), 0)

    def test_run_id_echoed(self):
        self.assertEqual(self.result.run_id, "run-pass-001")

    def test_lane_is_mlb_moneyline(self):
        self.assertEqual(self.result.lane, "MLB_MONEYLINE")

    def test_not_persisted_without_db_conn(self):
        self.assertFalse(self.result.orchestrator_result.persisted)

    def test_is_complete_helper(self):
        self.assertTrue(self.result.is_complete())

    def test_to_dict_has_expected_keys(self):
        d = self.result.to_dict()
        for key in ("pipeline_status", "shadow_enabled", "run_id", "bundle_status",
                    "accepted_count", "failed_count", "contradiction_count"):
            self.assertIn(key, d)


# ── 7. Degraded row ───────────────────────────────────────────────────────────

class TestShadowPipelineDegradedRow(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import run_shadow_pipeline
        self.result = run_shadow_pipeline(
            _degraded_row(), run_id="run-degraded-001", _force_enabled=True,
        )

    def test_pipeline_returns_result(self):
        self.assertIsNotNone(self.result)

    def test_adapter_result_degraded(self):
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import AdapterStatus
        self.assertEqual(self.result.adapter_result.adapter_status, AdapterStatus.DEGRADED)

    def test_orchestrator_still_completes(self):
        self.assertIsNotNone(self.result.orchestrator_result)

    def test_accepted_count_six_even_with_degraded_inputs(self):
        # All six roles still run; they get UNKNOWN/MISSING sentinels and still pass B1
        self.assertEqual(self.result.orchestrator_result.accepted_count(), 6)

    def test_degradation_reasons_present(self):
        self.assertGreater(len(self.result.adapter_result.degradation_reasons), 0)


# ── 8. Forbidden governance key rejection ────────────────────────────────────

class TestShadowPipelineForbiddenGovernanceKey(unittest.TestCase):
    """
    Inject a governance key into one role payload via DeterministicAdapterRunner.
    The B2 orchestrator's B0 post-hook must catch it → GOVERNANCE_REJECTED.
    Bundle must be PARTIAL or FAILED (not COMPLETE).
    """

    def _run_with_poisoned_role(self, role_id: str, governance_key: str) -> Any:
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        from gate_engine.universal_agent.shadow.deterministic_runner import DeterministicAdapterRunner
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        from gate_engine.universal_agent.orchestrator import run_orchestrator

        adapter_result = MlbMoneylineAdapter().adapt(row=_full_row(), run_id="gov-test")
        # Inject governance key into the target role payload
        payloads = {}
        for rid, payload in adapter_result.role_payloads.items():
            if rid == role_id:
                poisoned = dict(payload)
                poisoned[governance_key] = "INJECTED"
                payloads[rid] = poisoned
            else:
                payloads[rid] = payload

        runner = DeterministicAdapterRunner(payloads)
        registry = build_b1_registry()
        role_runners = runner.build_role_runners(registry)
        return run_orchestrator(
            packet=adapter_result.packet,
            registry=registry,
            role_runners=role_runners,
        )

    def test_final_decision_in_dsi_gives_governance_rejected(self):
        result = self._run_with_poisoned_role("DATA_SLATE_INTEGRITY", "final_decision")
        dsi = result.result_for_role("DATA_SLATE_INTEGRITY")
        from gate_engine.universal_agent.role_runner import RoleRunnerStatus
        self.assertEqual(dsi.status, RoleRunnerStatus.GOVERNANCE_REJECTED)

    def test_can_execute_in_news_status_gives_governance_rejected(self):
        result = self._run_with_poisoned_role("NEWS_STATUS", "can_execute")
        ns = result.result_for_role("NEWS_STATUS")
        from gate_engine.universal_agent.role_runner import RoleRunnerStatus
        self.assertEqual(ns.status, RoleRunnerStatus.GOVERNANCE_REJECTED)

    def test_governance_rejected_role_is_not_accepted(self):
        result = self._run_with_poisoned_role("MARKET_EXACT_LINE", "stake_tier")
        mel = result.result_for_role("MARKET_EXACT_LINE")
        self.assertFalse(mel.accepted)

    def test_governance_rejected_role_not_in_accepted_findings(self):
        result = self._run_with_poisoned_role("SPORT_SPECIALIST", "is_playable")
        self.assertNotIn("SPORT_SPECIALIST", result.bundle.accepted_findings)

    def test_bundle_not_complete_when_role_governance_rejected(self):
        result = self._run_with_poisoned_role("FINAL_REFRESH", "qualifying_label")
        from gate_engine.universal_agent.bundle_assembler import BundleStatus
        self.assertNotEqual(result.bundle.bundle_status, BundleStatus.COMPLETE)

    def test_governance_rejected_appears_in_failed_role_ids(self):
        result = self._run_with_poisoned_role("FAILURE_CONTRADICTION", "trade")
        self.assertIn("FAILURE_CONTRADICTION", result.bundle.failed_role_ids)


# ── 9. Missing runner → NO_RUNNER → PARTIAL ──────────────────────────────────

class TestShadowPipelineMissingRunner(unittest.TestCase):
    """
    Remove one agent from role_runners dict before calling orchestrator.
    That agent must get NO_RUNNER (fail-closed) and bundle must not be COMPLETE.
    """

    def _run_with_one_agent_missing(self, omit_role_id: str) -> Any:
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        from gate_engine.universal_agent.shadow.deterministic_runner import DeterministicAdapterRunner
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        from gate_engine.universal_agent.orchestrator import run_orchestrator

        adapter_result = MlbMoneylineAdapter().adapt(row=_full_row(), run_id="no-runner-test")
        runner = DeterministicAdapterRunner(adapter_result.role_payloads)
        registry = build_b1_registry()
        role_runners = runner.build_role_runners(registry)
        # Remove the target agent_id
        omit_agent_id = next(
            e.agent_id for e in registry.all_agents() if e.role == omit_role_id
        )
        del role_runners[omit_agent_id]
        return run_orchestrator(
            packet=adapter_result.packet,
            registry=registry,
            role_runners=role_runners,
        )

    def test_missing_dsi_runner_gives_no_runner_status(self):
        result = self._run_with_one_agent_missing("DATA_SLATE_INTEGRITY")
        dsi = result.result_for_role("DATA_SLATE_INTEGRITY")
        from gate_engine.universal_agent.role_runner import RoleRunnerStatus
        self.assertEqual(dsi.status, RoleRunnerStatus.NO_RUNNER)

    def test_missing_runner_not_in_accepted_findings(self):
        result = self._run_with_one_agent_missing("NEWS_STATUS")
        self.assertNotIn("NEWS_STATUS", result.bundle.accepted_findings)

    def test_bundle_not_complete_when_runner_missing(self):
        result = self._run_with_one_agent_missing("MARKET_EXACT_LINE")
        from gate_engine.universal_agent.bundle_assembler import BundleStatus
        self.assertNotEqual(result.bundle.bundle_status, BundleStatus.COMPLETE)

    def test_missing_role_in_failed_role_ids(self):
        result = self._run_with_one_agent_missing("SPORT_SPECIALIST")
        self.assertIn("SPORT_SPECIALIST", result.bundle.failed_role_ids)

    def test_five_other_roles_still_accepted(self):
        result = self._run_with_one_agent_missing("FINAL_REFRESH")
        self.assertEqual(result.accepted_count(), 5)


# ── 10. Contradiction behavior ────────────────────────────────────────────────

class TestShadowPipelineContradiction(unittest.TestCase):
    """
    A SCRATCHED starter row (player_status=OUT) with a statistical_assessment
    dict triggers RULE-1 (PLAYER-OUT-POSITIVE-ASSESSMENT, severity=HIGH).
    This causes bundle_status=PARTIAL.

    A hard blocker row also triggers RULE-3 (FAILURE-HIGH-SEVERITY) because
    FAILURE_CONTRADICTION will have contradiction_detected=True, severity=HIGH.
    """

    def setUp(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import run_shadow_pipeline
        # SCRATCHED row: starter_status=SCRATCHED + SS has full statistical_assessment dict
        self.result = run_shadow_pipeline(
            _scratched_row(), run_id="run-contradiction-001", _force_enabled=True,
        )

    def test_pipeline_returns_result(self):
        self.assertIsNotNone(self.result)

    def test_all_six_roles_accepted_despite_contradiction(self):
        # Contradiction is detected at bundle level; all roles still execute and accept
        self.assertEqual(self.result.orchestrator_result.accepted_count(), 6)

    def test_contradictions_detected(self):
        # RULE-1 (HIGH): player OUT + positive SS dict; RULE-3 (HIGH): FC hard blockers
        self.assertGreater(len(self.result.orchestrator_result.contradictions), 0)

    def test_has_high_severity_contradiction(self):
        contradictions = self.result.orchestrator_result.contradictions
        severities = {c.severity for c in contradictions}
        self.assertIn("HIGH", severities)

    def test_bundle_status_is_partial_not_complete(self):
        from gate_engine.universal_agent.bundle_assembler import BundleStatus
        self.assertEqual(
            self.result.orchestrator_result.bundle.bundle_status,
            BundleStatus.PARTIAL,
        )

    def test_pipeline_status_is_partial(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import ShadowPipelineStatus
        self.assertEqual(self.result.pipeline_status, ShadowPipelineStatus.PARTIAL)

    def test_contradiction_rule_ids_surfaced(self):
        rule_ids = {c.rule_id for c in self.result.orchestrator_result.contradictions}
        # RULE-1 fires: OUT + statistical dict; RULE-3 fires: hard blockers → FC HIGH
        self.assertTrue(
            rule_ids & {"RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT", "RULE-3-FAILURE-HIGH-SEVERITY"},
            msg=f"Expected at least one HIGH rule, got {rule_ids}",
        )


# ── 11. Packet identity across all six runners ───────────────────────────────

class TestShadowPipelinePacketIdentity(unittest.TestCase):

    def test_same_packet_object_to_all_six_runners(self):
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        from gate_engine.universal_agent.shadow.deterministic_runner import DeterministicAdapterRunner
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        from gate_engine.universal_agent.orchestrator import run_orchestrator

        adapter_result = MlbMoneylineAdapter().adapt(
            row=_full_row(), run_id="packet-id-test", snapshot_id="snap-pid-001"
        )
        runner = DeterministicAdapterRunner(adapter_result.role_payloads)
        registry = build_b1_registry()
        role_runners = runner.build_role_runners(registry)

        run_orchestrator(
            packet=adapter_result.packet,
            registry=registry,
            role_runners=role_runners,
        )

        ids = runner.packet_ids_seen()
        self.assertEqual(len(ids), 6, msg="Expected 6 runner calls")
        self.assertEqual(len(set(ids)), 1, msg=f"Expected one unique packet id, got: {ids}")
        self.assertEqual(ids[0], id(adapter_result.packet))

    def test_snapshot_id_consistent_across_runners(self):
        from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import MlbMoneylineAdapter
        from gate_engine.universal_agent.shadow.deterministic_runner import DeterministicAdapterRunner
        from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
        from gate_engine.universal_agent.orchestrator import run_orchestrator

        adapter_result = MlbMoneylineAdapter().adapt(
            row=_full_row(), run_id="snap-id-test", snapshot_id="snap-consistent-001"
        )
        runner = DeterministicAdapterRunner(adapter_result.role_payloads)
        registry = build_b1_registry()
        role_runners = runner.build_role_runners(registry)
        run_orchestrator(
            packet=adapter_result.packet,
            registry=registry,
            role_runners=role_runners,
        )
        for snap in runner.snapshot_ids_seen():
            self.assertEqual(snap, "snap-consistent-001")


# ── 12. Persistence path ──────────────────────────────────────────────────────

class TestShadowPipelinePersistencePath(unittest.TestCase):
    """
    Verify that persisted=True when db_conn is provided and False when None.
    Uses a lightweight SQLite in-memory mock adapter since psycopg2 requires
    a live Postgres connection.  The audit_store.py functions use the
    psycopg2 cursor API (context-manager + cursor() + execute()), so we
    provide a minimal compatible mock.
    """

    class _MockCursor:
        def __init__(self): self.executed = []
        def execute(self, sql, params=None): self.executed.append(sql)
        def fetchone(self): return None
        def __enter__(self): return self
        def __exit__(self, *_): pass

    class _MockConn:
        def __init__(self): self.cursors = []
        def cursor(self):
            c = TestShadowPipelinePersistencePath._MockCursor()
            self.cursors.append(c)
            return c
        def commit(self): pass

    def test_persisted_false_when_no_db_conn(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import run_shadow_pipeline
        result = run_shadow_pipeline(
            _full_row(), run_id="persist-no-db", _force_enabled=True,
        )
        self.assertFalse(result.orchestrator_result.persisted)

    def test_persisted_true_when_db_conn_provided(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import run_shadow_pipeline
        mock_conn = self._MockConn()
        result = run_shadow_pipeline(
            _full_row(), run_id="persist-with-db", _force_enabled=True,
            db_conn=mock_conn,
        )
        self.assertTrue(result.orchestrator_result.persisted)

    def test_no_other_tables_touched(self):
        """All SQL must reference only uac_* tables."""
        from gate_engine.universal_agent.shadow.shadow_pipeline import run_shadow_pipeline
        mock_conn = self._MockConn()
        run_shadow_pipeline(
            _full_row(), run_id="table-guard", _force_enabled=True,
            db_conn=mock_conn,
        )
        all_sql = " ".join(
            sql for cursor in mock_conn.cursors for sql in cursor.executed
        )
        # Collect all table names referenced (approximate: words after FROM/INTO/TABLE)
        import re
        tables = set(re.findall(r'(?:FROM|INTO|TABLE\s+IF\s+NOT\s+EXISTS)\s+(\w+)',
                                all_sql, re.IGNORECASE))
        non_uac = {t for t in tables if not t.lower().startswith("uac_")}
        self.assertEqual(non_uac, set(), msg=f"Non-uac tables touched: {non_uac}")


# ── 13. ShadowPipeline class interface ───────────────────────────────────────

class TestShadowPipelineClassInterface(unittest.TestCase):

    def setUp(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import (
            ShadowPipeline, ShadowPipelineStatus,
        )
        self.ShadowPipeline = ShadowPipeline
        self.Status = ShadowPipelineStatus

    def test_can_execute_false(self):
        self.assertFalse(self.ShadowPipeline.can_execute)

    def test_run_method_exists(self):
        pipeline = self.ShadowPipeline()
        self.assertTrue(callable(pipeline.run))

    def test_run_disabled_returns_disabled(self):
        pipeline = self.ShadowPipeline()
        result = pipeline.run(_full_row(), run_id="cls-disabled")
        self.assertEqual(result.pipeline_status, self.Status.DISABLED)

    def test_run_force_enabled_returns_complete(self):
        pipeline = self.ShadowPipeline()
        result = pipeline.run(_full_row(), run_id="cls-enabled", _force_enabled=True)
        self.assertEqual(result.pipeline_status, self.Status.COMPLETE)

    def test_run_adapter_error_propagates(self):
        pipeline = self.ShadowPipeline()
        row = _full_row(sport="NFL")
        result = pipeline.run(row, run_id="cls-err", _force_enabled=True)
        self.assertEqual(result.pipeline_status, self.Status.ADAPTER_ERROR)


# ── 14. No production imports ────────────────────────────────────────────────

class TestShadowPipelineNoProductionImports(unittest.TestCase):
    """
    Verify that shadow package modules do not import from app.py, Flask,
    Anthropic, OpenAI, or HTTP libraries.
    """

    def _source_of(self, module_path: str) -> str:
        import importlib.util, pathlib
        spec = importlib.util.find_spec(module_path)
        if spec is None or spec.origin is None:
            self.fail(f"Cannot locate module: {module_path}")
        return pathlib.Path(spec.origin).read_text()

    def _check_forbidden(self, module_path: str, forbidden: list[str]) -> None:
        source = self._source_of(module_path)
        for token in forbidden:
            self.assertNotIn(
                token, source,
                msg=f"Forbidden token {token!r} found in {module_path}",
            )

    def test_shadow_init_no_app_or_flask(self):
        self._check_forbidden(
            "gate_engine.universal_agent.shadow",
            ["from app", "import app", "flask", "Flask", "@app.route"],
        )

    def test_deterministic_runner_no_live_api(self):
        self._check_forbidden(
            "gate_engine.universal_agent.shadow.deterministic_runner",
            ["anthropic", "openai", "requests", "httpx", "aiohttp",
             "from app", "import app", "flask"],
        )

    def test_shadow_pipeline_no_live_api(self):
        self._check_forbidden(
            "gate_engine.universal_agent.shadow.shadow_pipeline",
            ["anthropic", "openai", "requests", "httpx", "aiohttp",
             "from app", "import app", "flask", "@app.route"],
        )

    def test_shadow_pipeline_no_terminal_label_authority(self):
        source = self._source_of("gate_engine.universal_agent.shadow.shadow_pipeline")
        forbidden = [
            "final_decision", "stake_tier", "FINAL_APPROVED",
            "capital_alloc", "execute_trade", "can_execute = True",
            "terminal_label", "qualifying_label",
        ]
        for token in forbidden:
            self.assertNotIn(
                token, source,
                msg=f"Terminal-label/authority token {token!r} in shadow_pipeline",
            )

    def test_deterministic_runner_no_terminal_label_authority(self):
        source = self._source_of("gate_engine.universal_agent.shadow.deterministic_runner")
        forbidden = [
            "final_decision", "stake_tier", "FINAL_APPROVED",
            "capital_alloc", "can_execute = True",
        ]
        for token in forbidden:
            self.assertNotIn(token, source,
                msg=f"Authority token {token!r} in deterministic_runner")


# ── 15. Invariants ────────────────────────────────────────────────────────────

class TestShadowPipelineInvariants(unittest.TestCase):

    def test_shadow_module_can_execute_false(self):
        from gate_engine.universal_agent import shadow as mod
        self.assertFalse(mod.can_execute)

    def test_shadow_pipeline_module_can_execute_false(self):
        from gate_engine.universal_agent.shadow import shadow_pipeline as mod
        self.assertFalse(mod.can_execute)

    def test_deterministic_runner_module_can_execute_false(self):
        from gate_engine.universal_agent.shadow import deterministic_runner as mod
        self.assertFalse(mod.can_execute)

    def test_shadow_pipeline_class_can_execute_false(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import ShadowPipeline
        self.assertFalse(ShadowPipeline.can_execute)

    def test_shadow_enabled_is_false_at_module_level(self):
        from gate_engine.universal_agent.shadow import shadow_pipeline as mod
        self.assertFalse(mod.SHADOW_ENABLED)

    def test_advisory_findings_do_not_override_deterministic_decision(self):
        """
        Advisory outputs (accepted_findings) must not contain any field that
        could be mistaken for a terminal label or execution authority.
        """
        from gate_engine.universal_agent.shadow.shadow_pipeline import run_shadow_pipeline
        from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS
        result = run_shadow_pipeline(
            _full_row(), run_id="invariant-gov", _force_enabled=True,
        )
        findings = result.orchestrator_result.bundle.accepted_findings
        for role_id, af in findings.items():
            for key in af:
                self.assertNotIn(
                    key, FORBIDDEN_GOVERNANCE_KEYS,
                    msg=f"Governance key {key!r} found in {role_id} advisory_findings",
                )

    def test_shadow_pipeline_result_is_frozen_dataclass(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import run_shadow_pipeline
        result = run_shadow_pipeline(_full_row(), run_id="inv-frozen", _force_enabled=True)
        with self.assertRaises(Exception):
            result.pipeline_status = "HACKED"  # type: ignore[misc]

    def test_all_pipeline_statuses_known(self):
        from gate_engine.universal_agent.shadow.shadow_pipeline import ShadowPipelineStatus
        statuses = ShadowPipelineStatus.all_statuses()
        self.assertIn("COMPLETE", statuses)
        self.assertIn("PARTIAL", statuses)
        self.assertIn("FAILED", statuses)
        self.assertIn("DISABLED", statuses)
        self.assertIn("ADAPTER_ERROR", statuses)

    def test_public_api_exported_from_shadow_init(self):
        from gate_engine.universal_agent import shadow
        for name in ("DeterministicAdapterRunner", "ShadowPipeline",
                     "ShadowPipelineResult", "ShadowPipelineStatus",
                     "run_shadow_pipeline", "SHADOW_ENABLED"):
            self.assertTrue(hasattr(shadow, name), msg=f"Missing export: {name}")


if __name__ == "__main__":
    unittest.main()
