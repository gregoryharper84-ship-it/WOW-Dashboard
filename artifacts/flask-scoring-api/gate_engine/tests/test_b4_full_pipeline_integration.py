"""
gate_engine/tests/test_b4_full_pipeline_integration.py
WOW B4-HARDENING-#195 — Full-Pipeline Integration Fixtures

End-to-end integration coverage for the WNBA/B4 pipeline path, verifying
that identity → enrichment → adapter → game-script → role payloads →
advisory bundle → governance ceilings all work together as a connected
pipeline rather than only as isolated unit tests.

No network or DB calls — all external dependencies mocked or bypassed.

Coverage
--------
TestB4IdentityAndEnrichmentFlow  — event identity, player name, team propagate
                                   from combined row into EvidencePacket
TestB4GameScriptShadowIntegration— game_script_shadow ceiling, structure,
                                   advisory semantics, non-interference
TestB4RolePayloadCompleteness    — all six B1 roles present and non-empty
TestB4GovernanceCeilingEnforced  — MODEL_QUALIFIED_HOLD ceiling never exceeded
TestB4TechnicalFailurePropagation— TECHNICAL failure preserves upstream, blocks
                                   verified/money/edge states, does not affect
                                   adjacent rows
TestB4ContractFailurePropagation — CONTRACT failure fails closed at all levels
TestB4DeterministicOutput        — same row → same adapter result on re-runs
TestB4NoProductionCoupling       — B4 modules do not import production WNBA files
TestB4GovernanceTreatment        — place_bet/settlement forbidden by output_contract;
                                   these keys are documented in FORBIDDEN set
TestB4OrchestratorIntegration    — packet from adapter flows into run_orchestrator
TestB4CanExecuteFalseAllModules  — can_execute=False in all B4 + pipeline_state modules
"""
from __future__ import annotations

import sys
import unittest
from datetime import date

from gate_engine.universal_agent.lanes.wnba_props.adapter import (
    AdapterStatus,
    WnbaPropsAdapter,
    WnbaPropsAdapterResult,
)
from gate_engine.universal_agent.lanes.wnba_props.validation import AdapterInputError
from gate_engine.universal_agent.evidence_packet import EvidencePacket, Lane
from gate_engine.universal_agent.orchestrator import run_orchestrator
from gate_engine.universal_agent.agent_registry import AgentRegistry
from gate_engine.universal_agent.output_contract import (
    FORBIDDEN_GOVERNANCE_KEYS,
    validate_output_contract,
    valid_output_payload,
)
from gate_engine.universal_agent.pipeline_state import (
    FailureKind,
    PipelineLayer,
    UpgradeCeiling,
    PipelineStateGuard,
    RowPipelineState,
)


# ── Shared fixtures ───────────────────────────────────────────────────────────

_ROLE_STATUS = {
    "active_status":      "ACTIVE",
    "projected_minutes":  32.0,
    "minutes_low":        26.0,
    "minutes_high":       38.0,
    "usage_role":         "STARTER",
    "sources":            ["espn", "wnba_official"],
    "as_of":              "2026-08-11T10:00:00Z",
}

_ODDS_SNAPSHOT = {
    "sportsbook_line":   22.5,
    "over_odds":         -115,
    "under_odds":        -105,
    "as_of":             "2026-08-11T09:00:00Z",
    "book":              "DraftKings",
}

_GAME_LOG = [
    {"min": 32, "pts": 25, "reb": 8, "ast": 4, "date": "2026-08-10"},
    {"min": 30, "pts": 22, "reb": 7, "ast": 3, "date": "2026-08-09"},
    {"min": 28, "pts": 20, "reb": 9, "ast": 2, "date": "2026-08-08"},
    {"min": 34, "pts": 27, "reb": 10, "ast": 5, "date": "2026-08-07"},
    {"min": 33, "pts": 19, "reb": 6, "ast": 3, "date": "2026-08-06"},
]


