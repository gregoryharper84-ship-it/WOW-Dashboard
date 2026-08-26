"""
tests/test_kalshi_wx_shadow_stage1.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Stage 1 tests

Tests for gate_engine/kalshi_wx_shadow_capability_boundary.py.

No live API calls, no network access, no DB access.

Test plan — Section B (Boundary)
─────────────────────────────────
B1:  Pre-hook allows the correct tool for each of the 5 registered subagents.
B2:  Pre-hook denies an unknown tool name (deny-by-default).
B3:  Pre-hook denies a cross-subagent tool call (forecast_context calling
     emit_source_reconciliation, even though it's in ALL_ALLOWED_SHADOW_TOOLS).
B4:  Pre-hook denies an unknown subagent_id.
B5:  Pre-hook rejects tool input containing a forbidden governance key.
B6:  Pre-hook rejects tool input containing a forbidden key nested inside a list.
B7:  Post-hook passes for clean dict output.
B8:  Post-hook rejects non-dict output.
B9:  Post-hook rejects dict output containing a forbidden governance key.
B10: ALL_ALLOWED_SHADOW_TOOLS contains exactly the 5 expected tool names.
B11: REGISTERED_SUBAGENT_IDS contains exactly the 5 expected subagent IDs.
B12: assert_inert() passes on KalshiWxShadowResearchClient (authority regression).
B13: assert_inert() raises on a subclass with CAN_EXECUTE=True.
B14: Each subagent's allowed tool set has exactly 1 entry.
"""
from __future__ import annotations

import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.kalshi_wx_shadow_capability_boundary import (
    ALL_ALLOWED_SHADOW_TOOLS,
    REGISTERED_SUBAGENT_IDS,
    CapabilityBoundary,
    PreHookResult,
    PostHookResult,
)
from gate_engine.kalshi_wx_shadow_client import KalshiWxShadowResearchClient

_BOUNDARY = CapabilityBoundary()

_SUBAGENT_TOOL_PAIRS = [
    ("forecast_context",        "emit_forecast_context"),
    ("source_reconciliation",   "emit_source_reconciliation"),
    ("contradiction_detection", "emit_contradiction_detection"),
    ("unusual_regime",          "emit_regime_assessment"),
    ("uncertainty_explanation", "emit_uncertainty_summary"),
]

_CLEAN_INPUT = {"scoring_mode": "gaussian_forecast", "calibration_status": "UNAVAILABLE"}


class TestB1PreHookAllowsCorrectTool(unittest.TestCase):

    def test_B1_each_subagent_allowed_its_own_tool(self):
        for subagent_id, tool_name in _SUBAGENT_TOOL_PAIRS:
            with self.subTest(subagent_id=subagent_id, tool_name=tool_name):
                result = _BOUNDARY.pre_tool_use_hook(subagent_id, tool_name, _CLEAN_INPUT)
                self.assertIsInstance(result, PreHookResult)
                self.assertTrue(
                    result.allowed,
                    f"Expected allowed=True for {subagent_id}/{tool_name}; "
                    f"got reason={result.reason!r}",
                )
                self.assertIsNone(result.reason)


class TestB2PreHookDeniesUnknownTool(unittest.TestCase):

    def test_B2_unknown_tool_denied_for_all_subagents(self):
        for subagent_id, _ in _SUBAGENT_TOOL_PAIRS:
            with self.subTest(subagent_id=subagent_id):
                result = _BOUNDARY.pre_tool_use_hook(
                    subagent_id, "emit_TOTALLY_UNKNOWN_tool", {}
                )
                self.assertFalse(result.allowed)
                self.assertIsNotNone(result.reason)
                self.assertIn("TOOL_NOT_ALLOWED", result.reason)


class TestB3PreHookDeniesCrossSubagentTool(unittest.TestCase):

    def test_B3_forecast_context_cannot_call_source_reconciliation_tool(self):
        """
        emit_source_reconciliation IS in ALL_ALLOWED_SHADOW_TOOLS, but
        forecast_context is not allowed to call it — per-subagent enforcement.
        """
        result = _BOUNDARY.pre_tool_use_hook(
            "forecast_context", "emit_source_reconciliation", _CLEAN_INPUT
        )
        self.assertFalse(result.allowed)
        self.assertIn("TOOL_NOT_ALLOWED", result.reason)

    def test_B3_contradiction_detection_cannot_call_forecast_context_tool(self):
        result = _BOUNDARY.pre_tool_use_hook(
            "contradiction_detection", "emit_forecast_context", _CLEAN_INPUT
        )
        self.assertFalse(result.allowed)

    def test_B3_uncertainty_explanation_cannot_call_regime_assessment_tool(self):
        result = _BOUNDARY.pre_tool_use_hook(
            "uncertainty_explanation", "emit_regime_assessment", _CLEAN_INPUT
        )
        self.assertFalse(result.allowed)


