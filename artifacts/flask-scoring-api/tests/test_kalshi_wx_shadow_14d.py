"""
tests/test_kalshi_wx_shadow_14d.py
Step 14D audit-hardening tests.

FIX 1 — Model identity persistence
====================================
Verifies that call_one_agent returns the model identifier read from
_MODEL on the gate_engine.kalshi_wx_shadow_subagents module at call time
(not hardcoded None), so that a future model migration (changing _MODEL)
automatically flows into what gets persisted with no runner code change.

FIX 2 — Mock-path enforcement (adversarial tests)
===================================================
Verifies that call_agent_fn mocks supplied to run_pilot() are gated by the
same outer enforcement choke point (native schema + CapabilityBoundary) as
real SDK output.  Seven adversarial cases prove the mock path cannot produce
a false PASS by taking an easier route than production code.

Zero real Anthropic API calls anywhere in this file.
"""
from __future__ import annotations

import sys
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Project path setup ────────────────────────────────────────────────────────
_PROJ_ROOT   = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _PROJ_ROOT / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_kalshi_wx_shadow_pilot as _pilot
from run_kalshi_wx_shadow_pilot import AGENT_IDS, run_pilot

# ── Shared fixtures (mirrors test_kalshi_wx_shadow_pilot.py helpers) ──────────

_MOCK_CONFIG = {
    "PILOT_BUDGET_USD":           100.0,
    "INPUT_PRICE_PER_TOKEN":      0.000001,
    "OUTPUT_PRICE_PER_TOKEN":     0.000005,
    "MAX_OUTPUT_TOKENS_PER_CALL": 1024,
}

_VALID_TOOL_INPUTS: dict = {
    "forecast_context": {
        "scoring_mode":        "gaussian_forecast",
        "calibration_status":  "PROVISIONAL",
        "uncertainty_tier":    "MODERATE",
        "recommended_ceiling": "KALSHI_WATCH",
        "blockers":            [],
        "notes":               "14d test fixture",
    },
    "source_reconciliation": {
        "sources_present":       [],
        "sources_missing":       [],
        "conflicts":             [],
        "reconciliation_status": "OK",
        "notes":                 "14d test fixture",
    },
    "contradiction_detection": {
        "contradictions_found": [],
        "ceiling_impacted":     False,
        "notes":                "14d test fixture",
    },
    "unusual_regime": {
        "regime_unusual":     False,
        "regime_factors":     [],
        "reliability_impact": "NONE",
        "notes":              "14d test fixture",
    },
    "uncertainty_explanation": {
        "uncertainty_tier":      "LOW",
        "uncertainty_sources":   [],
        "ceiling_impact":        "NONE",
        "sigma_f_estimate":      2.0,
        "horizon_hours_estimate": 12.0,
        "notes":                 "14d test fixture",
    },
}


def _make_snap(rsid: str) -> dict:
    return {
        "research_snapshot_id": rsid,
        "snapshot_json": {
            "research_snapshot_id": rsid,
            "city": "NYC",
            "station": "KNYC",
            "market_date": "2026-08-15",
            "forecast_high": 85.0,
            "weather_data_source_tier": "nws_primary",
            "sigma_f": 3.5,
            "horizon_hours": 18.0,
        },
        "terminal_label":         "KALSHI_WATCH",
        "price_gate_disposition": "DRY_RUN_ONLY",
        "can_execute":            False,
    }