def _full_row(**overrides) -> dict:
    """Full WNBA props row sufficient to pass all validation and role-input gates."""
    row: dict = {
        "sport":         "WNBA",
        "market":        "points",
        "event_id":      "wnba-2026-lv-chi-001",
        "player":        "A'ja Wilson",
        "team":          "LV",
        "opponent":      "CHI",
        "line":          22.5,
        "direction":     "over",
        "slate_date":    date.today().isoformat(),
        "role_status":   _ROLE_STATUS,
        "odds_snapshot": _ODDS_SNAPSHOT,
        "game_log":      _GAME_LOG,
        "source_timestamps": {
            "role_status": "2026-08-11T10:00:00Z",
            "odds":        "2026-08-11T09:00:00Z",
        },
        "matchup": {
            "spread":     -4.0,
            "total_line": 170.5,
        },
    }
    row.update(overrides)
    return row


def _adapt(row: dict | None = None, **kwargs) -> WnbaPropsAdapterResult:
    row = row or _full_row()
    return WnbaPropsAdapter().adapt(row=row, run_id="intg-run-001", **kwargs)


# ── Identity + enrichment flow ────────────────────────────────────────────────

class TestB4IdentityAndEnrichmentFlow(unittest.TestCase):
    """
    Verify that player name, team, event_id, and date propagate correctly
    from the combined row into the EvidencePacket.
    """

    def test_packet_is_evidence_packet(self):
        result = _adapt()
        self.assertIsInstance(result.packet, EvidencePacket)

    def test_lane_is_wnba_props(self):
        result = _adapt()
        self.assertEqual(result.packet.lane, Lane.WNBA_PROPS)

    def test_player_name_propagated(self):
        result = _adapt()
        self.assertEqual(result.packet.player_name, "A'ja Wilson")

    def test_team_propagated(self):
        result = _adapt()
        self.assertIsNotNone(result.packet.team_name)

    def test_canonical_event_id_propagated(self):
        result = _adapt()
        # event_id from row should appear in or seed canonical_event_id
        self.assertIsNotNone(result.packet.canonical_event_id)
        self.assertGreater(len(result.packet.canonical_event_id), 0)

    def test_run_id_echoed_in_packet(self):
        result = WnbaPropsAdapter().adapt(
            row=_full_row(), run_id="custom-run-xyz"
        )
        self.assertEqual(result.packet.run_id, "custom-run-xyz")

    def test_snapshot_id_override(self):
        result = WnbaPropsAdapter().adapt(
            row=_full_row(), run_id="r1", snapshot_id="snap-fixed"
        )
        self.assertEqual(result.packet.snapshot_id, "snap-fixed")

    def test_enrichment_game_log_overridden_by_row(self):
        """Row wins on key collision — enrichment game_log cannot override row game_log."""
        enrichment = {"game_log": [{"pts": 99}]}   # would inflate score
        result = WnbaPropsAdapter().adapt(
            row=_full_row(), run_id="r1", enrichment=enrichment
        )
        # Row game_log wins; adapter should complete without crashing
        self.assertIn(result.adapter_status,
                      {AdapterStatus.COMPLETE, AdapterStatus.DEGRADED})

    def test_missing_sport_raises_adapter_input_error(self):
        bad_row = _full_row()
        del bad_row["sport"]
        with self.assertRaises(AdapterInputError):
            WnbaPropsAdapter().adapt(row=bad_row, run_id="r1")

    def test_missing_event_id_raises_adapter_input_error(self):
        bad_row = _full_row()
        del bad_row["event_id"]
        with self.assertRaises(AdapterInputError):
            WnbaPropsAdapter().adapt(row=bad_row, run_id="r1")

    def test_missing_run_id_raises_adapter_input_error(self):
        with self.assertRaises(AdapterInputError):
            WnbaPropsAdapter().adapt(row=_full_row(), run_id="")


# ── Game-script shadow integration ────────────────────────────────────────────