class TestB4PreHookDeniesUnknownSubagent(unittest.TestCase):

    def test_B4_unknown_subagent_id_denied(self):
        result = _BOUNDARY.pre_tool_use_hook(
            "nonexistent_subagent", "emit_forecast_context", _CLEAN_INPUT
        )
        self.assertFalse(result.allowed)
        self.assertIn("UNKNOWN_SUBAGENT", result.reason)

    def test_B4_empty_subagent_id_denied(self):
        result = _BOUNDARY.pre_tool_use_hook("", "emit_forecast_context", _CLEAN_INPUT)
        self.assertFalse(result.allowed)


class TestB5PreHookRejectsForbiddenKeyInInput(unittest.TestCase):

    def test_B5_forbidden_key_terminal_label_in_flat_input(self):
        bad_input = {"scoring_mode": "gaussian_forecast", "terminal_label": "X"}
        result = _BOUNDARY.pre_tool_use_hook(
            "forecast_context", "emit_forecast_context", bad_input
        )
        self.assertFalse(result.allowed)
        self.assertIn("FORBIDDEN_KEY_IN_TOOL_INPUT", result.reason)
        self.assertIn("terminal_label", result.reason)

    def test_B5_forbidden_key_can_execute_rejected(self):
        bad_input = {"can_execute": True}
        result = _BOUNDARY.pre_tool_use_hook(
            "forecast_context", "emit_forecast_context", bad_input
        )
        self.assertFalse(result.allowed)

    def test_B5_forbidden_key_in_nested_dict(self):
        bad_input = {"meta": {"governance_state": "APPROVED"}}
        result = _BOUNDARY.pre_tool_use_hook(
            "forecast_context", "emit_forecast_context", bad_input
        )
        self.assertFalse(result.allowed)
        self.assertIn("FORBIDDEN_KEY_IN_TOOL_INPUT", result.reason)


class TestB6PreHookRejectsForbiddenKeyInList(unittest.TestCase):

    def test_B6_forbidden_key_inside_list_element(self):
        bad_input = {"blockers": [{"authorized": True}]}
        result = _BOUNDARY.pre_tool_use_hook(
            "forecast_context", "emit_forecast_context", bad_input
        )
        self.assertFalse(result.allowed)
        self.assertIn("FORBIDDEN_KEY_IN_TOOL_INPUT", result.reason)


class TestB7PostHookPassesCleanOutput(unittest.TestCase):

    def test_B7_clean_dict_passes_post_hook(self):
        for subagent_id, tool_name in _SUBAGENT_TOOL_PAIRS:
            with self.subTest(subagent_id=subagent_id):
                result = _BOUNDARY.post_tool_use_hook(
                    subagent_id, tool_name, {"status": "ok", "tier": "HIGH"}
                )
                self.assertIsInstance(result, PostHookResult)
                self.assertTrue(
                    result.passed,
                    f"Post-hook should pass for clean dict; got reason={result.reason!r}",
                )

    def test_B7_empty_dict_passes(self):
        result = _BOUNDARY.post_tool_use_hook(
            "forecast_context", "emit_forecast_context", {}
        )
        self.assertTrue(result.passed)


class TestB8PostHookRejectsNonDict(unittest.TestCase):

    def test_B8_string_output_rejected(self):
        result = _BOUNDARY.post_tool_use_hook(
            "forecast_context", "emit_forecast_context", "not a dict"
        )
        self.assertFalse(result.passed)
        self.assertIn("POST_HOOK_TYPE", result.reason)

    def test_B8_none_output_rejected(self):
        result = _BOUNDARY.post_tool_use_hook(
            "forecast_context", "emit_forecast_context", None
        )
        self.assertFalse(result.passed)

    def test_B8_list_output_rejected(self):
        result = _BOUNDARY.post_tool_use_hook(
            "forecast_context", "emit_forecast_context", ["a", "b"]
        )
        self.assertFalse(result.passed)


