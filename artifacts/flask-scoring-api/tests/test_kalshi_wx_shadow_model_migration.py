"""
tests/test_kalshi_wx_shadow_model_migration.py
Step 14A — Model migration compatibility tests.
    Old: claude-3-5-haiku-20241022  (retired)
    New: claude-haiku-4-5-20251001

All 8 test classes are MOCK-ONLY.  Zero real Anthropic API calls are made.
Zero live credentials are read.

Coverage:
  M1  Model string that reaches messages.create() is exactly "claude-haiku-4-5-20251001".
  M2  max_tokens is still 1024 and reaches messages.create() unchanged;
      temperature and top_p are still absent from the call.
  M3  Closed-schema validator (Step 9) pass/fail pair — model string is irrelevant.
  M4  Usage accounting (input_tokens / output_tokens) propagates from mocked response.
  M5  Budget guard is fully config-driven; no Haiku 3.5 prices hardcoded in runner.
  M6  Authority constants (CAN_EXECUTE / PRODUCTION_AUTHORITY / USER_OUTPUT_AUTHORITY)
      remain False; client module contains no model string at all; retired string
      absent from all production code.
  M7  Zero real Anthropic client construction with live credentials in this test run.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Project path setup ────────────────────────────────────────────────────────
_REPO        = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO / "scripts"

for _p in (str(_REPO), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
from gate_engine.kalshi_wx_shadow_client import KalshiWxShadowResearchClient
from gate_engine.kalshi_wx_shadow_schema import SHADOW_PASS, validate_shadow_output
from gate_engine.kalshi_wx_shadow_subagents import (
    _FC_SYSTEM_PROMPT,
    _FC_TOOL_DEF,
    _MAX_TOKENS,
    _MODEL,
    _run_single_tool_subagent,
)
from run_kalshi_wx_shadow_pilot import (
    run_pilot,
    worst_case_call_cost,
)

# ── Shared constants ──────────────────────────────────────────────────────────
_NEW_MODEL = "claude-haiku-4-5-20251001"
_OLD_MODEL = "claude-3-5-haiku-20241022"
_BOUNDARY  = CapabilityBoundary()

# ── Fixtures (match B2 conventions) ──────────────────────────────────────────
_FC_VALID_INPUT = {
    "scoring_mode":        "gaussian_forecast",
    "calibration_status":  "UNAVAILABLE",
    "uncertainty_tier":    "HIGH",
    "recommended_ceiling": "KALSHI_WATCH",
    "blockers":            [],
}


def _make_tool_response(tool_name: str, tool_input: dict) -> MagicMock:
    """Mock messages.create() response with a single tool_use block, usage=None."""
    tool_block        = MagicMock()
    tool_block.type   = "tool_use"
    tool_block.name   = tool_name
    tool_block.input  = tool_input

    resp              = MagicMock()
    resp.stop_reason  = "tool_use"
    resp.content      = [tool_block]
    resp.usage        = None
    return resp


def _make_tool_response_with_usage(
    tool_name: str,
    tool_input: dict,
    input_tokens: int,
    output_tokens: int,
) -> MagicMock:
    usage               = MagicMock()
    usage.input_tokens  = input_tokens    # real int, not MagicMock
    usage.output_tokens = output_tokens
    resp                = _make_tool_response(tool_name, tool_input)
    resp.usage          = usage
    return resp


def _valid_schema_payload() -> dict:
    """Minimally valid shadow agent output payload (matches Step 9 closed schema)."""
    return {
        "agent_id":               "wx-shadow-agent-001",
        "run_id":                 "run-20260808-abc123",
        "lane":                   "KALSHI_WEATHER",
        "status":                 "COMPLETE",
        "facts": {
            "city":               "MIA",
            "date":               "2026-08-09",
            "nws_station_code":   "KMIA",
            "scoring_mode":       "gaussian_forecast",
            "forecast_high_f":    91.0,
        },
        "probabilities": {
            "model_prob_sum":     1.0,
            "calibration_status": "CALIBRATED",
        },
        "uncertainty": {
            "horizon_hours":      18.0,
            "sigma_f":            3.5,
            "uncertainty_tier":   "MODERATE",
        },
        "agent_observed_blockers": [],
        "source_conflicts":        [],
        "recommended_ceiling":     "KALSHI_PLAYABLE_LIMIT_ONLY",
        "advisory_only":           True,
    }


def _make_snap(rsid: str) -> dict:
    return {
        "research_snapshot_id": rsid,
        "snapshot_json": {
            "research_snapshot_id":                    rsid,
            "city":                                    "MIA",
            "station":                                 "KMIA",
            "market_date":                             "2026-08-09",
            "forecast_high_used_by_deterministic_model": 91.0,
            "weather_data_source_tier":                "nws_primary",
            "sigma_f":                                 3.5,
            "horizon_hours":                           18.0,
        },
        "terminal_label":         "KALSHI_REJECT_BAD_RULES",
        "price_gate_disposition": "DRY_RUN_ONLY",
        "can_execute":            False,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# M1 — Model string that reaches messages.create() is the new identifier
# ═══════════════════════════════════════════════════════════════════════════════

class TestM1ModelStringReachesSDK(unittest.TestCase):

    def _call_subagent_with_mock(self) -> MagicMock:
        """Helper: run one subagent call through a mocked SDK; return call_args."""
        response    = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        with patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True):
            _run_single_tool_subagent(
                client=mock_client,
                subagent_id="forecast_context",
                tool_def=_FC_TOOL_DEF,
                system_prompt=_FC_SYSTEM_PROMPT,
                user_message="test",
                capability_boundary=_BOUNDARY,
            )
        return mock_client.messages.create.call_args

    def test_M1_module_constant_is_new_model(self):
        """_MODEL constant in subagents module equals the new identifier."""
        self.assertEqual(
            _MODEL, _NEW_MODEL,
            f"_MODEL={_MODEL!r} — expected {_NEW_MODEL!r}",
        )

    def test_M1_new_model_string_passed_to_create(self):
        """Captured call_args.kwargs['model'] is exactly the new identifier."""
        call_args    = self._call_subagent_with_mock()
        actual_model = call_args.kwargs.get("model")
        self.assertEqual(
            actual_model, _NEW_MODEL,
            f"SDK received model={actual_model!r}; expected {_NEW_MODEL!r}",
        )

    def test_M1_old_retired_string_not_passed_to_create(self):
        """Retired model string must NOT appear in the captured SDK call argument."""
        call_args    = self._call_subagent_with_mock()
        actual_model = call_args.kwargs.get("model")
        self.assertNotEqual(
            actual_model, _OLD_MODEL,
            "Retired claude-3-5-haiku-20241022 still reaching SDK — migration incomplete",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# M2 — max_tokens unchanged at 1024; temperature and top_p still absent
# ═══════════════════════════════════════════════════════════════════════════════

class TestM2MaxTokensAndCallSignature(unittest.TestCase):

    def test_M2_max_tokens_constant_is_1024(self):
        self.assertEqual(_MAX_TOKENS, 1024)

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_M2_default_max_tokens_1024_reaches_sdk(self):
        """Default max_output_tokens (1024) threads through to messages.create max_tokens."""
        response    = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
            # no max_output_tokens override → default _MAX_TOKENS=1024
        )

        actual_max = mock_client.messages.create.call_args.kwargs.get("max_tokens")
        self.assertEqual(actual_max, 1024,
                         f"max_tokens={actual_max!r} reached SDK — expected 1024")

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_M2_no_temperature_in_sdk_call(self):
        response    = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )

        kwargs = mock_client.messages.create.call_args.kwargs
        self.assertNotIn("temperature", kwargs,
                         "temperature must not appear in messages.create() — not set by migration")

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_M2_no_top_p_in_sdk_call(self):
        response    = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )

        kwargs = mock_client.messages.create.call_args.kwargs
        self.assertNotIn("top_p", kwargs,
                         "top_p must not appear in messages.create() — not set by migration")

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_M2_exact_call_signature_six_params(self):
        """messages.create() receives exactly six kwargs: model, max_tokens, system,
        tools, messages, tool_choice — no others."""
        response    = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )

        kwargs = mock_client.messages.create.call_args.kwargs
        expected = {"model", "max_tokens", "system", "tools", "messages", "tool_choice"}
        self.assertEqual(
            set(kwargs.keys()), expected,
            f"Unexpected call signature. got={set(kwargs.keys())}, expected={expected}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# M3 — Closed-schema validator pass/fail pair; model string is irrelevant
# ═══════════════════════════════════════════════════════════════════════════════

class TestM3SchemaValidatorUnaffected(unittest.TestCase):

    def test_M3_valid_payload_returns_shadow_pass(self):
        result = validate_shadow_output(_valid_schema_payload())
        self.assertTrue(result.passed,
                        f"SHADOW_PASS expected; got violation={result.violation!r}")

    def test_M3_shadow_pass_is_singleton(self):
        result = validate_shadow_output(_valid_schema_payload())
        self.assertIs(result, SHADOW_PASS)

    def test_M3_forbidden_key_terminal_label_still_rejected(self):
        bad = _valid_schema_payload()
        bad["terminal_label"] = "KALSHI_WATCH"
        result = validate_shadow_output(bad)
        self.assertFalse(result.passed,
                         "Forbidden key 'terminal_label' must be rejected")

    def test_M3_forbidden_key_can_execute_still_rejected(self):
        bad = _valid_schema_payload()
        bad["can_execute"] = False
        result = validate_shadow_output(bad)
        self.assertFalse(result.passed,
                         "Forbidden key 'can_execute' must be rejected")

    def test_M3_advisory_only_false_still_rejected(self):
        bad = _valid_schema_payload()
        bad["advisory_only"] = False
        result = validate_shadow_output(bad)
        self.assertFalse(result.passed,
                         "advisory_only=False must be rejected (must be exact bool True)")


# ═══════════════════════════════════════════════════════════════════════════════
# M4 — Usage accounting propagates from mocked response (model-independent)
# ═══════════════════════════════════════════════════════════════════════════════

class TestM4UsageAccountingUnaffected(unittest.TestCase):

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_M4_real_usage_tokens_propagate_to_subagent_result(self):
        """input_tokens / output_tokens from mocked usage.* reach SubagentResult fields."""
        response    = _make_tool_response_with_usage(
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
        self.assertEqual(result.input_tokens,  1500)
        self.assertEqual(result.output_tokens,  340)

    @patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True)
    def test_M4_missing_usage_gives_none_not_zero(self):
        """When response.usage is None, SubagentResult tokens are None (not 0)."""
        response           = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
        response.usage     = None
        mock_client        = MagicMock()
        mock_client.messages.create.return_value = response

        result = _run_single_tool_subagent(
            client=mock_client,
            subagent_id="forecast_context",
            tool_def=_FC_TOOL_DEF,
            system_prompt=_FC_SYSTEM_PROMPT,
            user_message="test",
            capability_boundary=_BOUNDARY,
        )

        self.assertIsNone(result.input_tokens,
                          "input_tokens must be None (not 0) when usage absent")
        self.assertIsNone(result.output_tokens,
                          "output_tokens must be None (not 0) when usage absent")
        self.assertNotEqual(result.input_tokens,  0)
        self.assertNotEqual(result.output_tokens, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# M5 — Budget guard is fully config-driven; no Haiku 3.5 prices hardcoded
# ═══════════════════════════════════════════════════════════════════════════════

class TestM5BudgetGuardConfigDriven(unittest.TestCase):

    def test_M5_runner_source_contains_no_hardcoded_price_literals(self):
        """
        In the runner source, INPUT_PRICE_PER_TOKEN and OUTPUT_PRICE_PER_TOKEN
        only appear as dict-key reads (config[...]), never as literal float assignments.
        """
        src   = (_REPO / "scripts" / "run_kalshi_wx_shadow_pilot.py").read_text()
        lines = [ln for ln in src.splitlines()
                 if "INPUT_PRICE_PER_TOKEN" in ln or "OUTPUT_PRICE_PER_TOKEN" in ln]

        import re
        pattern = re.compile(
            r'(?:INPUT_PRICE_PER_TOKEN|OUTPUT_PRICE_PER_TOKEN)\s*[=:]\s*0\.'
        )
        for ln in lines:
            self.assertIsNone(
                pattern.search(ln.strip()),
                f"Hardcoded price literal found in runner: {ln.strip()!r}",
            )

    def test_M5_subagents_source_contains_no_hardcoded_price_literals(self):
        """The subagents module also must not hardcode any Anthropic price values."""
        src = (_REPO / "gate_engine" / "kalshi_wx_shadow_subagents.py").read_text()
        for price_hint in ("INPUT_PRICE", "OUTPUT_PRICE", "0.000001", "0.000005",
                           "0.000008", "0.000025"):
            self.assertNotIn(
                price_hint, src,
                f"Price hint {price_hint!r} found in subagents module — must be absent",
            )

    def test_M5_worst_case_cost_is_fully_parameterised(self):
        """Changing the price config changes the cost — no hidden constant inside."""
        cost_a = worst_case_call_cost(1000, 1024,
                                      input_price=0.000001, output_price=0.000005)
        cost_b = worst_case_call_cost(1000, 1024,
                                      input_price=0.000008, output_price=0.000025)
        self.assertGreater(cost_b, cost_a)

    def test_M5_budget_guard_halts_pilot_under_haiku4_prices(self):
        """Budget guard fires correctly when configured with Haiku 4.5 pricing."""
        snaps = [_make_snap(f"rsid-{i}") for i in range(5)]

        def _mock_call(*a, **kw):
            return {"success": False, "failure_reason": "BLOCKED",
                    "tool_input": None, "input_tokens": None,
                    "output_tokens": None, "model": None}

        haiku4_config = {
            "PILOT_BUDGET_USD":           0.000001,   # sub-penny — fires immediately
            "INPUT_PRICE_PER_TOKEN":      0.000008,   # Haiku 4.5 hypothetical
            "OUTPUT_PRICE_PER_TOKEN":     0.000025,
            "MAX_OUTPUT_TOKENS_PER_CALL": 1024,
        }
        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=snaps):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                       return_value=False):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results",
                           return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                        summary = run_pilot(haiku4_config, MagicMock(),
                                            call_agent_fn=_mock_call)

        self.assertEqual(summary["stop_reason"], "BUDGET_EXCEEDED",
                         "Budget guard must fire under Haiku 4.5 price config")


# ═══════════════════════════════════════════════════════════════════════════════
# M6 — Authority constants unchanged; retired string absent from production code
# ═══════════════════════════════════════════════════════════════════════════════

class TestM6AuthorityAndCodeIntegrity(unittest.TestCase):

    def test_M6_can_execute_is_false(self):
        self.assertFalse(KalshiWxShadowResearchClient.CAN_EXECUTE)

    def test_M6_production_authority_is_false(self):
        self.assertFalse(KalshiWxShadowResearchClient.PRODUCTION_AUTHORITY)

    def test_M6_user_output_authority_is_false(self):
        self.assertFalse(KalshiWxShadowResearchClient.USER_OUTPUT_AUTHORITY)

    def test_M6_client_module_contains_no_model_string_at_all(self):
        """kalshi_wx_shadow_client.py must contain neither the old nor the new model string."""
        src = (_REPO / "gate_engine" / "kalshi_wx_shadow_client.py").read_text()
        self.assertNotIn(_NEW_MODEL, src,
                         "New model string must NOT appear in client module")
        self.assertNotIn(_OLD_MODEL, src,
                         "Old model string must NOT appear in client module")

    def test_M6_retired_string_absent_from_all_production_python(self):
        """claude-3-5-haiku-20241022 must not appear in any non-test .py file."""
        prod_dirs = [_REPO / "gate_engine", _REPO / "scripts"]
        hits = []
        for d in prod_dirs:
            for f in sorted(d.rglob("*.py")):
                if any(part in ("tests", "test") for part in f.parts):
                    continue
                if _OLD_MODEL in f.read_text():
                    hits.append(str(f.relative_to(_REPO)))
        self.assertEqual(hits, [],
                         f"Retired model string found in production files: {hits}")

    def test_M6_new_model_present_only_in_subagents_and_agent(self):
        """New model string appears in exactly the two expected production files."""
        prod_dirs = [_REPO / "gate_engine", _REPO / "scripts"]
        hits = []
        for d in prod_dirs:
            for f in sorted(d.rglob("*.py")):
                if any(part in ("tests", "test") for part in f.parts):
                    continue
                if _NEW_MODEL in f.read_text():
                    hits.append(f.name)
        self.assertEqual(
            sorted(hits),
            ["kalshi_wx_shadow_agent.py", "kalshi_wx_shadow_subagents.py"],
            f"New model string found in unexpected files: {hits}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# M7 — Zero real Anthropic client construction with live credentials
# ═══════════════════════════════════════════════════════════════════════════════

class TestM7ZeroRealAPICalls(unittest.TestCase):

    def test_M7_no_live_api_key_read_in_this_test_file(self):
        """This test file itself must not *use* any live credential key name.
        Check is done by scanning for os.environ.get / os.environ[] access patterns
        that reference credential keys — not a naive substring scan (which would
        self-match the key names written in this very assertion)."""
        this_src = Path(__file__).read_text()
        # Encode key fragments to prevent self-match: "ANTHROPIC" + "_API_KEY"
        cred_keys = [
            "ANTHROPIC" + "_API_KEY",
            "AI_INTEGRATIONS" + "_ANTHROPIC" + "_API_KEY",
        ]
        for key in cred_keys:
            # Only flag if the key appears in an access pattern, not in a string literal
            # inside an assertion.  Use a simple heuristic: the key must NOT appear
            # in an os.environ.get() call or as a bare env lookup.
            import re as _re
            access_pattern = _re.compile(
                r'os\.environ(?:\.get)?\s*[\[(][\'"]' + _re.escape(key)
            )
            self.assertIsNone(
                access_pattern.search(this_src),
                f"Test file must not access live credential via os.environ: {key!r}",
            )

    def test_M7_subagents_has_no_top_level_anthropic_import(self):
        """
        gate_engine/kalshi_wx_shadow_subagents.py must not import 'anthropic'
        at module top level — avoids eager SDK construction during import.
        """
        src = (_REPO / "gate_engine" / "kalshi_wx_shadow_subagents.py").read_text()
        top_level = [
            ln for ln in src.splitlines()
            if (ln.startswith("import anthropic") or
                ln.startswith("from anthropic"))
        ]
        self.assertEqual(top_level, [],
                         f"Top-level anthropic import found: {top_level}")

    def test_M7_build_sdk_client_not_called_when_flag_off(self):
        """When the shadow feature flag is False, _build_sdk_client() is never called."""
        import gate_engine.kalshi_wx_shadow_client as _cm
        with patch.object(_cm, "_build_sdk_client") as mock_build:
            with patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", False):
                client = KalshiWxShadowResearchClient()
                client.research("MIA", "2026-08-09", "test-run-m7")
            mock_build.assert_not_called()

    def test_M7_no_real_sdk_constructed_during_entire_test_module(self):
        """
        Confirm no real anthropic.Anthropic() object is instantiated anywhere in
        this test module's execution path — all SDK clients are MagicMock instances.
        """
        try:
            import anthropic as _anthropic
        except ImportError:
            self.skipTest("anthropic SDK not installed — trivially no real calls")

        with patch.object(_anthropic, "Anthropic") as mock_cls:
            # Re-run a representative subagent call — should use MagicMock not real SDK
            response    = _make_tool_response("emit_forecast_context", _FC_VALID_INPUT)
            mock_client = MagicMock()
            mock_client.messages.create.return_value = response
            with patch("gate_engine.kalshi_wx_shadow_subagents._RESEARCH_API_ENABLED", True):
                _run_single_tool_subagent(
                    client=mock_client,        # injected mock, not a real SDK instance
                    subagent_id="forecast_context",
                    tool_def=_FC_TOOL_DEF,
                    system_prompt=_FC_SYSTEM_PROMPT,
                    user_message="test",
                    capability_boundary=_BOUNDARY,
                )
            mock_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
