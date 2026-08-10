"""
tests/test_universal_agent_b0.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Unit tests for all B0 components (no database required).

Test categories:
  T1  Evidence Packet — valid construction, immutability, type/field errors
  T2  Output Contract — valid accept, forbidden keys (recursive depths),
        extra fields, missing required, wrong types, advisory_only enforcement
  T3  Agent Registry — registration, advisory_only invariant, unregistered denial,
        lane accepts arbitrary strings, resolve_model() module-attr pattern
  T4  Handoff Contract — valid construction, forbidden authority, field errors
  T5  Capability Boundary — deny-by-default, per-agent allowlist, pre/post hooks,
        recursive forbidden key in inputs/outputs, from_registry_entries()
  T6  Budget Guard — configurable pricing, allowed/blocked threshold, cost math
  T7  Cross-module — validate_output_contract applied to build_test_packet output dict,
        ensure test helpers share real validation (Weather Step 14D lesson)
"""
from __future__ import annotations

import types
import unittest

from gate_engine.universal_agent.evidence_packet import (
    Lane,
    EvidencePacket,
    build_evidence_packet,
    build_test_packet,
)
from gate_engine.universal_agent.agent_registry import (
    AgentRole,
    BudgetConfig,
    AgentRegistryEntry,
    AgentRegistry,
)
from gate_engine.universal_agent.handoff_contract import (
    AuthorityRequest,
    NextAction,
    HandoffContract,
    build_handoff_contract,
)
from gate_engine.universal_agent.output_contract import (
    OUTPUT_VALID,
    OutputContractViolation,
    OutputViolationCode,
    FORBIDDEN_GOVERNANCE_KEYS,
    validate_output_contract,
    valid_output_payload,
)
from gate_engine.universal_agent.capability_boundary import (
    HookStatus,
    PreHookResult,
    PostHookResult,
    UniversalCapabilityBoundary,
)
from gate_engine.universal_agent.audit_store import (
    UsageStatus,
    compute_budget_guard,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entry(
    agent_id: str = "agent-x",
    role: str = AgentRole.FORECAST_CONTEXT,
    lane: str = Lane.PLAYER_PROPS,
    caps: list | None = None,
) -> AgentRegistryEntry:
    return AgentRegistryEntry(
        agent_id=agent_id,
        role=role,
        lane=lane,
        allowed_capabilities=caps or ["tool_alpha"],
        input_schema_ref="input_schema_ref",
        output_schema_ref="output_schema_ref",
    )


def _boundary_for(agents: dict[str, list[str]]) -> UniversalCapabilityBoundary:
    return UniversalCapabilityBoundary({k: set(v) for k, v in agents.items()})


# ═══════════════════════════════════════════════════════════════════════════════
# T1 — Evidence Packet
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidencePacket(unittest.TestCase):

    def test_build_valid_packet(self):
        p = build_evidence_packet(
            run_id="run-001",
            canonical_event_id="evt-001",
            lane=Lane.PLAYER_PROPS,
        )
        self.assertEqual(p.run_id, "run-001")
        self.assertEqual(p.lane, Lane.PLAYER_PROPS)
        self.assertIsInstance(p.snapshot_id, str)
        self.assertTrue(len(p.snapshot_id) > 0)
        self.assertIsInstance(p.created_at, str)

    def test_packet_is_immutable(self):
        p = build_test_packet()
        with self.assertRaises((AttributeError, TypeError)):
            p.run_id = "mutated"  # type: ignore[misc]

    def test_source_failures_become_tuple(self):
        p = build_evidence_packet(
            run_id="r", canonical_event_id="e", lane=Lane.TENNIS,
            source_failures=[{"source": "espn", "reason": "timeout"}],
        )
        self.assertIsInstance(p.source_failures, tuple)
        self.assertEqual(p.source_failures[0]["source"], "espn")

    def test_source_conflicts_become_tuple(self):
        p = build_evidence_packet(
            run_id="r", canonical_event_id="e", lane=Lane.TENNIS,
            source_conflicts=[{"field": "line", "sources": ["a", "b"]}],
        )
        self.assertIsInstance(p.source_conflicts, tuple)

    def test_empty_run_id_rejected(self):
        with self.assertRaises(ValueError):
            build_evidence_packet(run_id="", canonical_event_id="e", lane=Lane.TENNIS)

    def test_whitespace_only_run_id_rejected(self):
        with self.assertRaises(ValueError):
            build_evidence_packet(run_id="   ", canonical_event_id="e", lane=Lane.TENNIS)

    def test_empty_lane_rejected(self):
        with self.assertRaises(ValueError):
            build_evidence_packet(run_id="r", canonical_event_id="e", lane="")

    def test_arbitrary_lane_string_accepted(self):
        """New lanes can be arbitrary strings without code changes."""
        p = build_evidence_packet(
            run_id="r", canonical_event_id="e", lane="NFL_TOTALS"
        )
        self.assertEqual(p.lane, "NFL_TOTALS")

    def test_snapshot_id_autogenerated(self):
        p1 = build_test_packet()
        p2 = build_test_packet()
        self.assertNotEqual(p1.snapshot_id, p2.snapshot_id)

    def test_explicit_snapshot_id_preserved(self):
        p = build_evidence_packet(
            run_id="r", canonical_event_id="e", lane=Lane.TENNIS,
            snapshot_id="fixed-snap-id",
        )
        self.assertEqual(p.snapshot_id, "fixed-snap-id")

    def test_to_dict_round_trip(self):
        p = build_test_packet()
        d = p.to_dict()
        self.assertEqual(d["run_id"], p.run_id)
        self.assertEqual(d["lane"], p.lane)
        self.assertIsInstance(d["source_failures"], list)

    def test_lane_known_constants(self):
        known = Lane.known()
        for val in [Lane.KALSHI_WEATHER, Lane.MLB_MONEYLINE, Lane.WNBA_PROPS,
                    Lane.TENNIS, Lane.PLAYER_PROPS, Lane.UNKNOWN]:
            self.assertIn(val, known)

    def test_build_test_packet_uses_real_evidence_packet_type(self):
        """
        build_test_packet() must return a real EvidencePacket, not a stub.
        Weather Step 14D lesson: test helpers must share real validation.
        """
        p = build_test_packet()
        self.assertIsInstance(p, EvidencePacket)


# ═══════════════════════════════════════════════════════════════════════════════
# T2 — Output Contract
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutputContract(unittest.TestCase):

    def _valid(self, **overrides) -> dict:
        return valid_output_payload(**overrides)

    # ── Accept valid ──────────────────────────────────────────────────────────

    def test_valid_minimal_payload_accepted(self):
        result = validate_output_contract(self._valid())
        self.assertIs(result, OUTPUT_VALID)
        self.assertTrue(bool(result))

    def test_valid_payload_with_optional_fields(self):
        result = validate_output_contract(self._valid(
            input_tokens=500,
            output_tokens=200,
            estimated_cost_usd=0.001,
            latency_ms=1250,
            confidence_note="High confidence",
            model_id="claude-haiku-4-5",
        ))
        self.assertIs(result, OUTPUT_VALID)

    def test_output_valid_singleton_is_truthy(self):
        self.assertTrue(OUTPUT_VALID)

    # ── Reject: not a dict ────────────────────────────────────────────────────

    def test_non_dict_rejected(self):
        result = validate_output_contract("not a dict")
        self.assertIsInstance(result, OutputContractViolation)
        self.assertEqual(result.code, OutputViolationCode.NOT_A_DICT)

    def test_none_rejected(self):
        result = validate_output_contract(None)
        self.assertIsInstance(result, OutputContractViolation)
        self.assertEqual(result.code, OutputViolationCode.NOT_A_DICT)

    # ── Reject: forbidden governance keys ─────────────────────────────────────

    def test_terminal_label_at_root_rejected(self):
        payload = self._valid()
        payload["terminal_label"] = "SOME_LABEL"
        result = validate_output_contract(payload)
        self.assertIsInstance(result, OutputContractViolation)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_can_execute_at_root_rejected(self):
        payload = self._valid()
        payload["can_execute"] = False
        result = validate_output_contract(payload)
        self.assertIsInstance(result, OutputContractViolation)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_final_decision_at_root_rejected(self):
        payload = self._valid()
        payload["final_decision"] = "HOLD"
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_stake_tier_at_root_rejected(self):
        payload = self._valid()
        payload["stake_tier"] = "STANDARD"
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_nested_one_level_rejected(self):
        """Recursive scan must catch governance keys inside advisory_findings."""
        payload = self._valid(advisory_findings={"terminal_label": "WATCH"})
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)
        self.assertIn("terminal_label", result.path)

    def test_governance_key_nested_two_levels_rejected(self):
        """Recursive scan must descend two levels."""
        payload = self._valid(advisory_findings={
            "market": {"can_execute": True}
        })
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_nested_three_levels_rejected(self):
        """Recursive scan must descend three or more levels."""
        payload = self._valid(advisory_findings={
            "outer": {"middle": {"final_decision": "PLAY"}}
        })
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_governance_key_inside_list_rejected(self):
        """Recursive scan must descend into lists."""
        payload = self._valid(data_gaps=[
            {"description": "missing line"},
            {"is_playable": True},
        ])
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_forbidden_scan_fires_before_extra_key_check(self):
        """
        A payload with both a forbidden governance key AND an unknown extra field
        must report FORBIDDEN_GOVERNANCE_KEY, not EXTRA_FIELD.
        Scan order: forbidden → allowlist. (Weather kalshi_wx_shadow_schema pattern.)
        """
        payload = self._valid()
        payload["unknown_extra"] = "x"
        payload["can_execute"] = True
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_capital_allocation_rejected(self):
        payload = self._valid()
        payload["capital_allocation"] = 100
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_production_authority_rejected(self):
        payload = self._valid()
        payload["production_authority"] = False
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    # ── Reject: extra fields ──────────────────────────────────────────────────

    def test_unknown_root_field_rejected(self):
        payload = self._valid()
        payload["unexpected_field"] = "value"
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.EXTRA_FIELD)
        self.assertIn("unexpected_field", result.message)

    # ── Reject: missing required fields ──────────────────────────────────────

    def test_missing_agent_id_rejected(self):
        payload = self._valid()
        del payload["agent_id"]
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_advisory_findings_rejected(self):
        payload = self._valid()
        del payload["advisory_findings"]
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_run_id_rejected(self):
        payload = self._valid()
        del payload["run_id"]
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_snapshot_id_rejected(self):
        payload = self._valid()
        del payload["snapshot_id"]
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_missing_lane_rejected(self):
        payload = self._valid()
        del payload["lane"]
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.MISSING_REQUIRED_FIELD)

    # ── Reject: wrong types ───────────────────────────────────────────────────

    def test_advisory_findings_must_be_dict(self):
        payload = self._valid(advisory_findings="not a dict")
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.WRONG_TYPE)

    def test_input_tokens_must_be_int(self):
        payload = self._valid(input_tokens=1.5)
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.WRONG_TYPE)

    def test_estimated_cost_usd_must_be_number(self):
        payload = self._valid(estimated_cost_usd="cheap")
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.WRONG_TYPE)

    # ── advisory_only enforcement ─────────────────────────────────────────────

    def test_advisory_only_false_rejected(self):
        payload = self._valid()
        payload["advisory_only"] = False
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.ADVISORY_ONLY_NOT_TRUE)

    def test_advisory_only_string_true_rejected(self):
        """Must be exactly bool True, not the string 'True'."""
        payload = self._valid()
        payload["advisory_only"] = "True"
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.ADVISORY_ONLY_NOT_TRUE)

    def test_advisory_only_int_one_rejected(self):
        """Must be exactly bool True, not int 1."""
        payload = self._valid()
        payload["advisory_only"] = 1
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.ADVISORY_ONLY_NOT_TRUE)

    def test_violation_is_falsy(self):
        payload = self._valid()
        del payload["agent_id"]
        result = validate_output_contract(payload)
        self.assertFalse(bool(result))

    def test_forbidden_governance_keys_set_is_nonempty(self):
        self.assertGreater(len(FORBIDDEN_GOVERNANCE_KEYS), 10)
        self.assertIn("terminal_label", FORBIDDEN_GOVERNANCE_KEYS)
        self.assertIn("can_execute", FORBIDDEN_GOVERNANCE_KEYS)
        self.assertIn("final_decision", FORBIDDEN_GOVERNANCE_KEYS)