class TestB9PostHookRejectsForbiddenKeyInOutput(unittest.TestCase):

    def test_B9_forbidden_key_label_in_output(self):
        bad_output = {"label": "SOME_LABEL", "ceiling": "KALSHI_WATCH"}
        result = _BOUNDARY.post_tool_use_hook(
            "forecast_context", "emit_forecast_context", bad_output
        )
        self.assertFalse(result.passed)
        self.assertIn("FORBIDDEN_KEY_IN_TOOL_OUTPUT", result.reason)

    def test_B9_forbidden_key_execute_in_output(self):
        bad_output = {"execute": "yes"}
        result = _BOUNDARY.post_tool_use_hook(
            "forecast_context", "emit_forecast_context", bad_output
        )
        self.assertFalse(result.passed)


class TestB10AllowedToolsContents(unittest.TestCase):

    def test_B10_all_allowed_shadow_tools_has_exactly_5_entries(self):
        self.assertEqual(len(ALL_ALLOWED_SHADOW_TOOLS), 5)

    def test_B10_all_5_expected_tools_present(self):
        expected = {
            "emit_forecast_context",
            "emit_source_reconciliation",
            "emit_contradiction_detection",
            "emit_regime_assessment",
            "emit_uncertainty_summary",
        }
        self.assertEqual(ALL_ALLOWED_SHADOW_TOOLS, expected)


class TestB11RegisteredSubagentIds(unittest.TestCase):

    def test_B11_registered_subagent_ids_has_exactly_5_entries(self):
        self.assertEqual(len(REGISTERED_SUBAGENT_IDS), 5)

    def test_B11_all_5_expected_ids_present(self):
        expected = {
            "forecast_context",
            "source_reconciliation",
            "contradiction_detection",
            "unusual_regime",
            "uncertainty_explanation",
        }
        self.assertEqual(REGISTERED_SUBAGENT_IDS, expected)


class TestB12AuthorityRegressionOnClient(unittest.TestCase):

    def test_B12_assert_inert_passes_on_base_class(self):
        try:
            KalshiWxShadowResearchClient.assert_inert()
        except AssertionError as e:
            self.fail(f"assert_inert() raised on base class: {e}")

    def test_B12_can_execute_is_false(self):
        self.assertIs(KalshiWxShadowResearchClient.CAN_EXECUTE, False)

    def test_B12_production_authority_is_false(self):
        self.assertIs(KalshiWxShadowResearchClient.PRODUCTION_AUTHORITY, False)

    def test_B12_user_output_authority_is_false(self):
        self.assertIs(KalshiWxShadowResearchClient.USER_OUTPUT_AUTHORITY, False)


class TestB13SubclassWithTrueConstantRejected(unittest.TestCase):

    def test_B13_assert_inert_raises_when_can_execute_true(self):
        class _Bad(KalshiWxShadowResearchClient):
            CAN_EXECUTE = True

        with self.assertRaises(AssertionError):
            _Bad.assert_inert()

    def test_B13_assert_inert_raises_when_production_authority_true(self):
        class _Bad(KalshiWxShadowResearchClient):
            PRODUCTION_AUTHORITY = True

        with self.assertRaises(AssertionError):
            _Bad.assert_inert()


class TestB14PerSubagentAllowlistSize(unittest.TestCase):

    def test_B14_each_subagent_has_exactly_one_allowed_tool(self):
        """
        Each subagent is permitted exactly ONE tool — no subagent has a superset.
        Verified by checking that each pair is unique and the map is 1:1.
        """
        tool_names_seen = set()
        for subagent_id, tool_name in _SUBAGENT_TOOL_PAIRS:
            self.assertNotIn(
                tool_name, tool_names_seen,
                f"Tool {tool_name!r} appears for more than one subagent",
            )
            tool_names_seen.add(tool_name)

            pre = _BOUNDARY.pre_tool_use_hook(subagent_id, tool_name, _CLEAN_INPUT)
            self.assertTrue(
                pre.allowed,
                f"Subagent {subagent_id!r} should be allowed tool {tool_name!r}",
            )

            # Any OTHER tool from the global list should be denied
            for _, other_tool in _SUBAGENT_TOOL_PAIRS:
                if other_tool == tool_name:
                    continue
                pre_other = _BOUNDARY.pre_tool_use_hook(subagent_id, other_tool, _CLEAN_INPUT)
                self.assertFalse(
                    pre_other.allowed,
                    f"Subagent {subagent_id!r} must NOT be allowed tool {other_tool!r}",
                )


if __name__ == "__main__":
    unittest.main()