def _mock_result(tool_input: dict, success: bool = True) -> dict:
    return {
        "success":                 success,
        "tool_input":              tool_input if success else {},
        "failure_reason":          None if success else "mock failure",
        "latency_ms":              1,
        "model":                   "mock-model",
        "input_tokens":            1,
        "output_tokens":           1,
        "usage_accounting_status": "AVAILABLE",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FIX 1 — Model identity persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelIdentityPersistence(unittest.TestCase):
    """
    call_one_agent must read the model identifier from _MODEL on
    gate_engine.kalshi_wx_shadow_subagents at call time (module attribute
    reference), not from a hardcoded literal.  A future model migration
    changes only _MODEL in the subagents file; no runner code change required.
    """

    def _call_one_agent_with_sentinel(self, sentinel: str) -> dict:
        """
        Run call_one_agent for forecast_context with Gate A enabled,
        the actual subagent function mocked to avoid a real API call,
        and _MODEL patched to the given sentinel string.

        Returns the result dict from call_one_agent.
        """
        import gate_engine.kalshi_wx_shadow_subagents as sub_mod

        # Build a minimal SubagentResult that run_forecast_context_subagent
        # would return on success (no real API call).
        fake_result = sub_mod.SubagentResult(
            subagent_id="forecast_context",
            tool_name="emit_forecast_context",
            tool_input=dict(_VALID_TOOL_INPUTS["forecast_context"]),
            hook_violations=[],
            success=True,
            input_tokens=10,
            output_tokens=5,
            usage_accounting_status="AVAILABLE",
        )

        with patch.dict(os.environ, {"SHADOW_RESEARCH_API_ENABLED": "true"}):
            with patch.object(sub_mod, "_MODEL", sentinel):
                with patch.object(
                    sub_mod, "run_forecast_context_subagent",
                    return_value=fake_result,
                ):
                    with patch(
                        "run_kalshi_wx_shadow_pilot.deserialize_snapshot",
                        return_value=MagicMock(
                            city="NYC", market_date="2026-08-15",
                            research_snapshot_id="test-rsid",
                        ),
                    ):
                        return _pilot.call_one_agent(
                            "forecast_context",
                            {},                   # snap_json (not used after mock)
                            {},                   # prior_results
                            "run-test-14d",
                            MagicMock(),          # sdk_client
                            MagicMock(),          # capability_boundary
                        )

    def test_SDMOD_model_comes_from_module_constant_not_hardcoded(self):
        """
        call_one_agent must return the value of _MODEL, not None or a
        literal string.  Changing _MODEL automatically changes what is
        returned — no runner code change required.
        """
        sentinel = "test-model-14d-migration-sentinel"
        result = self._call_one_agent_with_sentinel(sentinel)

        self.assertEqual(
            result["model"], sentinel,
            f"model must equal the current _MODEL value {sentinel!r}; "
            f"got {result['model']!r}. The runner must NOT hardcode None.",
        )

    def test_SDMOD_different_sentinel_produces_different_model(self):
        """
        Two different _MODEL values must produce two different model strings.
        Proves the value is not cached from a previous call or hardcoded.
        """
        result_a = self._call_one_agent_with_sentinel("model-variant-alpha")
        result_b = self._call_one_agent_with_sentinel("model-variant-beta")

        self.assertNotEqual(
            result_a["model"], result_b["model"],
            "Changing _MODEL sentinel must change the returned model string.",
        )
        self.assertEqual(result_a["model"], "model-variant-alpha")
        self.assertEqual(result_b["model"], "model-variant-beta")

    def test_SDMOD_real_model_constant_is_returned_when_unpatched(self):
        """
        Without any patching, call_one_agent must return the real _MODEL
        constant defined in kalshi_wx_shadow_subagents.
        """
        import gate_engine.kalshi_wx_shadow_subagents as sub_mod
        real_model = sub_mod._MODEL

        result = self._call_one_agent_with_sentinel(real_model)
        self.assertEqual(result["model"], real_model)

    def test_SDMOD_gate_a_disabled_returns_none_model(self):
        """
        When Gate A (SHADOW_RESEARCH_API_ENABLED) is not true, call_one_agent
        returns without calling the subagent.  In that case model=None is
        correct (no model was invoked).
        """
        import gate_engine.kalshi_wx_shadow_subagents as sub_mod

        with patch.dict(os.environ, {"SHADOW_RESEARCH_API_ENABLED": "false"}):
            result = _pilot.call_one_agent(
                "forecast_context", {}, {}, "run-gate-a-test",
                MagicMock(), MagicMock(),
            )

        self.assertFalse(result["success"], "Gate A disabled must return success=False")
        self.assertIsNone(result["model"],
                          "model must be None when gate A is disabled (no model invoked)")

    def test_SDMOD_no_hardcoded_model_string_in_runner(self):
        """
        Structural: the runner script must not contain the literal model string
        'claude-haiku-4-5-20251001' (it must be read from the subagent module,
        not duplicated in the runner).
        """
        runner_src = (_SCRIPTS_DIR / "run_kalshi_wx_shadow_pilot.py").read_text()
        self.assertNotIn(
            "claude-haiku-4-5-20251001", runner_src,
            "Runner must not hardcode the model string — read it from _sa_mod._MODEL.",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# FIX 2 — Mock-path enforcement (adversarial tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMockPathEnforcement(unittest.TestCase):
    """
    Adversarial tests proving that call_agent_fn mocks cannot bypass the
    outer enforcement choke point in run_pilot().

    Every test uses call_agent_fn (the mock injection path) to prove
    enforcement fires on THAT path.  The enforcement must use the same
    validate_subagent_output and CapabilityBoundary code as the real path —
    not a hand-rolled parallel check.
    """

    def _run_one_agent_with_mock_output(
        self,
        agent_id: str,
        mock_tool_input: dict,
        success: bool = True,
    ) -> list:
        """
        Run pilot for exactly one uncompleted agent (agent_id).  The mock
        caller returns mock_tool_input with success=True (or False).

        Returns the list of write_result_row kwargs dicts for that agent,
        with a convenience key "failure_reason" hoisted from
        validated_output_json so assertions can reference it directly.
        """
        snap = _make_snap("adv-rsid-14d")
        writes: list = []

        def mock_caller(aid, snap_json, prior, run_id, sdk, cap, **kw):
            if aid == agent_id:
                return _mock_result(mock_tool_input, success=success)
            # All other agents already completed
            return {
                "success": False, "tool_input": {}, "failure_reason": "not target",
                "latency_ms": 0, "model": None, "input_tokens": None,
                "output_tokens": None, "usage_accounting_status": "UNAVAILABLE",
            }

        def _is_completed(conn, rsid, aid):
            return aid != agent_id   # only agent_id is uncompleted

        with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                   return_value=[snap]):
            with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                       side_effect=_is_completed):
                with patch("run_kalshi_wx_shadow_pilot.load_prior_results",
                           return_value={}):
                    with patch("run_kalshi_wx_shadow_pilot.write_result_row",
                               side_effect=lambda conn, **kw: writes.append(kw)):
                        with patch(
                            "run_kalshi_wx_shadow_pilot._record_snapshot_schema_validation"
                        ):
                            run_pilot(
                                _MOCK_CONFIG,
                                MagicMock(),
                                call_agent_fn=mock_caller,
                            )

        # failure_reason lives inside validated_output_json (the persisted JSON
        # blob), not as a top-level column.  Hoist it for cleaner assertions.
        result_rows = []
        for w in writes:
            if w.get("agent_id") == agent_id:
                vjson = w.get("validated_output_json") or {}
                row = dict(w)
                row["failure_reason"] = vjson.get("failure_reason")
                result_rows.append(row)
        return result_rows

    # ── 1. Governance key: final_decision ─────────────────────────────────────

    def test_SDADV_governance_key_final_decision_blocked(self):
        """
        Mock returning 'final_decision' in tool_input must be BLOCKED.
        The outer enforcement must catch it before it reaches prior_results.
        """
        rows = self._run_one_agent_with_mock_output(
            "forecast_context",
            {
                "final_decision":      "APPROVED",
                "scoring_mode":        "gaussian_forecast",
                "calibration_status":  "PROVISIONAL",
                "uncertainty_tier":    "MODERATE",
                "recommended_ceiling": "KALSHI_WATCH",
                "blockers":            [],
                "notes":               "",
            },
        )
        self.assertEqual(len(rows), 1,
                         "write_result_row must still be called for BLOCKED results")
        self.assertEqual(
            rows[0]["status"], "BLOCKED",
            "final_decision in mock output must be BLOCKED by outer enforcement",
        )
        self.assertIn("OUTER_", rows[0].get("failure_reason") or "",
                      "failure_reason must reference outer enforcement")

    # ── 2. Governance key: stake_tier ─────────────────────────────────────────

    def test_SDADV_governance_key_stake_tier_blocked(self):
        """Mock returning 'stake_tier' in tool_input must be BLOCKED."""
        rows = self._run_one_agent_with_mock_output(
            "forecast_context",
            {
                "stake_tier":          "A",
                "scoring_mode":        "gaussian_forecast",
                "calibration_status":  "PROVISIONAL",
                "uncertainty_tier":    "MODERATE",
                "recommended_ceiling": "KALSHI_WATCH",
                "blockers":            [],
                "notes":               "",
            },
        )
        self.assertEqual(rows[0]["status"], "BLOCKED")
        self.assertIn("OUTER_", rows[0].get("failure_reason") or "")

    # ── 3. Governance key: is_playable ────────────────────────────────────────

    def test_SDADV_governance_key_is_playable_blocked(self):
        """Mock returning 'is_playable' in tool_input must be BLOCKED."""
        rows = self._run_one_agent_with_mock_output(
            "forecast_context",
            {
                "is_playable":         True,
                "scoring_mode":        "gaussian_forecast",
                "calibration_status":  "PROVISIONAL",
                "uncertainty_tier":    "MODERATE",
                "recommended_ceiling": "KALSHI_WATCH",
                "blockers":            [],
                "notes":               "",
            },
        )
        self.assertEqual(rows[0]["status"], "BLOCKED")
        self.assertIn("OUTER_", rows[0].get("failure_reason") or "")

    # ── 4. Unknown/unexpected property ────────────────────────────────────────

    def test_SDADV_unknown_property_blocked(self):
        """
        Mock returning an unexpected property not in the subagent's allowed
        key set must be BLOCKED by the outer native schema check.
        """
        rows = self._run_one_agent_with_mock_output(
            "forecast_context",
            {
                "scoring_mode":         "gaussian_forecast",
                "calibration_status":   "PROVISIONAL",
                "uncertainty_tier":     "MODERATE",
                "recommended_ceiling":  "KALSHI_WATCH",
                "blockers":             [],
                "notes":                "",
                "unexpected_extra_key": "surprise",   # ← not in _FC_ALLOWED
            },
        )
        self.assertEqual(
            rows[0]["status"], "BLOCKED",
            "Unknown property must be BLOCKED by outer native schema (additionalProperties=false)",
        )
        self.assertIn(
            "OUTER_NATIVE_SCHEMA_VIOLATION",
            rows[0].get("failure_reason") or "",
            "failure_reason must name OUTER_NATIVE_SCHEMA_VIOLATION",
        )

    # ── 5. Missing required property ──────────────────────────────────────────

    def test_SDADV_missing_required_property_blocked(self):
        """
        Mock returning output that omits a required field must be BLOCKED.
        'calibration_status' is required for forecast_context.
        """
        rows = self._run_one_agent_with_mock_output(
            "forecast_context",
            {
                # calibration_status intentionally omitted
                "scoring_mode":        "gaussian_forecast",
                "uncertainty_tier":    "MODERATE",
                "recommended_ceiling": "KALSHI_WATCH",
                "blockers":            [],
                "notes":               "",
            },
        )
        self.assertEqual(
            rows[0]["status"], "BLOCKED",
            "Missing required field must be BLOCKED by outer native schema",
        )
        self.assertIn("OUTER_NATIVE_SCHEMA_VIOLATION",
                      rows[0].get("failure_reason") or "")

    # ── 6. Wrong-typed field ──────────────────────────────────────────────────

    def test_SDADV_wrong_typed_field_blocked(self):
        """
        Mock returning output with wrong type for a field must be BLOCKED.
        'scoring_mode' must be a string; passing an int instead.
        """
        rows = self._run_one_agent_with_mock_output(
            "forecast_context",
            {
                "scoring_mode":        42,              # ← must be str
                "calibration_status":  "PROVISIONAL",
                "uncertainty_tier":    "MODERATE",
                "recommended_ceiling": "KALSHI_WATCH",
                "blockers":            [],
                "notes":               "",
            },
        )
        self.assertEqual(
            rows[0]["status"], "BLOCKED",
            "Wrong-typed field must be BLOCKED by outer native schema",
        )
        self.assertIn("OUTER_NATIVE_SCHEMA_VIOLATION",
                      rows[0].get("failure_reason") or "")

    # ── 7. Valid output passes ─────────────────────────────────────────────────

    def test_SDADV_valid_output_passes(self):
        """
        Mock returning a genuinely valid, correctly-shaped output must PASS
        (status=COMPLETE).  Proves enforcement does not over-block valid mocks.
        """
        rows = self._run_one_agent_with_mock_output(
            "forecast_context",
            _VALID_TOOL_INPUTS["forecast_context"],
        )
        self.assertEqual(
            rows[0]["status"], "COMPLETE",
            "Valid mock output must be accepted as COMPLETE by outer enforcement",
        )
        self.assertIsNone(
            rows[0].get("failure_reason"),
            "failure_reason must be None for a valid mock output",
        )

    # ── 8. Shared validator code proof ────────────────────────────────────────

    def test_SDADV_enforcement_uses_shared_validate_subagent_output(self):
        """
        Structural proof: the outer enforcement must call validate_subagent_output
        from kalshi_wx_shadow_native_schema — the SAME function used inside
        _run_single_tool_subagent on the real SDK path.

        We patch the function at its canonical module location and confirm it
        is invoked during a mock-path run_pilot() call, proving the mock path
        and the real path share exactly the same enforcement code, not two
        independent implementations.
        """
        import gate_engine.kalshi_wx_shadow_native_schema as _ns_mod

        call_record: list = []
        original_fn = _ns_mod.validate_subagent_output

        def recording_validator(subagent_id, tool_input):
            call_record.append(subagent_id)
            return original_fn(subagent_id, tool_input)

        def mock_caller(aid, *a, **kw):
            return _mock_result(_VALID_TOOL_INPUTS.get(aid, _VALID_TOOL_INPUTS["forecast_context"]))

        snap = _make_snap("adv-shared-validator")
        with patch.object(_ns_mod, "validate_subagent_output", recording_validator):
            with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                       return_value=[snap]):
                with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                           return_value=False):
                    with patch("run_kalshi_wx_shadow_pilot.load_prior_results",
                               return_value={}):
                        with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                            with patch("run_kalshi_wx_shadow_pilot."
                                       "_record_snapshot_schema_validation"):
                                run_pilot(
                                    _MOCK_CONFIG,
                                    MagicMock(),
                                    call_agent_fn=mock_caller,
                                )

        self.assertEqual(
            len(call_record), len(AGENT_IDS),
            f"validate_subagent_output must be called once per agent "
            f"(expected {len(AGENT_IDS)}, got {len(call_record)})",
        )
        self.assertEqual(
            sorted(call_record), sorted(AGENT_IDS),
            "validate_subagent_output must be called with each of the 5 agent_ids",
        )

    # ── 9. CapabilityBoundary post_tool_use_hook invoked on mock path ─────────

    def test_SDADV_capability_boundary_post_hook_invoked_on_mock_path(self):
        """
        Structural proof: the outer enforcement must call CapabilityBoundary
        post_tool_use_hook on mock-path results (not skip it).

        We patch CapabilityBoundary.post_tool_use_hook to record calls and
        confirm it fires for each accepted mock output.
        """
        from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
        import gate_engine.kalshi_wx_shadow_capability_boundary as _cb_mod

        post_calls: list = []
        original_post = CapabilityBoundary.post_tool_use_hook

        def recording_post_hook(self_cb, subagent_id, tool_name, tool_output):
            post_calls.append(subagent_id)
            return original_post(self_cb, subagent_id, tool_name, tool_output)

        def mock_caller(aid, *a, **kw):
            return _mock_result(_VALID_TOOL_INPUTS.get(aid, _VALID_TOOL_INPUTS["forecast_context"]))

        snap = _make_snap("adv-cap-boundary")
        with patch.object(CapabilityBoundary, "post_tool_use_hook", recording_post_hook):
            with patch("run_kalshi_wx_shadow_pilot.fetch_eligible_snapshots",
                       return_value=[snap]):
                with patch("run_kalshi_wx_shadow_pilot.is_pair_completed",
                           return_value=False):
                    with patch("run_kalshi_wx_shadow_pilot.load_prior_results",
                               return_value={}):
                        with patch("run_kalshi_wx_shadow_pilot.write_result_row"):
                            with patch("run_kalshi_wx_shadow_pilot."
                                       "_record_snapshot_schema_validation"):
                                run_pilot(
                                    _MOCK_CONFIG,
                                    MagicMock(),
                                    call_agent_fn=mock_caller,
                                )

        self.assertEqual(
            len(post_calls), len(AGENT_IDS),
            f"post_tool_use_hook must fire once per agent on the mock path "
            f"(expected {len(AGENT_IDS)}, got {len(post_calls)})",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
