"""
gate_engine/tests/test_b0_universal_agent.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0 acceptance tests.

Covers:
  (a) EvidencePacket is frozen; same object identity passed to all runners.
  (b) AgentRegistry rejects entries where advisory_only is not True.
  (c) validate_output_contract rejects every key in FORBIDDEN_GOVERNANCE_KEYS.
  (d) UniversalCapabilityBoundary is deny-by-default.
  (e) HandoffContract is data-schema only (no authority grant).
  (f) Authority constants in the package __init__.py.

No network or DB calls anywhere.
"""
from __future__ import annotations

import dataclasses
import unittest

import gate_engine.universal_agent as ua_pkg
from gate_engine.universal_agent.agent_registry import (
    AgentRegistry,
    AgentRegistryEntry,
    BudgetConfig,
)
from gate_engine.universal_agent.capability_boundary import (
    HookStatus,
    UniversalCapabilityBoundary,
)
from gate_engine.universal_agent.evidence_packet import (
    EvidencePacket,
    Lane,
    build_evidence_packet,
    build_test_packet,
)
from gate_engine.universal_agent.handoff_contract import (
    AuthorityRequest,
    HandoffContract,
    NextAction,
    build_handoff_contract,
)
from gate_engine.universal_agent.output_contract import (
    FORBIDDEN_GOVERNANCE_KEYS,
    OUTPUT_VALID,
    OutputContractViolation,
    OutputViolationCode,
    _scan_forbidden_keys,
    valid_output_payload,
    validate_output_contract,
)


def _make_entry(agent_id: str = "agent-a", **kw) -> AgentRegistryEntry:
    base = dict(
        agent_id=agent_id,
        role="TEST_ROLE",
        lane=Lane.UNKNOWN,
        allowed_capabilities=["tool_x"],
        input_schema_ref="ref.in",
        output_schema_ref="ref.out",
    )
    base.update(kw)
    return AgentRegistryEntry(**base)


# ── (a) EvidencePacket ─────────────────────────────────────────────────────────

class TestEvidencePacket(unittest.TestCase):
    def test_packet_is_frozen_dataclass(self):
        self.assertTrue(dataclasses.is_dataclass(EvidencePacket))
        self.assertTrue(EvidencePacket.__dataclass_params__.frozen)

    def test_mutation_raises_frozen_error(self):
        pkt = build_test_packet()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            pkt.run_id = "mutated"

    def test_mutation_of_lane_raises(self):
        pkt = build_test_packet()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            pkt.lane = "OTHER"

    def test_build_test_packet_uses_real_type(self):
        pkt = build_test_packet()
        self.assertIsInstance(pkt, EvidencePacket)

    def test_same_object_identity_preserved_across_consumers(self):
        # Passing the packet around never copies it — identity is stable.
        pkt = build_test_packet()
        seen = [pkt for _ in range(6)]
        self.assertTrue(all(p is pkt for p in seen))
        self.assertEqual(len({id(p) for p in seen}), 1)

    def test_empty_run_id_rejected(self):
        with self.assertRaises(ValueError):
            build_evidence_packet(run_id="  ", canonical_event_id="e", lane="L")

    def test_empty_canonical_event_id_rejected(self):
        with self.assertRaises(ValueError):
            build_evidence_packet(run_id="r", canonical_event_id="", lane="L")

    def test_non_string_lane_rejected(self):
        with self.assertRaises(ValueError):
            build_evidence_packet(run_id="r", canonical_event_id="e", lane=None)

    def test_defaults_are_empty_collections_never_none(self):
        pkt = build_evidence_packet(run_id="r", canonical_event_id="e", lane="L")
        self.assertEqual(pkt.source_timestamps, {})
        self.assertEqual(pkt.market_snapshot, {})
        self.assertEqual(pkt.source_failures, ())
        self.assertEqual(pkt.source_conflicts, ())

    def test_snapshot_id_auto_generated_and_unique(self):
        p1 = build_evidence_packet(run_id="r", canonical_event_id="e", lane="L")
        p2 = build_evidence_packet(run_id="r", canonical_event_id="e", lane="L")
        self.assertTrue(p1.snapshot_id)
        self.assertNotEqual(p1.snapshot_id, p2.snapshot_id)

    def test_lists_converted_to_tuples(self):
        pkt = build_evidence_packet(
            run_id="r", canonical_event_id="e", lane="L",
            source_failures=[{"source": "x", "reason": "y"}],
        )
        self.assertIsInstance(pkt.source_failures, tuple)

    def test_to_dict_round_trip_fields(self):
        pkt = build_test_packet()
        d = pkt.to_dict()
        self.assertEqual(d["run_id"], pkt.run_id)
        self.assertEqual(d["snapshot_id"], pkt.snapshot_id)
        self.assertIsInstance(d["source_failures"], list)

    def test_lane_known_contains_declared_constants(self):
        known = Lane.known()
        self.assertIn("MLB_MONEYLINE", known)
        self.assertIn("WNBA_PROPS", known)
        self.assertIn("KALSHI_WEATHER", known)