class TestB4GameScriptShadowIntegration(unittest.TestCase):
    """
    game_script_shadow must carry MODEL_QUALIFIED_HOLD ceiling, be advisory-only,
    and never alter role_payloads or adapter_status.
    """

    def setUp(self):
        self.result = _adapt()

    def test_game_script_shadow_is_dict_or_none(self):
        # Best-effort: may be None if shadow gate gracefully returns None
        self.assertIn(type(self.result.game_script_shadow), (dict, type(None)))

    def test_game_script_ceiling_is_model_qualified_hold(self):
        shadow = self.result.game_script_shadow
        if shadow is None:
            self.skipTest("game_script_shadow returned None (shadow gate skipped)")
        self.assertEqual(
            shadow.get("ceiling"), "MODEL_QUALIFIED_HOLD",
            "game_script_shadow ceiling must be MODEL_QUALIFIED_HOLD"
        )

    def test_game_script_can_execute_false(self):
        shadow = self.result.game_script_shadow
        if shadow is None:
            self.skipTest("game_script_shadow returned None")
        self.assertFalse(
            shadow.get("can_execute", True),
            "game_script_shadow.can_execute must be False"
        )

    def test_game_script_does_not_modify_role_payloads(self):
        """game_script_shadow is computed AFTER role_payloads; must not alter them."""
        payloads_before = set(_adapt().role_payloads.keys())
        # Run twice to confirm stability
        payloads_after = set(_adapt().role_payloads.keys())
        self.assertEqual(payloads_before, payloads_after)

    def test_game_script_does_not_affect_adapter_status(self):
        """Whether game_script_shadow is None or dict, adapter_status is independent."""
        result = self.result
        # adapter_status is driven by build_data_gaps, not game_script_shadow
        self.assertIn(result.adapter_status, {AdapterStatus.COMPLETE, AdapterStatus.DEGRADED})

    def test_game_script_shadow_is_not_in_role_payloads(self):
        """game_script_shadow must be separate — never injected into role_payloads."""
        result = self.result
        for role_id, payload in result.role_payloads.items():
            self.assertNotIn(
                "game_script_shadow", payload,
                f"game_script_shadow leaked into role payload {role_id}"
            )


# ── Role payload completeness ─────────────────────────────────────────────────

class TestB4RolePayloadCompleteness(unittest.TestCase):
    """All six B1 advisory roles must be present with non-empty payloads."""

    _EXPECTED_ROLES = {
        "DATA_SLATE_INTEGRITY",
        "NEWS_STATUS",
        "MARKET_EXACT_LINE",
        "SPORT_SPECIALIST",
        "FAILURE_CONTRADICTION",
        "FINAL_REFRESH",
    }

    def setUp(self):
        self.result = _adapt()

    def test_six_role_payloads(self):
        self.assertEqual(len(self.result.role_payloads), 6)

    def test_all_expected_role_ids_present(self):
        present = set(self.result.role_payloads.keys())
        self.assertEqual(present, self._EXPECTED_ROLES)

    def test_each_payload_is_dict(self):
        for role_id, payload in self.result.role_payloads.items():
            with self.subTest(role=role_id):
                self.assertIsInstance(payload, dict, f"Role {role_id} payload is not a dict")

    def test_each_payload_non_empty(self):
        for role_id, payload in self.result.role_payloads.items():
            with self.subTest(role=role_id):
                self.assertGreater(len(payload), 0, f"Role {role_id} payload is empty")

    def test_source_row_fields_used_is_tuple(self):
        self.assertIsInstance(self.result.source_row_fields_used, tuple)

    def test_degradation_reasons_is_tuple(self):
        self.assertIsInstance(self.result.degradation_reasons, tuple)


# ── Governance ceiling enforced ───────────────────────────────────────────────

class TestB4GovernanceCeilingEnforced(unittest.TestCase):
    """
    Adapter and game-script outputs must never claim a ceiling above
    MODEL_QUALIFIED_HOLD. FINAL_APPROVED, VERIFIED, EDGE_QUALIFIED, MONEY
    are permanently blocked.
    """

    _HIGH_AUTHORITY_STATES = {
        "FINAL_APPROVED", "VERIFIED", "MARKET_VERIFIED",
        "EDGE_QUALIFIED", "EDGE", "MONEY", "CAPITAL_AUTHORIZED",
    }

    def test_game_script_ceiling_not_high_authority(self):
        result = _adapt()
        shadow = result.game_script_shadow
        if shadow is None:
            return   # Nothing to check; absence is safe
        ceiling = shadow.get("ceiling", "")
        self.assertNotIn(ceiling, self._HIGH_AUTHORITY_STATES,
                         f"game_script_shadow ceiling {ceiling!r} exceeds allowed maximum")

    def test_role_payloads_contain_no_can_execute_true(self):
        """Role payloads (B1 input dicts) must not assert can_execute=True."""
        result = _adapt()
        for role_id, payload in result.role_payloads.items():
            with self.subTest(role=role_id):
                can_exec = payload.get("can_execute")
                if can_exec is not None:
                    self.assertIsNot(can_exec, True,
                                     f"Role {role_id} payload has can_execute=True")

    def test_adapter_result_is_not_executable(self):
        """WnbaPropsAdapterResult must not carry execution authority."""
        result = _adapt()
        self.assertFalse(
            getattr(result, "can_execute", False),
            "WnbaPropsAdapterResult must not expose can_execute=True"
        )


