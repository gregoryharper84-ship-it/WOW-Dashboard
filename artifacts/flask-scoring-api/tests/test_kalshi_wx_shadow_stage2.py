"""
tests/test_kalshi_wx_shadow_stage2.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Stage 2 tests

Tests for gate_engine/kalshi_wx_shadow_subagents.py.

No live API calls.  All tests use mock clients.  No network access.

Test plan — Section SA (SubAgent)
──────────────────────────────────
SA1:  forecast_context subagent — valid mock response → SubagentResult.success=True,
      tool_input captured as a plain dict.
SA2:  source_reconciliation subagent — valid mock response → success=True.
SA3:  contradiction_detection subagent — valid mock response → success=True.
SA4:  unusual_regime subagent — valid mock response → success=True.
SA5:  uncertainty_explanation subagent — valid mock response → success=True.
SA6:  Pre-hook denial on a forbidden tool call → success=False,
      hook_violations recorded, failure_reason contains PRE_HOOK_DENIED.
SA7:  SDK exception during messages.create → success=False, SDK_ERROR in reason.
SA8:  Model ends turn with no tool call (stop_reason=end_turn, exhausted turns)
      → success=False, MAX_TURNS_EXCEEDED or NO_TOOL_CALL in reason.
SA9:  Pre-hook denies tool input containing a forbidden governance key.
SA10: Post-hook violation is recorded in hook_violations but does not block success.
SA11: SubagentResult.tool_input is always a plain dict (not a MagicMock object).
SA12: Flag-off regression — KalshiWxShadowResearchClient.research() still returns
      SHADOW_AGENT_DISABLED when the flag is off.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
from gate_engine.kalshi_wx_shadow_client import KalshiWxShadowResearchClient
from gate_engine.kalshi_wx_shadow_schema import ShadowValidationResult
from gate_engine.kalshi_wx_shadow_subagents import (
    SubagentResult,
    run_contradiction_detection_subagent,
    run_forecast_context_subagent,
    run_source_reconciliation_subagent,
    run_uncertainty_explanation_subagent,
    run_unusual_regime_subagent,
)

_BOUNDARY = CapabilityBoundary()
_CONTEXT  = {"city": "NYC", "date": "2026-08-08", "run_id": "run-stage2"}


# ── Module-level Gate B patch ─────────────────────────────────────────────────
# SA1–SA11 call run_*_subagent() and _run_single_tool_subagent() directly with
# mock SDK clients — they legitimately need to reach messages.create().
# Gate B (_RESEARCH_API_ENABLED) would block them without this patch.
# SA12 tests KalshiWxShadowResearchClient.research() (Gate 1 only) and is
# unaffected by this module-level patch.
_patch_research_enabled = patch(
    "gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True
)

def setUpModule() -> None:   # noqa: N802
    _patch_research_enabled.start()

def tearDownModule() -> None:  # noqa: N802
    _patch_research_enabled.stop()


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_tool_use_mock(tool_name: str, tool_input: dict) -> MagicMock:
    """Return a mock Anthropic client whose first messages.create() call
    returns a response with one tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = tool_name
    tool_block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [tool_block]

    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _make_end_turn_mock() -> MagicMock:
    """Return a mock that always returns end_turn with no tool_use blocks."""
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = []

    client = MagicMock()
    client.messages.create.return_value = response
    return client


def _make_exception_mock(exc: Exception) -> MagicMock:
    """Return a mock whose messages.create raises the given exception."""
    client = MagicMock()
    client.messages.create.side_effect = exc
    return client


# ── SA1: forecast_context ────────────────────────────────────────────────────

class TestSA1ForecastContext(unittest.TestCase):

    def setUp(self):
        self._input = {
            "scoring_mode": "gaussian_forecast",
            "calibration_status": "UNAVAILABLE",
            "uncertainty_tier": "HIGH",
            "recommended_ceiling": "KALSHI_WATCH",
            "blockers": [],
        }
        self._client = _make_tool_use_mock("emit_forecast_context", self._input)

    def test_SA1_success_true(self):
        result = run_forecast_context_subagent(self._client, _CONTEXT, _BOUNDARY)
        self.assertIsInstance(result, SubagentResult)
        self.assertTrue(result.success)

    def test_SA1_tool_input_captured(self):
        result = run_forecast_context_subagent(self._client, _CONTEXT, _BOUNDARY)
        self.assertEqual(result.tool_input, self._input)

    def test_SA1_tool_input_is_plain_dict(self):
        result = run_forecast_context_subagent(self._client, _CONTEXT, _BOUNDARY)
        self.assertIsInstance(result.tool_input, dict)
        self.assertNotIsInstance(result.tool_input, MagicMock)

    def test_SA1_no_hook_violations(self):
        result = run_forecast_context_subagent(self._client, _CONTEXT, _BOUNDARY)
        self.assertEqual(result.hook_violations, [])

    def test_SA1_subagent_id_correct(self):
        result = run_forecast_context_subagent(self._client, _CONTEXT, _BOUNDARY)
        self.assertEqual(result.subagent_id, "forecast_context")