# ── (b) AgentRegistry advisory_only enforcement ────────────────────────────────

class TestAgentRegistry(unittest.TestCase):
    def test_advisory_only_always_true(self):
        self.assertIs(_make_entry().advisory_only, True)

    def test_advisory_only_cannot_be_set_false(self):
        entry = _make_entry()
        with self.assertRaises(AttributeError):
            entry.advisory_only = False

    def test_advisory_only_cannot_be_set_true_either(self):
        entry = _make_entry()
        with self.assertRaises(AttributeError):
            entry.advisory_only = True

    def test_register_rejects_non_advisory_entry(self):
        # Simulate a hostile subclass overriding the property to False.
        class BadEntry(AgentRegistryEntry):
            @property
            def advisory_only(self):
                return False
        reg = AgentRegistry()
        with self.assertRaises(ValueError):
            reg.register(BadEntry(
                agent_id="bad", role="R", lane="L",
                allowed_capabilities=[], input_schema_ref="i",
                output_schema_ref="o",
            ))

    def test_duplicate_registration_raises_keyerror(self):
        reg = AgentRegistry()
        reg.register(_make_entry("dup"))
        with self.assertRaises(KeyError):
            reg.register(_make_entry("dup"))

    def test_get_unregistered_raises_keyerror(self):
        with self.assertRaises(KeyError):
            AgentRegistry().get("ghost")

    def test_unregister_missing_raises_keyerror(self):
        with self.assertRaises(KeyError):
            AgentRegistry().unregister("ghost")

    def test_register_get_unregister_lifecycle(self):
        reg = AgentRegistry()
        reg.register(_make_entry("a"))
        self.assertTrue(reg.is_registered("a"))
        self.assertEqual(reg.get("a").agent_id, "a")
        reg.unregister("a")
        self.assertFalse(reg.is_registered("a"))
        self.assertEqual(len(reg), 0)

    def test_agents_for_lane_and_role(self):
        reg = AgentRegistry()
        reg.register(_make_entry("a", lane="L1", role="R1"))
        reg.register(_make_entry("b", lane="L2", role="R1"))
        self.assertEqual([e.agent_id for e in reg.agents_for_lane("L1")], ["a"])
        self.assertEqual(len(reg.agents_for_role("R1")), 2)

    def test_entry_requires_non_empty_identity(self):
        with self.assertRaises(ValueError):
            _make_entry(agent_id="  ")
        with self.assertRaises(ValueError):
            _make_entry(role="")
        with self.assertRaises(ValueError):
            _make_entry(lane="")

    def test_budget_config_immutable(self):
        b = BudgetConfig()
        with self.assertRaises(AttributeError):
            b.max_cost_usd = 999.0

    def test_resolve_model_none_when_no_module(self):
        self.assertIsNone(_make_entry().resolve_model())

    def test_resolve_model_reads_module_attr_at_call_time(self):
        import types
        mod = types.ModuleType("fake_model_mod")
        mod._MODEL = "model-v1"
        entry = _make_entry(model_module=mod)
        self.assertEqual(entry.resolve_model(), "model-v1")
        mod._MODEL = "model-v2"
        self.assertEqual(entry.resolve_model(), "model-v2")


# ── (c) Output contract forbidden keys ─────────────────────────────────────────