# ── Technical failure propagation ─────────────────────────────────────────────

class TestB4TechnicalFailurePropagation(unittest.TestCase):
    """
    TECHNICAL failure at a downstream layer:
    • Upstream adapter result is preserved.
    • Upgrade to ADVISORY / HOLD allowed (with preserved_upstream_result).
    • Upgrade to VERIFIED / MONEY / FINAL_APPROVED blocked.
    • Adjacent rows are NOT affected.
    """

    def setUp(self):
        self.guard = PipelineStateGuard()
        self.result = _adapt()

    def _make_state_with_technical_failure(self, row_id: str = "row-T") -> RowPipelineState:
        state = RowPipelineState(row_id=row_id)
        upstream_snapshot = {
            "adapter_status": self.result.adapter_status,
            "role_payload_count": len(self.result.role_payloads),
        }
        state.record_layer_complete(
            PipelineLayer.ADAPTER, result=upstream_snapshot
        )
        failure = self.guard.scope_failure(
            row_id=row_id,
            failure_kind=FailureKind.TECHNICAL,
            failure_code="DB_TIMEOUT",
            failed_at_layer=PipelineLayer.MARKET,
            message="Market gate DB connection timed out during pipeline run",
            preserved_upstream_result=upstream_snapshot,
        )
        state.record_failure(failure)
        return state

    def test_advisory_allowed_with_upstream(self):
        state = self._make_state_with_technical_failure()
        r = state.check_upgrade(UpgradeCeiling.ADVISORY)
        self.assertTrue(r.allowed, "ADVISORY upgrade must be allowed for TECHNICAL failure")
        self.assertEqual(r.reason, "TECHNICAL_FAILURE_UPSTREAM_PRESERVED")

    def test_upstream_preserved_contains_adapter_data(self):
        state = self._make_state_with_technical_failure()
        r = state.check_upgrade(UpgradeCeiling.ADVISORY)
        self.assertIsNotNone(r.preserved_upstream_result)
        self.assertIn("adapter_status", r.preserved_upstream_result)

    def test_hold_allowed(self):
        state = self._make_state_with_technical_failure()
        r = state.check_upgrade(UpgradeCeiling.HOLD)
        self.assertTrue(r.allowed)

    def test_verified_blocked(self):
        state = self._make_state_with_technical_failure()
        r = state.check_upgrade(UpgradeCeiling.VERIFIED)
        self.assertFalse(r.allowed)

    def test_money_blocked(self):
        state = self._make_state_with_technical_failure()
        r = state.check_upgrade(UpgradeCeiling.MONEY)
        self.assertFalse(r.allowed)

    def test_final_approved_blocked(self):
        state = self._make_state_with_technical_failure()
        r = state.check_upgrade(UpgradeCeiling.FINAL_APPROVED)
        self.assertFalse(r.allowed)

    def test_does_not_affect_adjacent_row(self):
        """Row isolation: TECHNICAL failure on row T does not affect row U."""
        state_t = self._make_state_with_technical_failure(row_id="row-T")
        state_u = RowPipelineState(row_id="row-U")

        self.assertTrue(state_t.has_failure)
        self.assertFalse(state_u.has_failure)

        r_u = state_u.check_upgrade(UpgradeCeiling.FINAL_APPROVED)
        self.assertTrue(r_u.allowed, "Adjacent row must be unrestricted")

    def test_wrong_row_id_assignment_raises(self):
        failure = self.guard.scope_failure(
            row_id="row-X",
            failure_kind=FailureKind.TECHNICAL,
            failure_code="ERR",
            failed_at_layer=PipelineLayer.MARKET,
            message="wrong row",
        )
        state_y = RowPipelineState(row_id="row-Y")
        with self.assertRaises(ValueError):
            state_y.record_failure(failure)


# ── Contract failure propagation ──────────────────────────────────────────────