# ═══════════════════════════════════════════════════════════════════════════════
# T3 — Agent Registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentRegistry(unittest.TestCase):

    def setUp(self):
        self.registry = AgentRegistry()

    def test_register_and_get(self):
        entry = _make_entry("agent-001")
        self.registry.register(entry)
        got = self.registry.get("agent-001")
        self.assertEqual(got.agent_id, "agent-001")

    def test_unregistered_get_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.registry.get("does-not-exist")

    def test_is_registered_true_after_register(self):
        entry = _make_entry("agent-002")
        self.registry.register(entry)
        self.assertTrue(self.registry.is_registered("agent-002"))

    def test_is_registered_false_for_unknown(self):
        self.assertFalse(self.registry.is_registered("ghost"))

    def test_duplicate_register_raises(self):
        entry = _make_entry("agent-dup")
        self.registry.register(entry)
        with self.assertRaises(KeyError):
            self.registry.register(_make_entry("agent-dup"))

    def test_unregister_removes_agent(self):
        entry = _make_entry("agent-rem")
        self.registry.register(entry)
        self.registry.unregister("agent-rem")
        self.assertFalse(self.registry.is_registered("agent-rem"))

    def test_advisory_only_is_always_true(self):
        entry = _make_entry("agent-adv")
        self.assertIs(entry.advisory_only, True)

    def test_advisory_only_cannot_be_set_to_false(self):
        entry = _make_entry("agent-block")
        with self.assertRaises(AttributeError):
            entry.advisory_only = False  # type: ignore[misc]

    def test_advisory_only_cannot_be_set_to_true_either(self):
        """Even setting to True is blocked — the property has no setter."""
        entry = _make_entry("agent-block2")
        with self.assertRaises(AttributeError):
            entry.advisory_only = True  # type: ignore[misc]

    def test_advisory_only_not_a_constructor_parameter(self):
        """advisory_only is not accepted as a constructor kwarg."""
        with self.assertRaises(TypeError):
            AgentRegistryEntry(  # type: ignore[call-arg]
                agent_id="x",
                role="r",
                lane="l",
                allowed_capabilities=[],
                input_schema_ref="in",
                output_schema_ref="out",
                advisory_only=False,
            )

    def test_lane_accepts_arbitrary_string(self):
        entry = _make_entry(lane="NHL_FUTURES_NEW_LANE")
        self.assertEqual(entry.lane, "NHL_FUTURES_NEW_LANE")

    def test_role_accepts_arbitrary_string(self):
        entry = _make_entry(role="CUSTOM_FUTURE_ROLE")
        self.assertEqual(entry.role, "CUSTOM_FUTURE_ROLE")

    def test_agents_for_lane_filters_correctly(self):
        self.registry.register(_make_entry("a1", lane=Lane.WNBA_PROPS))
        self.registry.register(_make_entry("a2", lane=Lane.TENNIS))
        self.registry.register(_make_entry("a3", lane=Lane.WNBA_PROPS))
        wnba = self.registry.agents_for_lane(Lane.WNBA_PROPS)
        self.assertEqual({e.agent_id for e in wnba}, {"a1", "a3"})

    def test_agents_for_role_filters_correctly(self):
        self.registry.register(_make_entry("r1", role=AgentRole.FORECAST_CONTEXT))
        self.registry.register(_make_entry("r2", role=AgentRole.SOURCE_RECONCILIATION))
        fc = self.registry.agents_for_role(AgentRole.FORECAST_CONTEXT)
        self.assertEqual([e.agent_id for e in fc], ["r1"])

    def test_all_agents_returns_all(self):
        self.registry.register(_make_entry("all1"))
        self.registry.register(_make_entry("all2"))
        self.assertEqual(len(self.registry.all_agents()), 2)

    def test_registry_len(self):
        self.registry.register(_make_entry("len1"))
        self.assertEqual(len(self.registry), 1)

    def test_resolve_model_returns_none_when_no_module(self):
        entry = _make_entry("no-mod")
        self.assertIsNone(entry.resolve_model())

    def test_resolve_model_reads_module_attribute_at_call_time(self):
        """
        resolve_model() reads model_module._MODEL at call time,
        not at registration time. Weather Step 14D fix 1 pattern:
        future model string changes propagate without code changes.
        """
        mock_mod = types.ModuleType("mock_model_module")
        mock_mod._MODEL = "claude-haiku-4-5-20251001"
        entry = AgentRegistryEntry(
            agent_id="model-agent",
            role=AgentRole.FORECAST_CONTEXT,
            lane=Lane.PLAYER_PROPS,
            allowed_capabilities=["emit_findings"],
            input_schema_ref="in",
            output_schema_ref="out",
            model_module=mock_mod,
            model_attr="_MODEL",
        )
        self.assertEqual(entry.resolve_model(), "claude-haiku-4-5-20251001")

        # Simulate a model update — resolve_model() should return the new value
        mock_mod._MODEL = "claude-opus-4-20260101"
        self.assertEqual(entry.resolve_model(), "claude-opus-4-20260101")

    def test_allowed_capabilities_defensive_copy(self):
        """allowed_capabilities property returns a copy, not the internal list."""
        caps = ["tool_a", "tool_b"]
        entry = _make_entry(caps=caps)
        returned = entry.allowed_capabilities
        returned.append("injected_tool")
        self.assertNotIn("injected_tool", entry.allowed_capabilities)

    def test_budget_config_defaults(self):
        entry = _make_entry()
        self.assertEqual(entry.budget.max_input_tokens, 8_000)
        self.assertEqual(entry.budget.timeout_s, 90)

    def test_budget_config_custom(self):
        budget = BudgetConfig(max_input_tokens=2000, max_cost_usd=0.05)
        entry = AgentRegistryEntry(
            agent_id="budget-agent",
            role=AgentRole.FORECAST_CONTEXT,
            lane=Lane.PLAYER_PROPS,
            allowed_capabilities=[],
            input_schema_ref="in",
            output_schema_ref="out",
            budget=budget,
        )
        self.assertEqual(entry.budget.max_input_tokens, 2000)
        self.assertEqual(entry.budget.max_cost_usd, 0.05)

    def test_budget_config_is_immutable(self):
        bc = BudgetConfig()
        with self.assertRaises(AttributeError):
            bc.max_cost_usd = 999.0  # type: ignore[misc]

    def test_empty_agent_id_rejected(self):
        with self.assertRaises(ValueError):
            AgentRegistryEntry(
                agent_id="",
                role="r", lane="l",
                allowed_capabilities=[],
                input_schema_ref="in", output_schema_ref="out",
            )

    def test_empty_role_rejected(self):
        with self.assertRaises(ValueError):
            AgentRegistryEntry(
                agent_id="x",
                role="",
                lane="l",
                allowed_capabilities=[],
                input_schema_ref="in", output_schema_ref="out",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# T4 — Handoff Contract
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandoffContract(unittest.TestCase):

    def _valid_handoff(self, **overrides) -> dict:
        base = dict(
            current_gate="gate-1",
            sender="agent-a",
            recipient="agent-b",
            claim="Market is mispriced by 3%",
            requested_ruling="CONFIRM_OR_REJECT",
            authority_requested=AuthorityRequest.NONE,
            next_allowed_action=NextAction.CONTINUE_PIPELINE,
        )
        base.update(overrides)
        return base

    def test_build_valid_handoff(self):
        h = build_handoff_contract(**self._valid_handoff())
        self.assertEqual(h.sender, "agent-a")
        self.assertIsInstance(h.tests_or_evidence_produced, tuple)
        self.assertIsInstance(h.known_failures, tuple)

    def test_lists_become_tuples(self):
        h = build_handoff_contract(
            **self._valid_handoff(
                tests_or_evidence_produced=["test_a", "test_b"],
                known_failures=["known_issue_1"],
            )
        )
        self.assertIsInstance(h.tests_or_evidence_produced, tuple)
        self.assertIsInstance(h.known_failures, tuple)

    def test_handoff_is_immutable(self):
        h = build_handoff_contract(**self._valid_handoff())
        with self.assertRaises((AttributeError, TypeError)):
            h.sender = "modified"  # type: ignore[misc]

    def test_execute_authority_rejected(self):
        with self.assertRaises(ValueError):
            build_handoff_contract(
                **self._valid_handoff(authority_requested="EXECUTE")
            )

    def test_trade_authority_rejected(self):
        with self.assertRaises(ValueError):
            build_handoff_contract(
                **self._valid_handoff(authority_requested="TRADE")
            )

    def test_capital_authority_rejected(self):
        with self.assertRaises(ValueError):
            build_handoff_contract(
                **self._valid_handoff(authority_requested="CAPITAL")
            )

    def test_approve_authority_rejected(self):
        with self.assertRaises(ValueError):
            build_handoff_contract(
                **self._valid_handoff(authority_requested="APPROVE")
            )

    def test_advisory_review_permitted(self):
        h = build_handoff_contract(
            **self._valid_handoff(authority_requested=AuthorityRequest.ADVISORY_REVIEW)
        )
        self.assertEqual(h.authority_requested, AuthorityRequest.ADVISORY_REVIEW)

    def test_escalate_permitted(self):
        h = build_handoff_contract(
            **self._valid_handoff(authority_requested=AuthorityRequest.ESCALATE)
        )
        self.assertEqual(h.authority_requested, AuthorityRequest.ESCALATE)

    def test_empty_sender_rejected(self):
        with self.assertRaises(ValueError):
            build_handoff_contract(**self._valid_handoff(sender=""))

    def test_empty_current_gate_rejected(self):
        with self.assertRaises(ValueError):
            build_handoff_contract(**self._valid_handoff(current_gate=""))

    def test_non_dict_evidence_rejected(self):
        with self.assertRaises(TypeError):
            build_handoff_contract(**self._valid_handoff(evidence="not a dict"))

    def test_to_dict_round_trip(self):
        h = build_handoff_contract(**self._valid_handoff(
            known_failures=["f1"], tests_or_evidence_produced=["t1"]
        ))
        d = h.to_dict()
        self.assertIsInstance(d["known_failures"], list)
        self.assertIsInstance(d["tests_or_evidence_produced"], list)
        self.assertEqual(d["sender"], "agent-a")


# ═══════════════════════════════════════════════════════════════════════════════
# T5 — Capability Boundary
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapabilityBoundary(unittest.TestCase):

    def test_deny_by_default_unregistered_agent(self):
        """An agent not in the boundary dict is blocked entirely."""
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        result = boundary.pre_tool_use_hook("unknown-agent", "tool_x", {})
        self.assertTrue(result.blocked)
        self.assertEqual(result.status, HookStatus.DENIED_AGENT_NOT_REGISTERED)

    def test_deny_by_default_registered_agent_with_empty_allowlist(self):
        """An agent with an empty allowlist is denied all tools."""
        boundary = _boundary_for({"agent-empty": []})
        result = boundary.pre_tool_use_hook("agent-empty", "any_tool", {})
        self.assertTrue(result.blocked)
        self.assertEqual(result.status, HookStatus.DENIED_TOOL_NOT_PERMITTED)

    def test_allowed_tool_for_registered_agent(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        result = boundary.pre_tool_use_hook("agent-a", "tool_x", {})
        self.assertFalse(result.blocked)
        self.assertEqual(result.status, HookStatus.ALLOWED)

    def test_disallowed_tool_for_registered_agent(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        result = boundary.pre_tool_use_hook("agent-a", "tool_y", {})
        self.assertTrue(result.blocked)
        self.assertEqual(result.status, HookStatus.DENIED_TOOL_NOT_PERMITTED)

    def test_cross_agent_tool_use_blocked(self):
        """Agent-a's tool is in boundary but agent-b cannot use it."""
        boundary = _boundary_for({"agent-a": ["tool_x"], "agent-b": ["tool_y"]})
        result = boundary.pre_tool_use_hook("agent-b", "tool_x", {})
        self.assertTrue(result.blocked)

    def test_forbidden_key_in_tool_input_blocked(self):
        """Pre-hook blocks tool input containing a forbidden governance key."""
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        tool_input = {"data": "ok", "can_execute": True}
        result = boundary.pre_tool_use_hook("agent-a", "tool_x", tool_input)
        self.assertTrue(result.blocked)
        self.assertEqual(result.status, HookStatus.DENIED_FORBIDDEN_KEY)

    def test_forbidden_key_nested_in_tool_input_blocked(self):
        """Recursive scan catches governance keys in nested input dicts."""
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        tool_input = {"payload": {"inner": {"terminal_label": "WATCH"}}}
        result = boundary.pre_tool_use_hook("agent-a", "tool_x", tool_input)
        self.assertTrue(result.blocked)
        self.assertEqual(result.status, HookStatus.DENIED_FORBIDDEN_KEY)

    def test_clean_tool_input_allowed(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        tool_input = {"city": "CHI", "date": "2026-08-09"}
        result = boundary.pre_tool_use_hook("agent-a", "tool_x", tool_input)
        self.assertFalse(result.blocked)

    def test_post_hook_clean_output_passes(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        output = {"findings": {"temperature": 85}, "confidence": "HIGH"}
        result = boundary.post_tool_use_hook("agent-a", "tool_x", output)
        self.assertTrue(result.passed)
        self.assertEqual(result.status, HookStatus.ALLOWED)

    def test_post_hook_forbidden_key_in_output_flagged(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        output = {"findings": {"stake_tier": "STANDARD"}}
        result = boundary.post_tool_use_hook("agent-a", "tool_x", output)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, HookStatus.DENIED_FORBIDDEN_KEY)
        self.assertIsNotNone(result.violation)

    def test_post_hook_forbidden_key_three_levels_deep(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        output = {"a": {"b": {"c": {"final_decision": "PLAY"}}}}
        result = boundary.post_tool_use_hook("agent-a", "tool_x", output)
        self.assertEqual(result.status, HookStatus.DENIED_FORBIDDEN_KEY)

    def test_post_hook_forbidden_key_in_list_flagged(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        output = {"items": [{"good": 1}, {"is_playable": False}]}
        result = boundary.post_tool_use_hook("agent-a", "tool_x", output)
        self.assertEqual(result.status, HookStatus.DENIED_FORBIDDEN_KEY)

    def test_post_hook_non_dict_output_flagged(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        result = boundary.post_tool_use_hook("agent-a", "tool_x", "not a dict")
        self.assertEqual(result.status, HookStatus.DENIED_FORBIDDEN_KEY)

    def test_allowed_tools_for_agent_returns_frozenset(self):
        boundary = _boundary_for({"agent-a": ["tool_x", "tool_y"]})
        allowed = boundary.allowed_tools_for_agent("agent-a")
        self.assertIsInstance(allowed, frozenset)
        self.assertEqual(allowed, frozenset({"tool_x", "tool_y"}))

    def test_allowed_tools_empty_for_unregistered(self):
        boundary = _boundary_for({"agent-a": ["tool_x"]})
        self.assertEqual(boundary.allowed_tools_for_agent("ghost"), frozenset())

    def test_from_registry_entries(self):
        """from_registry_entries() builds boundary from registry entries."""
        entries = [
            _make_entry("reg-a", caps=["emit_a", "emit_b"]),
            _make_entry("reg-b", caps=["emit_c"]),
        ]
        boundary = UniversalCapabilityBoundary.from_registry_entries(entries)
        self.assertEqual(boundary.allowed_tools_for_agent("reg-a"),
                         frozenset({"emit_a", "emit_b"}))
        self.assertEqual(boundary.allowed_tools_for_agent("reg-b"),
                         frozenset({"emit_c"}))

    def test_all_registered_agents_sorted(self):
        boundary = _boundary_for({"b-agent": ["t1"], "a-agent": ["t2"]})
        self.assertEqual(boundary.all_registered_agents(), ["a-agent", "b-agent"])


# ═══════════════════════════════════════════════════════════════════════════════
# T6 — Budget Guard (pure function, no DB required)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBudgetGuard(unittest.TestCase):

    def test_within_budget_allowed(self):
        result = compute_budget_guard(
            input_tokens=1000,
            output_tokens=200,
            input_price_per_1k=0.001,
            output_price_per_1k=0.003,
            max_cost_usd=0.01,
        )
        self.assertTrue(result["allowed"])
        self.assertAlmostEqual(result["estimated_cost_usd"], 0.001 + 0.0006, places=6)

    def test_over_budget_blocked(self):
        result = compute_budget_guard(
            input_tokens=10_000,
            output_tokens=2_000,
            input_price_per_1k=0.001,
            output_price_per_1k=0.003,
            max_cost_usd=0.005,
        )
        self.assertFalse(result["allowed"])

    def test_exactly_at_limit_allowed(self):
        result = compute_budget_guard(
            input_tokens=1000,
            output_tokens=0,
            input_price_per_1k=0.01,
            output_price_per_1k=0.01,
            max_cost_usd=0.01,
        )
        self.assertTrue(result["allowed"])
        self.assertAlmostEqual(result["remaining_usd"], 0.0, places=6)

    def test_configurable_pricing_used(self):
        """Pricing is not hardcoded — different per-token rates produce different costs."""
        cheap = compute_budget_guard(
            input_tokens=1000, output_tokens=200,
            input_price_per_1k=0.0001, output_price_per_1k=0.0003,
            max_cost_usd=0.01,
        )
        expensive = compute_budget_guard(
            input_tokens=1000, output_tokens=200,
            input_price_per_1k=0.01, output_price_per_1k=0.03,
            max_cost_usd=0.01,
        )
        self.assertTrue(cheap["allowed"])
        self.assertFalse(expensive["allowed"])

    def test_cost_components_reported(self):
        result = compute_budget_guard(
            input_tokens=2000, output_tokens=500,
            input_price_per_1k=0.002, output_price_per_1k=0.006,
            max_cost_usd=1.0,
        )
        self.assertAlmostEqual(result["input_cost_usd"],  0.004, places=6)
        self.assertAlmostEqual(result["output_cost_usd"], 0.003, places=6)

    def test_zero_tokens_zero_cost(self):
        result = compute_budget_guard(
            input_tokens=0, output_tokens=0,
            input_price_per_1k=0.01, output_price_per_1k=0.03,
            max_cost_usd=0.01,
        )
        self.assertTrue(result["allowed"])
        self.assertAlmostEqual(result["estimated_cost_usd"], 0.0, places=6)


# ═══════════════════════════════════════════════════════════════════════════════
# T7 — Cross-module: test helpers use real validation (Weather Step 14D lesson)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossModuleIntegration(unittest.TestCase):

    def test_valid_output_payload_helper_passes_real_validator(self):
        """
        valid_output_payload() (test helper) must produce a payload that passes
        the real validate_output_contract() validator. Same validator, not a stub.
        Weather Step 14D: mock/test path must share real validation.
        """
        payload = valid_output_payload()
        result = validate_output_contract(payload)
        self.assertIs(result, OUTPUT_VALID)

    def test_build_test_packet_is_real_evidence_packet_type(self):
        """build_test_packet() returns a genuine EvidencePacket, not a dict."""
        p = build_test_packet()
        self.assertIsInstance(p, EvidencePacket)
        # Real __post_init__ validation ran
        self.assertTrue(len(p.snapshot_id) > 0)

    def test_registry_entry_advisory_only_consistent_with_output_contract(self):
        """
        Registry entry advisory_only=True is the same semantics as
        output contract requiring advisory_only=True in payloads.
        """
        entry = _make_entry("cross-agent")
        payload = valid_output_payload(advisory_only=entry.advisory_only)
        result = validate_output_contract(payload)
        self.assertIs(result, OUTPUT_VALID)

    def test_capability_boundary_from_registry_denies_unlisted_tool(self):
        """from_registry_entries and pre_tool_use_hook work end-to-end."""
        entry = _make_entry("e2e-agent", caps=["emit_findings"])
        boundary = UniversalCapabilityBoundary.from_registry_entries([entry])
        blocked = boundary.pre_tool_use_hook("e2e-agent", "other_tool", {})
        allowed = boundary.pre_tool_use_hook("e2e-agent", "emit_findings", {})
        self.assertTrue(blocked.blocked)
        self.assertFalse(allowed.blocked)

    def test_forbidden_key_set_shared_between_contract_and_boundary(self):
        """
        output_contract.FORBIDDEN_GOVERNANCE_KEYS is the same object
        imported by capability_boundary — single source of truth.
        """
        from gate_engine.universal_agent.capability_boundary import (
            FORBIDDEN_GOVERNANCE_KEYS as CB_FORBIDDEN,
        )
        self.assertIs(FORBIDDEN_GOVERNANCE_KEYS, CB_FORBIDDEN)

    def test_handoff_contract_to_dict_evidence_is_plain_dict(self):
        h = build_handoff_contract(
            current_gate="g", sender="s", recipient="r",
            claim="claim", requested_ruling="RULE",
            evidence={"confidence": 0.8},
        )
        d = h.to_dict()
        self.assertIsInstance(d["evidence"], dict)

    def test_usage_status_constants(self):
        self.assertEqual(UsageStatus.AVAILABLE,   "AVAILABLE")
        self.assertEqual(UsageStatus.UNAVAILABLE, "UNAVAILABLE")
        self.assertEqual(UsageStatus.BLOCKED,     "BLOCKED")
        self.assertEqual(UsageStatus.ERROR,       "ERROR")

    def test_agent_role_constants(self):
        self.assertEqual(AgentRole.FORECAST_CONTEXT,       "FORECAST_CONTEXT")
        self.assertEqual(AgentRole.SOURCE_RECONCILIATION,  "SOURCE_RECONCILIATION")
        self.assertEqual(AgentRole.CONTRADICTION_DETECTOR, "CONTRADICTION_DETECTOR")
        self.assertEqual(AgentRole.UNUSUAL_REGIME,         "UNUSUAL_REGIME")
        self.assertEqual(AgentRole.UNCERTAINTY_EXPLAINER,  "UNCERTAINTY_EXPLAINER")
        self.assertEqual(AgentRole.SUMMARY_SYNTHESIZER,    "SUMMARY_SYNTHESIZER")


if __name__ == "__main__":
    unittest.main()