class TestOutputContractForbiddenKeys(unittest.TestCase):
    def test_every_forbidden_key_rejected_at_root(self):
        for key in sorted(FORBIDDEN_GOVERNANCE_KEYS):
            payload = valid_output_payload()
            payload[key] = "x"
            result = validate_output_contract(payload)
            self.assertIsInstance(result, OutputContractViolation, key)
            self.assertEqual(
                result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY, key
            )

    def test_every_forbidden_key_rejected_when_nested(self):
        for key in sorted(FORBIDDEN_GOVERNANCE_KEYS):
            payload = valid_output_payload(
                advisory_findings={"nested": {"deeper": {key: 1}}}
            )
            result = validate_output_contract(payload)
            self.assertEqual(
                result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY, key
            )

    def test_forbidden_key_case_insensitive(self):
        payload = valid_output_payload()
        payload["CAN_EXECUTE"] = True
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_forbidden_key_inside_list_detected(self):
        payload = valid_output_payload(
            advisory_findings={"items": [{"terminal_label": "PLAY"}]}
        )
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY)

    def test_valid_payload_passes(self):
        self.assertIs(validate_output_contract(valid_output_payload()), OUTPUT_VALID)

    def test_non_dict_rejected(self):
        result = validate_output_contract(["not", "a", "dict"])
        self.assertEqual(result.code, OutputViolationCode.NOT_A_DICT)

    def test_advisory_only_must_be_exactly_true(self):
        for bad in (False, 1, "true", None):
            payload = valid_output_payload()
            payload["advisory_only"] = bad
            result = validate_output_contract(payload)
            self.assertEqual(
                result.code, OutputViolationCode.ADVISORY_ONLY_NOT_TRUE, repr(bad)
            )

    def test_extra_field_rejected(self):
        payload = valid_output_payload()
        payload["unexpected_field"] = 1
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.EXTRA_FIELD)

    def test_missing_required_field_rejected(self):
        payload = valid_output_payload()
        del payload["snapshot_id"]
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.MISSING_REQUIRED_FIELD)

    def test_wrong_type_agent_id(self):
        payload = valid_output_payload()
        payload["agent_id"] = 123
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.WRONG_TYPE)

    def test_wrong_type_token_counts(self):
        payload = valid_output_payload(input_tokens="lots")
        result = validate_output_contract(payload)
        self.assertEqual(result.code, OutputViolationCode.WRONG_TYPE)

    def test_violation_is_falsy_valid_is_truthy(self):
        self.assertTrue(OUTPUT_VALID)
        self.assertFalse(OutputContractViolation(code="X", message="m"))

    def test_scan_forbidden_keys_returns_none_on_clean(self):
        self.assertIsNone(_scan_forbidden_keys({"clean": {"nested": [1, 2]}}))


# ── (d) Capability boundary deny-by-default ────────────────────────────────────

class TestCapabilityBoundary(unittest.TestCase):
    def test_unregistered_agent_denied(self):
        b = UniversalCapabilityBoundary({})
        pre = b.pre_tool_use_hook("ghost", "any_tool", {})
        self.assertTrue(pre.blocked)
        self.assertEqual(pre.status, HookStatus.DENIED_AGENT_NOT_REGISTERED)

    def test_registered_agent_empty_allowlist_denied(self):
        b = UniversalCapabilityBoundary({"a": set()})
        pre = b.pre_tool_use_hook("a", "tool_x", {})
        self.assertTrue(pre.blocked)
        self.assertEqual(pre.status, HookStatus.DENIED_TOOL_NOT_PERMITTED)

    def test_tool_not_in_allowlist_denied(self):
        b = UniversalCapabilityBoundary({"a": {"tool_x"}})
        pre = b.pre_tool_use_hook("a", "tool_y", {})
        self.assertTrue(pre.blocked)

    def test_allowed_tool_permitted(self):
        b = UniversalCapabilityBoundary({"a": {"tool_x"}})
        pre = b.pre_tool_use_hook("a", "tool_x", {"clean": True})
        self.assertFalse(pre.blocked)
        self.assertEqual(pre.status, HookStatus.ALLOWED)

    def test_pre_hook_blocks_forbidden_key_in_input(self):
        b = UniversalCapabilityBoundary({"a": {"tool_x"}})
        pre = b.pre_tool_use_hook("a", "tool_x", {"can_execute": True})
        self.assertTrue(pre.blocked)
        self.assertEqual(pre.status, HookStatus.DENIED_FORBIDDEN_KEY)

    def test_post_hook_flags_forbidden_key_in_output(self):
        b = UniversalCapabilityBoundary({"a": {"tool_x"}})
        post = b.post_tool_use_hook("a", "tool_x", {"final_decision": "PLAY"})
        self.assertFalse(post.passed)
        self.assertEqual(post.status, HookStatus.DENIED_FORBIDDEN_KEY)
        self.assertIsNotNone(post.violation)

    def test_post_hook_rejects_non_dict_output(self):
        b = UniversalCapabilityBoundary({"a": {"tool_x"}})
        post = b.post_tool_use_hook("a", "tool_x", "raw string")
        self.assertFalse(post.passed)

    def test_post_hook_clean_output_passes(self):
        b = UniversalCapabilityBoundary({"a": {"tool_x"}})
        post = b.post_tool_use_hook("a", "tool_x", {"advisory": "fine"})
        self.assertTrue(post.passed)

    def test_from_registry_entries_syncs_allowlists(self):
        entry = _make_entry("a", allowed_capabilities=["cap_1", "cap_2"])
        b = UniversalCapabilityBoundary.from_registry_entries([entry])
        self.assertEqual(b.allowed_tools_for_agent("a"), frozenset({"cap_1", "cap_2"}))
        self.assertEqual(b.allowed_tools_for_agent("ghost"), frozenset())