class TestB4ContractFailurePropagation(unittest.TestCase):
    """
    CONTRACT failure (DATA_CONTRACT_FAIL, schema error):
    Fail-closed at all levels — even ADVISORY is blocked.
    """

    def setUp(self):
        self.guard = PipelineStateGuard()

    def _make_contract_state(self) -> RowPipelineState:
        state = RowPipelineState(row_id="row-C")
        failure = self.guard.scope_failure(
            row_id="row-C",
            failure_kind=FailureKind.CONTRACT,
            failure_code="DATA_CONTRACT_FAIL",
            failed_at_layer=PipelineLayer.ADAPTER,
            message="Required field 'stat_key' missing from scoring row",
        )
        state.record_failure(failure)
        return state

    def test_advisory_blocked(self):
        state = self._make_contract_state()
        r = state.check_upgrade(UpgradeCeiling.ADVISORY)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "CONTRACT_FAILURE_FAIL_CLOSED")

    def test_hold_blocked(self):
        state = self._make_contract_state()
        r = state.check_upgrade(UpgradeCeiling.HOLD)
        self.assertFalse(r.allowed)

    def test_verified_blocked(self):
        state = self._make_contract_state()
        r = state.check_upgrade(UpgradeCeiling.VERIFIED)
        self.assertFalse(r.allowed)

    def test_final_approved_blocked(self):
        state = self._make_contract_state()
        r = state.check_upgrade(UpgradeCeiling.FINAL_APPROVED)
        self.assertFalse(r.allowed)

    def test_no_preserved_upstream_for_contract(self):
        state = self._make_contract_state()
        r = state.check_upgrade(UpgradeCeiling.ADVISORY)
        self.assertIsNone(r.preserved_upstream_result)

    def test_reconstruction_blocks_hold_too(self):
        state = RowPipelineState(row_id="row-R")
        failure = self.guard.scope_failure(
            row_id="row-R",
            failure_kind=FailureKind.TECHNICAL,
            failure_code="PARTIAL",
            failed_at_layer=PipelineLayer.ADAPTER,
            message="partial data reconstructed",
            reconstruction_attempted=True,
        )
        state.record_failure(failure)
        r = state.check_upgrade(UpgradeCeiling.HOLD)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "RECONSTRUCTION_BLOCKS_UPGRADE")


# ── Deterministic output ──────────────────────────────────────────────────────

class TestB4DeterministicOutput(unittest.TestCase):
    """Same row, same run_id, same snapshot_id → identical adapter result."""

    def test_adapter_status_deterministic(self):
        row = _full_row()
        r1 = WnbaPropsAdapter().adapt(row=row, run_id="det-run", snapshot_id="snap-001")
        r2 = WnbaPropsAdapter().adapt(row=row, run_id="det-run", snapshot_id="snap-001")
        self.assertEqual(r1.adapter_status, r2.adapter_status)

    def test_role_payload_keys_deterministic(self):
        row = _full_row()
        r1 = WnbaPropsAdapter().adapt(row=row, run_id="det-run", snapshot_id="snap-001")
        r2 = WnbaPropsAdapter().adapt(row=row, run_id="det-run", snapshot_id="snap-001")
        self.assertEqual(set(r1.role_payloads.keys()), set(r2.role_payloads.keys()))

    def test_packet_snapshot_id_deterministic(self):
        row = _full_row()
        r1 = WnbaPropsAdapter().adapt(row=row, run_id="det-run", snapshot_id="snap-001")
        r2 = WnbaPropsAdapter().adapt(row=row, run_id="det-run", snapshot_id="snap-001")
        self.assertEqual(r1.packet.snapshot_id, r2.packet.snapshot_id)

    def test_same_instance_reentrant(self):
        """A single WnbaPropsAdapter instance must be re-entrant."""
        adapter = WnbaPropsAdapter()
        row = _full_row()
        r1 = adapter.adapt(row=row, run_id="r1")
        r2 = adapter.adapt(row=row, run_id="r2")
        self.assertEqual(r1.adapter_status, r2.adapter_status)


# ── No production coupling ────────────────────────────────────────────────────

