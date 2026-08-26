"""
tests/test_kalshi_wx_shadow_b2.py
Step 12.5B2 — Research-only authorization gate (Gate A + Gate B) and real
usage accounting.

Test requirements (8):
  T1  Gate A: flag unset/false → call_one_agent returns SHADOW_RESEARCH_API_DISABLED
              without dispatching any subagent; messages.create never called.
  T2  Gate B: _RESEARCH_API_ENABLED=False → _run_single_tool_subagent returns
              RESEARCH_API_DISABLED; messages.create never called.
  T3  Flag=true + mocked SDK → messages.create IS invoked; max_output_tokens
              threaded correctly.
  T4  Structural: SHADOW_RESEARCH_API_ENABLED not in kalshi_wx_shadow_client.py;
              CAN_EXECUTE / PRODUCTION_AUTHORITY / USER_OUTPUT_AUTHORITY still False.
  T5  AVAILABLE path: usage.input_tokens/output_tokens propagate exactly;
              pilot runner uses real counts for row cost.
  T6  UNAVAILABLE path: missing usage → tokens and cost on row are None, not 0.
  T7  SHADOW_RESEARCH_API_ENABLED not in app.py.
  T8  Pre-call worst-case budget check fires before every call regardless.

All tests use mocked SDK clients.  Zero real Anthropic API calls are made.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Project path setup ─────────────────────────────────────────────────────────
_REPO        = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO / "scripts"

if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
from gate_engine.kalshi_wx_shadow_subagents import (
    _FC_SYSTEM_PROMPT,
    _FC_TOOL_DEF,
    _run_single_tool_subagent,
)
from run_kalshi_wx_shadow_pilot import (
    call_one_agent,
    run_pilot,
)

# ── Shared fixtures ─────────────────────────────────────────────────────────────

_MOCK_CONFIG = {
    "PILOT_BUDGET_USD":           100.0,
    "INPUT_PRICE_PER_TOKEN":      0.000001,   # $1 per 1M input tokens
    "OUTPUT_PRICE_PER_TOKEN":     0.000005,   # $5 per 1M output tokens
    "MAX_OUTPUT_TOKENS_PER_CALL": 1024,
}

_BOUNDARY = CapabilityBoundary()

_FC_VALID_INPUT = {
    "scoring_mode":        "gaussian_forecast",
    "calibration_status":  "UNAVAILABLE",
    "uncertainty_tier":    "HIGH",
    "recommended_ceiling": "KALSHI_WATCH",
    "blockers":            [],
}


def _make_snap(rsid: str) -> dict:
    """Minimal eligible-snapshot dict (same shape as fetch_eligible_snapshots returns)."""
    return {
        "research_snapshot_id": rsid,
        "snapshot_json": {
            "research_snapshot_id": rsid,
            "city":                   "NYC",
            "station":                "KNYC",
            "market_date":            "2026-08-15",
            "forecast_high":          85.0,
            "weather_data_source_tier": "nws_primary",
            "sigma_f":                3.5,
            "horizon_hours":          18.0,
        },
        "terminal_label":         "KALSHI_WATCH",
        "price_gate_disposition": "DRY_RUN_ONLY",
        "can_execute":            False,
    }


def _make_tool_response(tool_name: str, tool_input: dict) -> MagicMock:
    """Mock response with a tool_use block and usage=None."""
    tool_block = MagicMock()
    tool_block.type  = "tool_use"
    tool_block.name  = tool_name
    tool_block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content     = [tool_block]
    response.usage       = None   # UNAVAILABLE by default
    return response


def _make_tool_response_with_usage(
    tool_name: str, tool_input: dict,
    input_tokens: int, output_tokens: int,
) -> MagicMock:
    """Mock response with explicit real integer usage counts → AVAILABLE."""
    usage = MagicMock()
    usage.input_tokens  = input_tokens    # real int: isinstance(int) → True
    usage.output_tokens = output_tokens   # real int

    response = _make_tool_response(tool_name, tool_input)
    response.usage = usage
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# T1 — Gate A: flag unset/false → call_one_agent blocked
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2GateA(unittest.TestCase):
    """
    Gate A lives in call_one_agent() and reads os.environ live on each call.
    When SHADOW_RESEARCH_API_ENABLED is absent or not 'true', no subagent
    function is dispatched and messages.create() is never called.
    """

    def _snap_json(self, rsid: str) -> dict:
        return _make_snap(rsid)["snapshot_json"]

    @patch.dict(os.environ, {"SHADOW_RESEARCH_API_ENABLED": "false"})
    def test_B2T1_gate_a_flag_false_returns_disabled_reason(self):
        result = call_one_agent(
            "forecast_context",
            self._snap_json("rsid-ga-t1a"),
            {}, "run-ga-t1a", MagicMock(), _BOUNDARY,
        )
        self.assertFalse(result["success"])
        self.assertIn("SHADOW_RESEARCH_API_DISABLED", result["failure_reason"])
        self.assertEqual(result["usage_accounting_status"], "UNAVAILABLE")
        self.assertIsNone(result["input_tokens"])
        self.assertIsNone(result["output_tokens"])

    @patch.dict(os.environ, {"SHADOW_RESEARCH_API_ENABLED": "false"})
    def test_B2T1_gate_a_sdk_create_never_called(self):
        mock_sdk = MagicMock()
        call_one_agent(
            "forecast_context",
            self._snap_json("rsid-ga-t1b"),
            {}, "run-ga-t1b", mock_sdk, _BOUNDARY,
        )
        mock_sdk.messages.create.assert_not_called()

    def test_B2T1_gate_a_flag_absent_defaults_to_disabled(self):
        """Flag absent → default 'false' → Gate A fires."""
        env_without = {
            k: v for k, v in os.environ.items()
            if k != "SHADOW_RESEARCH_API_ENABLED"
        }
        with patch.dict(os.environ, env_without, clear=True):
            result = call_one_agent(
                "forecast_context",
                self._snap_json("rsid-ga-t1c"),
                {}, "run-ga-t1c", MagicMock(), _BOUNDARY,
            )
        self.assertFalse(result["success"])
        self.assertIn("SHADOW_RESEARCH_API_DISABLED", result["failure_reason"])


# ═══════════════════════════════════════════════════════════════════════════════
# T2 — Gate B: _RESEARCH_API_ENABLED=False → _run_single_tool_subagent blocked
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2GateB(unittest.TestCase):
    """
    Gate B lives in _run_single_tool_subagent() and checks the module-level
    _RESEARCH_API_ENABLED bool (set once from the env var at import time;
    patchable in tests).  Independent of Gate A.
    """

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", False)
    def test_B2T2_gate_b_flag_false_returns_disabled(self):
        result = _run_single_tool_subagent(
            client=MagicMock(),
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )
        self.assertFalse(result.success)
        self.assertIn("RESEARCH_API_DISABLED", result.failure_reason)
        self.assertEqual(result.usage_accounting_status, "UNAVAILABLE")
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.output_tokens)

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", False)
    def test_B2T2_gate_b_sdk_create_never_called(self):
        mock_client = MagicMock()
        _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )
        mock_client.messages.create.assert_not_called()

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", False)
    def test_B2T2_gate_b_independent_of_gate_a_env_var(self):
        """
        Gate B fires regardless of the env var state — it checks the module
        bool, which is patched independently of os.environ.
        """
        with patch.dict(os.environ, {"SHADOW_RESEARCH_API_ENABLED": "true"}):
            # Even with env var = true, Gate B fires when the cached bool is False
            result = _run_single_tool_subagent(
                client=MagicMock(),
                subagent_id="forecast_context",
                tool_def=_FC_TOOL_DEF,
                system_prompt=_FC_SYSTEM_PROMPT,
                user_message="test",
                capability_boundary=_BOUNDARY,
            )
        self.assertFalse(result.success)
        self.assertIn("RESEARCH_API_DISABLED", result.failure_reason)


# ═══════════════════════════════════════════════════════════════════════════════
# T3 — Both gates open: messages.create IS invoked; max_output_tokens threaded
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2FlagTrueMockSdk(unittest.TestCase):
    """When both gates are open, messages.create() must be called."""

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_B2T3_flag_true_sdk_create_is_called(self):
        response = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        result = _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )

        mock_client.messages.create.assert_called_once()
        self.assertTrue(result.success)

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_B2T3_max_output_tokens_threaded_to_sdk_call(self):
        """max_output_tokens travels from caller → subagent → messages.create."""
        response = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
            max_output_tokens=512,
        )

        call_kwargs = mock_client.messages.create.call_args
        actual_max_tokens = call_kwargs.kwargs.get("max_tokens")
        self.assertEqual(actual_max_tokens, 512,
                         f"max_output_tokens=512 not threaded to SDK — got {actual_max_tokens}")


# ═══════════════════════════════════════════════════════════════════════════════
# T4 — Structural: authority constants must remain untouched
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2AuthorityStructural(unittest.TestCase):
    """
    SHADOW_RESEARCH_API_ENABLED must NOT appear in kalshi_wx_shadow_client.py
    (which holds trading-authority constants).  CAN_EXECUTE et al. must remain False.
    """

    def test_B2T4_shadow_research_api_enabled_not_in_client_module(self):
        client_path = _REPO / "gate_engine" / "kalshi_wx_shadow_client.py"
        client_src  = client_path.read_text()
        self.assertNotIn(
            "SHADOW_RESEARCH_API_ENABLED",
            client_src,
            "SHADOW_RESEARCH_API_ENABLED must not appear in "
            "kalshi_wx_shadow_client.py — that file holds trading-authority "
            "constants and must not be mixed with research-call authorization",
        )

    def test_B2T4_can_execute_still_false(self):
        from gate_engine.kalshi_wx_shadow_client import KalshiWxShadowResearchClient
        self.assertFalse(KalshiWxShadowResearchClient.CAN_EXECUTE)
        self.assertFalse(KalshiWxShadowResearchClient.PRODUCTION_AUTHORITY)
        self.assertFalse(KalshiWxShadowResearchClient.USER_OUTPUT_AUTHORITY)


# ═══════════════════════════════════════════════════════════════════════════════
# T5 — AVAILABLE path: real token counts propagate; pilot uses them for row cost
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2UsageAccountingAvailable(unittest.TestCase):

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_B2T5_real_usage_propagates_to_subagent_result_fields(self):
        """
        When response.usage.input_tokens/output_tokens are real ints,
        SubagentResult must carry them exactly with status=AVAILABLE.
        """
        response = _make_tool_response_with_usage(
            "emit_forecast_context", _FC_VALID_INPUT,
            input_tokens=1500, output_tokens=340,
        )
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        result = _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.usage_accounting_status, "AVAILABLE")
        self.assertEqual(result.input_tokens,  1500)
        self.assertEqual(result.output_tokens,  340)

    def test_B2T5_available_usage_row_cost_uses_real_counts(self):
        """
        When the mock call_agent_fn returns AVAILABLE with input=1500/output=340,
        the persisted row must have those exact counts and the correct derived cost.
        """
        writes: list = []
        snap = _make_snap("rsid-avail-cost")

        expected_cost = (
            1500 * _MOCK_CONFIG["INPUT_PRICE_PER_TOKEN"]
            + 340 * _MOCK_CONFIG["OUTPUT_PRICE_PER_TOKEN"]
        )

        def avail_call(agent_id, snap_json, prior, run_id, sdk, cap, **kwargs):
            return {
                "success":                 True,
                "tool_input":              {},
                "failure_reason":          None,
                "latency_ms":              50,
                "model":                   None,
                "input_tokens":            1500,
                "output_tokens":           340,
                "usage_accounting_status": "AVAILABLE",
            }

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=[snap]):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                       return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results",
                           return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row",
                               side_effect=lambda conn, **kw: writes.append(kw)):
                        run_pilot(_MOCK_CONFIG, MagicMock(),
                                  call_agent_fn=avail_call)

        avail_rows = [w for w in writes if w.get("input_tokens") == 1500]
        self.assertTrue(
            len(avail_rows) > 0,
            f"Expected at least one row with input_tokens=1500; all rows: {writes}",
        )
        row = avail_rows[0]
        self.assertEqual(row["output_tokens"], 340)
        self.assertAlmostEqual(row["estimated_cost_usd"], expected_cost, places=8)
        self.assertEqual(
            row["validated_output_json"]["usage_accounting_status"], "AVAILABLE",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T6 — UNAVAILABLE path: missing usage → row values are None, not 0
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2UsageAccountingUnavailable(unittest.TestCase):

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_B2T6_no_usage_gives_unavailable_and_none_not_zero(self):
        """
        When response.usage=None, SubagentResult must have status=UNAVAILABLE
        and input_tokens/output_tokens=None — never 0.
        """
        response = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        # response.usage is already None from _make_tool_response
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        result = _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.usage_accounting_status, "UNAVAILABLE")
        self.assertIsNone(result.input_tokens,
                          "input_tokens must be None (not 0) when UNAVAILABLE")
        self.assertIsNone(result.output_tokens,
                          "output_tokens must be None (not 0) when UNAVAILABLE")
        # Explicitly guard the "silently zero-cost" failure mode
        self.assertNotEqual(result.input_tokens,  0,
                            "UNAVAILABLE must not be represented as 0 input tokens")
        self.assertNotEqual(result.output_tokens, 0,
                            "UNAVAILABLE must not be represented as 0 output tokens")

    def test_B2T6_unavailable_row_values_are_none_not_zero(self):
        """
        When the call_agent_fn returns UNAVAILABLE, write_result_row must
        receive input_tokens=None, output_tokens=None, estimated_cost_usd=None.
        """
        writes: list = []
        snap = _make_snap("rsid-unavail-none")

        def unavail_call(agent_id, snap_json, prior, run_id, sdk, cap, **kwargs):
            return {
                "success":                 True,
                "tool_input":              {},
                "failure_reason":          None,
                "latency_ms":              50,
                "model":                   None,
                "input_tokens":            None,
                "output_tokens":           None,
                "usage_accounting_status": "UNAVAILABLE",
            }

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=[snap]):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                       return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results",
                           return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row",
                               side_effect=lambda conn, **kw: writes.append(kw)):
                        run_pilot(_MOCK_CONFIG, MagicMock(),
                                  call_agent_fn=unavail_call)

        unavail_rows = [
            w for w in writes
            if w["validated_output_json"].get("usage_accounting_status") == "UNAVAILABLE"
        ]
        self.assertTrue(
            len(unavail_rows) > 0,
            f"Expected at least one UNAVAILABLE row; all writes: {writes}",
        )
        row = unavail_rows[0]
        self.assertIsNone(row["input_tokens"],
                          "Row input_tokens must be None when UNAVAILABLE, not 0")
        self.assertIsNone(row["output_tokens"],
                          "Row output_tokens must be None when UNAVAILABLE, not 0")
        self.assertIsNone(row["estimated_cost_usd"],
                          "Row estimated_cost_usd must be None when UNAVAILABLE, not 0")


# ═══════════════════════════════════════════════════════════════════════════════
# T7 — SHADOW_RESEARCH_API_ENABLED must not appear in app.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2AppPyIsolation(unittest.TestCase):
    """The research-call gate env var must not leak into the Flask app."""

    def test_B2T7_shadow_research_api_enabled_not_in_app_py(self):
        app_src = (_REPO / "app.py").read_text()
        self.assertNotIn(
            "SHADOW_RESEARCH_API_ENABLED",
            app_src,
            "SHADOW_RESEARCH_API_ENABLED must not appear in app.py — it belongs "
            "only in the pilot runner script and kalshi_wx_shadow_subagents.py",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T8 — Pre-call worst-case budget check is unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestB2PreCallBudgetCheckUnchanged(unittest.TestCase):
    """
    The pessimistic pre-call worst-case budget check must fire BEFORE every
    individual call and must not be weakened by the new post-call accounting.
    A zero budget must stop the runner before the first call_agent_fn invocation.
    """

    def test_B2T8_zero_budget_refuses_before_any_call(self):
        snaps  = [_make_snap("rsid-budget-b2")]
        called: list = []

        def tracking_call(agent_id, snap_json, prior, run_id, sdk, cap, **kwargs):
            called.append(agent_id)
            return {
                "success":                 True,
                "tool_input":              {},
                "failure_reason":          None,
                "latency_ms":              10,
                "model":                   None,
                "input_tokens":            100,
                "output_tokens":            50,
                "usage_accounting_status": "AVAILABLE",
            }

        zero_budget = dict(_MOCK_CONFIG, PILOT_BUDGET_USD=0.0)

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                       return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results",
                           return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(
                            zero_budget, MagicMock(),
                            call_agent_fn=tracking_call,
                        )

        self.assertEqual(summary["stop_reason"], "BUDGET_EXCEEDED",
                         f"Expected BUDGET_EXCEEDED but got: {summary['stop_reason']}")
        self.assertEqual(
            len(called), 0,
            f"Pre-call budget check must fire before call_agent_fn — "
            f"but got {len(called)} invocation(s): {called}",
        )

    def test_B2T8_ample_budget_allows_all_calls(self):
        """
        Positive case: with a generous budget, the pre-call worst-case check
        must NOT block any calls.  All 5 agents for the single snapshot must
        run, proving the guard does not over-fire.
        """
        snaps  = [_make_snap("rsid-ample-b2")]
        called: list = []

        def tracking_call(agent_id, snap_json, prior, run_id, sdk, cap, **kwargs):
            called.append(agent_id)
            return {
                "success":                 True,
                "tool_input":              {},
                "failure_reason":          None,
                "latency_ms":              10,
                "model":                   None,
                "input_tokens":            None,
                "output_tokens":           None,
                "usage_accounting_status": "UNAVAILABLE",
            }

        ample_config = dict(_MOCK_CONFIG, PILOT_BUDGET_USD=1000.0)

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                       return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results",
                           return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(
                            ample_config, MagicMock(),
                            call_agent_fn=tracking_call,
                        )

        # All 5 agents for the 1 snapshot must have run
        self.assertEqual(
            len(called), 5,
            f"Expected 5 calls (5 agents × 1 snapshot); got {len(called)}: {called}",
        )
        self.assertNotEqual(
            summary["stop_reason"], "BUDGET_EXCEEDED",
            "Ample budget must not trigger BUDGET_EXCEEDED",
        )


if __name__ == "__main__":
    unittest.main()