# ── SA2: source_reconciliation ────────────────────────────────────────────────

class TestSA2SourceReconciliation(unittest.TestCase):

    def test_SA2_success_true(self):
        inp = {
            "sources_present": ["nws_forecast"],
            "sources_missing": [],
            "conflicts": [],
            "reconciliation_status": "OK",
        }
        client = _make_tool_use_mock("emit_source_reconciliation", inp)
        result = run_source_reconciliation_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertTrue(result.success)
        self.assertEqual(result.tool_input, inp)
        self.assertEqual(result.subagent_id, "source_reconciliation")


# ── SA3: contradiction_detection ──────────────────────────────────────────────

class TestSA3ContradictionDetection(unittest.TestCase):

    def test_SA3_success_with_no_contradictions(self):
        inp = {"contradictions_found": [], "ceiling_impacted": False}
        client = _make_tool_use_mock("emit_contradiction_detection", inp)
        result = run_contradiction_detection_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertTrue(result.success)
        self.assertEqual(result.tool_input, inp)

    def test_SA3_success_with_revised_ceiling(self):
        inp = {
            "contradictions_found": ["source_A_vs_source_B"],
            "ceiling_impacted": True,
            "revised_ceiling": "KALSHI_REJECT_NO_EDGE",
        }
        client = _make_tool_use_mock("emit_contradiction_detection", inp)
        result = run_contradiction_detection_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertTrue(result.success)
        self.assertEqual(result.tool_input["revised_ceiling"], "KALSHI_REJECT_NO_EDGE")

    def test_SA3_receives_prior_results(self):
        """Confirm the function signature accepts optional prior result kwargs."""
        fc = SubagentResult(
            subagent_id="forecast_context",
            tool_name="emit_forecast_context",
            tool_input={"recommended_ceiling": "KALSHI_WATCH"},
            hook_violations=[],
            success=True,
        )
        inp = {"contradictions_found": [], "ceiling_impacted": False}
        client = _make_tool_use_mock("emit_contradiction_detection", inp)
        result = run_contradiction_detection_subagent(
            client, _CONTEXT, _BOUNDARY,
            forecast_context=fc,
            source_reconciliation=None,
        )
        self.assertTrue(result.success)


# ── SA4: unusual_regime ───────────────────────────────────────────────────────

class TestSA4UnusualRegime(unittest.TestCase):

    def test_SA4_success_regime_not_unusual(self):
        inp = {
            "regime_unusual": False,
            "regime_factors": [],
            "reliability_impact": "NONE",
        }
        client = _make_tool_use_mock("emit_regime_assessment", inp)
        result = run_unusual_regime_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertTrue(result.success)
        self.assertEqual(result.subagent_id, "unusual_regime")

    def test_SA4_success_regime_is_unusual(self):
        inp = {
            "regime_unusual": True,
            "regime_factors": ["heat_dome", "above_normal_dewpoint"],
            "reliability_impact": "SIGNIFICANT",
        }
        client = _make_tool_use_mock("emit_regime_assessment", inp)
        result = run_unusual_regime_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertTrue(result.success)
        self.assertEqual(len(result.tool_input["regime_factors"]), 2)


# ── SA5: uncertainty_explanation ──────────────────────────────────────────────