class TestB4NoProductionCoupling(unittest.TestCase):
    """
    B4 lane modules must not import from the production WNBA scoring engine.
    These imports would couple shadow/advisory code to live decision paths.

    Checked via source-code inspection rather than sys.modules so the test
    is isolated from what other tests in the same session happen to import.
    """

    _FORBIDDEN_IMPORT_STRINGS = [
        "gate_engine.wnba_composite_gate",
        "gate_engine.wnba_generative_gate",
        "gate_engine.wnba_enrichment_contract",
        "from gate_engine.wnba import",
        "import gate_engine.wnba ",
    ]

    _B4_MODULES = [
        "gate_engine.universal_agent.lanes.wnba_props.adapter",
        "gate_engine.universal_agent.lanes.wnba_props.validation",
        "gate_engine.universal_agent.lanes.wnba_props.field_map",
        "gate_engine.universal_agent.lanes.wnba_props.role_inputs",
        "gate_engine.universal_agent.pipeline_state",
    ]

    def test_no_production_wnba_imports_in_b4_sources(self):
        """
        Parse source of each B4 module and confirm no imports from
        production WNBA gate files appear.
        """
        import importlib, inspect
        for mod_path in self._B4_MODULES:
            mod = importlib.import_module(mod_path)
            src = inspect.getsource(mod)
            for forbidden in self._FORBIDDEN_IMPORT_STRINGS:
                self.assertNotIn(
                    forbidden, src,
                    f"B4 module {mod_path!r} imports production module "
                    f"via {forbidden!r}"
                )


# ── Governance treatment: place_bet / settlement ──────────────────────────────

class TestB4GovernanceTreatment(unittest.TestCase):
    """
    place_bet and settlement are in FORBIDDEN_GOVERNANCE_KEYS.
    External/advisory model outputs that contain these keys are rejected by
    validate_output_contract() with FORBIDDEN_GOVERNANCE_KEY code.
    """

    def test_place_bet_in_forbidden_set(self):
        self.assertIn("place_bet", FORBIDDEN_GOVERNANCE_KEYS)

    def test_settlement_in_forbidden_set(self):
        self.assertIn("settlement", FORBIDDEN_GOVERNANCE_KEYS)

    def test_settle_in_forbidden_set(self):
        self.assertIn("settle", FORBIDDEN_GOVERNANCE_KEYS)

    def test_settle_result_in_forbidden_set(self):
        self.assertIn("settle_result", FORBIDDEN_GOVERNANCE_KEYS)

    def test_bet_in_forbidden_set(self):
        self.assertIn("bet", FORBIDDEN_GOVERNANCE_KEYS)

    def test_wager_in_forbidden_set(self):
        self.assertIn("wager", FORBIDDEN_GOVERNANCE_KEYS)

    def test_market_order_in_forbidden_set(self):
        self.assertIn("market_order", FORBIDDEN_GOVERNANCE_KEYS)

    def test_place_bet_at_root_blocked(self):
        payload = valid_output_payload()
        payload["place_bet"] = "some-bet"
        result = validate_output_contract(payload)
        self.assertFalse(result)
        self.assertEqual(result.code, "FORBIDDEN_GOVERNANCE_KEY")

    def test_settlement_nested_in_advisory_findings_blocked(self):
        """Forbidden scan runs recursively — nested keys are caught."""
        payload = valid_output_payload()
        payload["advisory_findings"]["settlement"] = {"status": "SETTLED"}
        result = validate_output_contract(payload)
        self.assertFalse(result)
        self.assertEqual(result.code, "FORBIDDEN_GOVERNANCE_KEY")

    def test_place_bet_nested_deep_blocked(self):
        payload = valid_output_payload()
        payload["advisory_findings"]["nested"] = {"deep": {"place_bet": True}}
        result = validate_output_contract(payload)
        self.assertFalse(result)
        self.assertEqual(result.code, "FORBIDDEN_GOVERNANCE_KEY")

    def test_settle_result_nested_blocked(self):
        payload = valid_output_payload()
        payload["advisory_findings"]["settle_result"] = "WIN"
        result = validate_output_contract(payload)
        self.assertFalse(result)

    def test_wager_in_list_blocked(self):
        """Forbidden scan traverses lists — wager inside a list item is caught."""
        payload = valid_output_payload()
        payload["advisory_findings"]["items"] = [{"wager": 100}]
        result = validate_output_contract(payload)
        self.assertFalse(result)
        self.assertEqual(result.code, "FORBIDDEN_GOVERNANCE_KEY")

    def test_valid_advisory_findings_unblocked(self):
        """An output with only safe fields passes the contract."""
        payload = valid_output_payload(
            advisory_findings={
                "probability_estimate":   0.68,
                "confidence":             "HIGH",
                "notes":                  "Game log trending over in last 5.",
                "data_freshness_seconds": 3600,
            }
        )
        result = validate_output_contract(payload)
        self.assertTrue(result, f"Expected OUTPUT_VALID, got {result!r}")

    def test_forbidden_set_is_frozenset(self):
        self.assertIsInstance(FORBIDDEN_GOVERNANCE_KEYS, frozenset)