# ── (e) HandoffContract is data-schema only ────────────────────────────────────

class TestHandoffContract(unittest.TestCase):
    def _build(self, **kw):
        base = dict(
            current_gate="G1", sender="agent-a", recipient="agent-b",
            claim="claim", requested_ruling="review",
        )
        base.update(kw)
        return build_handoff_contract(**base)

    def test_valid_contract_builds(self):
        c = self._build()
        self.assertEqual(c.authority_requested, AuthorityRequest.NONE)
        self.assertEqual(c.next_allowed_action, NextAction.CONTINUE_PIPELINE)

    def test_every_forbidden_authority_rejected(self):
        for auth in sorted(AuthorityRequest._FORBIDDEN):
            with self.assertRaises(ValueError, msg=auth):
                self._build(authority_requested=auth)

    def test_forbidden_authority_case_insensitive(self):
        with self.assertRaises(ValueError):
            self._build(authority_requested="execute")

    def test_permitted_authority_values(self):
        for auth in (AuthorityRequest.NONE, AuthorityRequest.ADVISORY_REVIEW,
                     AuthorityRequest.ESCALATE):
            self.assertEqual(self._build(authority_requested=auth).authority_requested, auth)

    def test_contract_is_frozen(self):
        c = self._build()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            c.claim = "mutated"

    def test_no_execution_authority_fields_in_schema(self):
        field_names = {f.name for f in dataclasses.fields(HandoffContract)}
        for banned in ("can_execute", "execute", "capital", "trade",
                       "final_decision", "stake_tier", "terminal_label"):
            self.assertNotIn(banned, field_names)

    def test_non_dict_evidence_rejected(self):
        with self.assertRaises(TypeError):
            self._build(evidence=["not-a-dict"])

    def test_empty_sender_rejected(self):
        with self.assertRaises(ValueError):
            self._build(sender="  ")

    def test_lists_frozen_to_tuples(self):
        c = self._build(known_failures=["f1"], tests_or_evidence_produced=["t1"])
        self.assertIsInstance(c.known_failures, tuple)
        self.assertIsInstance(c.tests_or_evidence_produced, tuple)


# ── (f) Package authority constants ────────────────────────────────────────────

class TestB0AuthorityConstants(unittest.TestCase):
    def test_can_execute_false(self):
        self.assertIs(ua_pkg.can_execute, False)

    def test_production_authority_false(self):
        self.assertIs(ua_pkg.PRODUCTION_AUTHORITY, False)

    def test_user_output_authority_false(self):
        self.assertIs(ua_pkg.USER_OUTPUT_AUTHORITY, False)

    def test_capital_authority_false(self):
        self.assertIs(ua_pkg.CAPITAL_AUTHORITY, False)

    def test_no_auto_promotion_true(self):
        self.assertIs(ua_pkg.NO_AUTO_PROMOTION, True)

    def test_patch_id(self):
        self.assertEqual(ua_pkg.PATCH_ID, "WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1")

    def test_patch_id_declared_in_all_six_packages(self):
        import gate_engine.universal_agent.canary as canary_pkg
        import gate_engine.universal_agent.lanes.mlb_moneyline as mlb_pkg
        import gate_engine.universal_agent.lanes.wnba_props as wnba_pkg
        import gate_engine.universal_agent.roles as roles_pkg
        import gate_engine.universal_agent.shadow as shadow_pkg
        expected = {
            ua_pkg:     "WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1",
            roles_pkg:  "WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1",
            mlb_pkg:    "WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3A",
            shadow_pkg: "WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3B",
            canary_pkg: "WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C",
            wnba_pkg:   "WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4",
        }
        for pkg, patch_id in expected.items():
            self.assertEqual(pkg.PATCH_ID, patch_id, pkg.__name__)

    def test_docstring_no_stale_not_yet_built_block(self):
        self.assertNotIn("NOT YET BUILT", ua_pkg.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