class TestSA5UncertaintyExplanation(unittest.TestCase):

    def test_SA5_success(self):
        inp = {
            "uncertainty_tier": "HIGH",
            "uncertainty_sources": ["forecast_horizon", "model_spread"],
            "ceiling_impact": "MODERATE",
        }
        client = _make_tool_use_mock("emit_uncertainty_summary", inp)
        result = run_uncertainty_explanation_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertTrue(result.success)
        self.assertEqual(result.subagent_id, "uncertainty_explanation")

    def test_SA5_with_numeric_fields(self):
        inp = {
            "uncertainty_tier": "MODERATE",
            "uncertainty_sources": ["model_spread"],
            "ceiling_impact": "MINOR",
            "sigma_f_estimate": 3.5,
            "horizon_hours_estimate": 36.0,
        }
        client = _make_tool_use_mock("emit_uncertainty_summary", inp)
        result = run_uncertainty_explanation_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.tool_input["sigma_f_estimate"], 3.5)


# ── SA6: pre-hook denial on forbidden tool name ───────────────────────────────

class TestSA6PreHookDenialForbiddenTool(unittest.TestCase):

    def test_SA6_mock_calls_wrong_tool_pre_hook_denies(self):
        """
        Mock client returns a tool_use block with a tool name that is not in
        the forecast_context subagent's allowlist.  Pre-hook should deny it.
        """
        # Model returns emit_source_reconciliation — wrong for forecast_context
        client = _make_tool_use_mock("emit_source_reconciliation", {})
        result = run_forecast_context_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertFalse(result.success)
        self.assertIn("PRE_HOOK_DENIED", result.failure_reason)
        self.assertEqual(len(result.hook_violations), 1)
        self.assertEqual(result.hook_violations[0]["stage"], "pre")
        self.assertIn("TOOL_NOT_ALLOWED", result.hook_violations[0]["reason"])


# ── SA7: SDK exception ────────────────────────────────────────────────────────

class TestSA7SdkException(unittest.TestCase):

    def test_SA7_runtime_error_caught_returns_failure(self):
        client = _make_exception_mock(RuntimeError("connection refused"))
        result = run_forecast_context_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertFalse(result.success)
        self.assertIn("SDK_ERROR", result.failure_reason)
        self.assertIn("RuntimeError", result.failure_reason)

    def test_SA7_timeout_error_caught(self):
        client = _make_exception_mock(TimeoutError("request timed out"))
        result = run_source_reconciliation_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertFalse(result.success)
        self.assertIn("SDK_ERROR", result.failure_reason)


# ── SA8: no tool call produced ────────────────────────────────────────────────

class TestSA8NoToolCall(unittest.TestCase):

    def test_SA8_end_turn_with_no_tool_use_exhausts_turns_and_fails(self):
        """
        Model returns end_turn without a tool_use block on every turn.
        After max_turns, the loop returns a failure.
        """
        client = _make_end_turn_mock()
        # Use a minimal max_turns to keep the test fast
        from gate_engine.kalshi_wx_shadow_subagents import _run_single_tool_subagent
        from gate_engine.kalshi_wx_shadow_subagents import _FC_TOOL_DEF, _FC_SYSTEM_PROMPT

        result = _run_single_tool_subagent(
            client=client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
            max_turns=2,
        )
        self.assertFalse(result.success)
        self.assertTrue(
            "MAX_TURNS_EXCEEDED" in result.failure_reason
            or "NO_TOOL_CALL" in result.failure_reason
        )


# ── SA9: pre-hook rejects forbidden governance key in tool input ──────────────

class TestSA9ForbiddenKeyInInput(unittest.TestCase):

    def test_SA9_terminal_label_in_tool_input_denied_by_pre_hook(self):
        bad_input = {
            "scoring_mode": "gaussian_forecast",
            "terminal_label": "KALSHI_WATCH",   # FORBIDDEN
            "calibration_status": "UNAVAILABLE",
            "uncertainty_tier": "HIGH",
            "recommended_ceiling": "KALSHI_WATCH",
            "blockers": [],
        }
        client = _make_tool_use_mock("emit_forecast_context", bad_input)
        result = run_forecast_context_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertFalse(result.success)
        self.assertIn("PRE_HOOK_DENIED", result.failure_reason)
        self.assertEqual(len(result.hook_violations), 1)
        self.assertIn("FORBIDDEN_KEY_IN_TOOL_INPUT", result.hook_violations[0]["reason"])


# ── SA10: post-hook violation recorded but does not block success ─────────────