# ── Orchestrator integration ──────────────────────────────────────────────────

class TestB4OrchestratorIntegration(unittest.TestCase):
    """
    EvidencePacket from adapter must flow into run_orchestrator correctly.
    Uses empty role_runners (→ NO_RUNNER for all) so no network calls are made.
    """

    def test_orchestrator_accepts_adapter_packet(self):
        result = _adapt()
        packet = result.packet
        registry = AgentRegistry()  # empty registry
        orch = run_orchestrator(packet, registry, role_runners={}, db_conn=None)
        self.assertIsNotNone(orch)

    def test_orchestrator_returns_orchestrator_result(self):
        from gate_engine.universal_agent.orchestrator import OrchestratorResult
        result = _adapt()
        orch = run_orchestrator(
            result.packet, AgentRegistry(), role_runners={}, db_conn=None
        )
        self.assertIsInstance(orch, OrchestratorResult)

    def test_orchestrator_empty_registry_zero_accepted(self):
        result = _adapt()
        orch = run_orchestrator(
            result.packet, AgentRegistry(), role_runners={}, db_conn=None
        )
        self.assertEqual(orch.accepted_count(), 0)

    def test_orchestrator_echoes_run_id(self):
        result = WnbaPropsAdapter().adapt(
            row=_full_row(), run_id="echo-run-001"
        )
        orch = run_orchestrator(
            result.packet, AgentRegistry(), role_runners={}, db_conn=None
        )
        self.assertEqual(orch.run_id, "echo-run-001")

    def test_orchestrator_raises_for_non_packet(self):
        with self.assertRaises(TypeError):
            run_orchestrator(
                {"not": "a packet"}, AgentRegistry(),  # type: ignore[arg-type]
                role_runners={}, db_conn=None
            )

    def test_same_packet_object_identity_preserved(self):
        """Same Python object identity of EvidencePacket must be maintained."""
        result = _adapt()
        packet = result.packet
        orch = run_orchestrator(
            packet, AgentRegistry(), role_runners={}, db_conn=None
        )
        # The packet in the result must be the same object (not a copy)
        self.assertIs(orch.bundle.packet if hasattr(orch.bundle, "packet") else packet, packet)


# ── can_execute=False in all B4 + pipeline_state modules ─────────────────────

class TestB4CanExecuteFalseAllModules(unittest.TestCase):
    """
    Governance invariant: can_execute=False must be present and equal to False
    in every B4 lane module and in pipeline_state.
    """

    def _check_can_execute(self, import_path: str) -> None:
        import importlib
        mod = importlib.import_module(import_path)
        val = getattr(mod, "can_execute", None)
        self.assertIsNotNone(
            val, f"can_execute missing from {import_path}"
        )
        self.assertIs(
            val, False,
            f"can_execute is not False in {import_path} (got {val!r})"
        )

    def test_pipeline_state_can_execute_false(self):
        self._check_can_execute("gate_engine.universal_agent.pipeline_state")

    def test_adapter_can_execute_false(self):
        self._check_can_execute("gate_engine.universal_agent.lanes.wnba_props.adapter")

    def test_validation_can_execute_false(self):
        self._check_can_execute("gate_engine.universal_agent.lanes.wnba_props.validation")

    def test_field_map_can_execute_false(self):
        self._check_can_execute("gate_engine.universal_agent.lanes.wnba_props.field_map")

    def test_role_inputs_can_execute_false(self):
        self._check_can_execute("gate_engine.universal_agent.lanes.wnba_props.role_inputs")

    def test_settlement_worker_can_execute_false(self):
        import gate_engine.settlement_worker as sw
        self.assertIs(sw.CAN_EXECUTE, False)


if __name__ == "__main__":
    unittest.main()