class TestSA10PostHookViolationRecorded(unittest.TestCase):
    """
    Pre-hook and post-hook scan the same dict for the same forbidden keys, so a
    forbidden key in the model's tool input is always caught by the pre-hook first
    inside _run_single_tool_subagent.  The post-hook's own interface is therefore
    tested directly against CapabilityBoundary, and its non-blocking contract is
    verified by patching post_tool_use_hook to return a failure on a clean dict.
    """

    def test_SA10_post_hook_returns_failure_on_forbidden_key(self):
        """
        post_tool_use_hook itself returns passed=False when the output dict
        contains a forbidden governance key.
        """
        bad_output = {
            "scoring_mode": "gaussian_forecast",
            "calibration_status": "UNAVAILABLE",
            "uncertainty_tier": "HIGH",
            "recommended_ceiling": "KALSHI_WATCH",
            "blockers": [],
            "label": "SOME_LABEL",  # FORBIDDEN
        }
        post = _BOUNDARY.post_tool_use_hook(
            "forecast_context", "emit_forecast_context", bad_output
        )
        self.assertFalse(post.passed)
        self.assertIn("FORBIDDEN_KEY_IN_TOOL_OUTPUT", post.reason)

    def test_SA10_post_hook_failure_is_non_blocking_in_subagent(self):
        """
        When post_tool_use_hook returns passed=False, the subagent loop still
        records success=True (non-blocking) and logs the violation in hook_violations.
        We patch the boundary's post hook to always return a failure so we can
        exercise this path with a clean tool input that passes the pre-hook.
        """
        from gate_engine.kalshi_wx_shadow_capability_boundary import (
            PostHookResult,
        )

        # Clean tool input — pre-hook will allow it.
        clean_output = {
            "scoring_mode": "gaussian_forecast",
            "calibration_status": "UNAVAILABLE",
            "uncertainty_tier": "HIGH",
            "recommended_ceiling": "KALSHI_WATCH",
            "blockers": [],
        }
        client = _make_tool_use_mock("emit_forecast_context", clean_output)

        # Patch post_tool_use_hook on the shared boundary to simulate a violation.
        original = _BOUNDARY.post_tool_use_hook
        try:
            _BOUNDARY.post_tool_use_hook = lambda sa, tn, out: PostHookResult(
                passed=False,
                reason="FORBIDDEN_KEY_IN_TOOL_OUTPUT:injected_by_test",
            )
            result = run_forecast_context_subagent(client, _CONTEXT, _BOUNDARY)
        finally:
            _BOUNDARY.post_tool_use_hook = original  # restore

        # Post-hook failure is non-blocking — success is still True
        self.assertTrue(result.success)

        # The violation is recorded in hook_violations
        post_violations = [v for v in result.hook_violations if v["stage"] == "post"]
        self.assertEqual(len(post_violations), 1)
        self.assertIn("FORBIDDEN_KEY_IN_TOOL_OUTPUT", post_violations[0]["reason"])


# ── SA11: tool_input is always a plain dict ───────────────────────────────────

class TestSA11ToolInputPlainDict(unittest.TestCase):

    def test_SA11_tool_input_is_plain_dict_not_mock(self):
        inp = {"regime_unusual": False, "regime_factors": [], "reliability_impact": "NONE"}
        client = _make_tool_use_mock("emit_regime_assessment", inp)
        result = run_unusual_regime_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertIsInstance(result.tool_input, dict)
        self.assertNotIsInstance(result.tool_input, MagicMock)

    def test_SA11_tool_input_is_empty_dict_on_failure(self):
        client = _make_exception_mock(RuntimeError("sdk fail"))
        result = run_forecast_context_subagent(client, _CONTEXT, _BOUNDARY)
        self.assertFalse(result.success)
        self.assertEqual(result.tool_input, {})
        self.assertIsInstance(result.tool_input, dict)


# ── SA12: flag-off regression ─────────────────────────────────────────────────

class TestSA12FlagOffRegression(unittest.TestCase):

    def test_SA12_flag_off_returns_shadow_agent_disabled(self):
        """
        Regression: KalshiWxShadowResearchClient.research() must still return
        SHADOW_AGENT_DISABLED when the flag is off, even after wiring.
        """
        strict_mock = MagicMock()
        strict_mock.messages.create.side_effect = AssertionError("must not be called")

        with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", False):
            client = KalshiWxShadowResearchClient(sdk_client=strict_mock)
            result = client.research(city="NYC", date="2026-08-08", run_id="regression-sa12")

        self.assertIsInstance(result, ShadowValidationResult)
        self.assertFalse(result.passed)
        self.assertTrue(result.shadow_failure_only)
        self.assertIn("SHADOW_AGENT_DISABLED", result.failure_reason)
        strict_mock.messages.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
