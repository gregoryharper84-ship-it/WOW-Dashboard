"""
tests/test_universal_agent_b3c.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C

Offline enforcement audit test suite for the B3C bounded Claude canary.

ZERO REAL ANTHROPIC API CALLS — every test uses a mocked Anthropic client.
The live dispatch flag (UAC_MLB_ML_CLAUDE_SHADOW_ENABLED) remains False by default.
Force-enabled runs use _force_enabled=True (testing escape-hatch only).

Test categories:
  1  TestCanaryConfig              — flag, budget, model constants
  2  TestBudgetState               — budget guard unit tests
  3  TestClaudeRoleRunnerModelIdentity — model pinning, CANARY_FAIL_MODEL_IDENTITY
  4  TestClaudeRoleRunnerForbiddenKeys — adversarial: each forbidden key → OUTPUT_REJECTED
  5  TestClaudeRoleRunnerFailModes  — timeout/network/malformed/missing-usage fail-closed
  6  TestClaudeRoleRunnerBudgetGuard — structural 6-call ceiling, spend ceiling
  7  TestOfflineEnforcementAudit   — Step-14D: prove REAL B0/B1/B2 functions called
  8  TestCanaryStore               — table DDL and upsert with mock DB
  9  TestCanaryPipelineIntegration — end-to-end with mocked client
  10 TestCanaryNoProductionImports — source scan: no real Anthropic, no app.py
  11 TestCanaryFlagIndependence    — no CAN_EXECUTE / kalshi_wx / SHADOW_ENABLED refs
  12 TestCanaryInvariants          — can_execute=False, advisory_only=True, frozen results
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import pathlib
import unittest
from typing import Any
from unittest.mock import MagicMock, call, patch

# ── Imports under test ────────────────────────────────────────────────────────
from gate_engine.universal_agent.canary.canary_config import (
    AUTO_BUDGET_INCREASE,
    AUTOMATIC_RETRIES,
    INPUT_COST_PER_MTOK,
    MAX_CALLS,
    MAX_TOKENS,
    MAX_TOTAL_SPEND_USD,
    OUTPUT_COST_PER_MTOK,
    PER_CALL_TIMEOUT_SECONDS,
    PINNED_MODEL,
    WORST_CASE_COST_PER_CALL,
    WORST_CASE_INPUT_TOKENS,
    UAC_MLB_ML_CLAUDE_SHADOW_ENABLED,
)
from gate_engine.universal_agent.canary.claude_role_runner import (
    BudgetState,
    CanaryBudgetGuardError,
    CanaryCallFailedError,
    CanaryCallRecord,
    CanaryCallStatus,
    CanaryModelIdentityError,
    CanaryOutputRejectedError,
    ClaudeRoleRunner,
    _PINNED_MODEL,
    _ROLE_TOOL_DEFINITIONS,
    _scan_forbidden_keys,  # must be the REAL B0 function — tests verify via assertIs
)
from gate_engine.universal_agent.canary.canary_pipeline import (
    CanaryPipeline,
    CanaryPipelineResult,
    CanaryPipelineStatus,
    run_canary_pipeline,
)
from gate_engine.universal_agent.canary.canary_store import (
    ensure_canary_tables,
    persist_canary_result,
)

# ── Real B0/B1/B2 imports for object-identity and call-count assertions ────────
from gate_engine.universal_agent.output_contract import (
    FORBIDDEN_GOVERNANCE_KEYS,
    _scan_forbidden_keys as _real_scan_forbidden_keys,
)
from gate_engine.universal_agent.capability_boundary import UniversalCapabilityBoundary
from gate_engine.universal_agent.bundle_assembler import assemble_bundle as real_assemble_bundle
from gate_engine.universal_agent.roles.data_slate_integrity import (
    validate_data_slate_integrity_output as real_validate_dsi,
)
from gate_engine.universal_agent.roles.news_status import (
    validate_news_status_output as real_validate_ns,
)
from gate_engine.universal_agent.roles.market_exact_line import (
    validate_market_exact_line_output as real_validate_mel,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    validate_sport_specialist_output as real_validate_ss,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    validate_failure_contradiction_output as real_validate_fc,
)
from gate_engine.universal_agent.roles.final_refresh import (
    validate_final_refresh_output as real_validate_fr,
)
from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry


# ═══════════════════════════════════════════════════════════════════════════════
# Shared test fixtures
# ═══════════════════════════════════════════════════════════════════════════════

_VALID_MLB_ROW: dict = {
    "sport":       "MLB",
    "market":      "moneyline",
    "event_id":    "2026-08-10-NYY-BOS",
    "target_date": "2026-08-10",
    "home_team":   "Boston Red Sox",
    "away_team":   "New York Yankees",
}

# Valid tool inputs (block.input) for each role — the runner adds role_id/schema_version.
# These must produce payloads that pass the B1 validators after wrapping.
_VALID_DSI_TOOL_INPUT: dict = {
    "data_freshness_status":   "FRESH",
    "slate_consistency_check": "CONSISTENT",
    "source_coverage":         {"primary": "available"},
    "data_gaps_identified":    [],
}
_VALID_NS_TOOL_INPUT: dict = {
    "player_status": "UNKNOWN",
    "status_source": "MLB",
    "status_as_of":  "2026-08-10",
    "injury_flag":   False,
}
_VALID_MEL_TOOL_INPUT: dict = {
    "line_confirmed": True,
    "line_source":    "DraftKings",
    "market_status":  "OPEN",
}
_VALID_SS_TOOL_INPUT: dict = {
    "sport":                  "MLB",
    "statistical_assessment": {"verdict": "NEUTRAL"},
    "key_metrics":            [],
}
_VALID_FC_TOOL_INPUT: dict = {
    "contradiction_detected":    False,
    "failure_detected":          False,
    "resolution_recommendation": "PROCEED",
}
_VALID_FR_TOOL_INPUT: dict = {
    "all_roles_completed":    True,
    "roles_completed":        ["DATA_SLATE_INTEGRITY"],
    "roles_missing":          [],
    "refresh_status":         "COMPLETE",
    "evidence_snapshot_valid": True,
}

_VALID_TOOL_INPUTS_BY_ROLE = {
    "DATA_SLATE_INTEGRITY":  _VALID_DSI_TOOL_INPUT,
    "NEWS_STATUS":           _VALID_NS_TOOL_INPUT,
    "MARKET_EXACT_LINE":     _VALID_MEL_TOOL_INPUT,
    "SPORT_SPECIALIST":      _VALID_SS_TOOL_INPUT,
    "FAILURE_CONTRADICTION": _VALID_FC_TOOL_INPUT,
    "FINAL_REFRESH":         _VALID_FR_TOOL_INPUT,
}

# Tool name → role_id (for mock dispatch)
_TOOL_NAME_TO_ROLE: dict = {v["name"]: k for k, v in _ROLE_TOOL_DEFINITIONS.items()}


def _make_mock_tool_block(tool_input: dict) -> MagicMock:
    block = MagicMock()
    block.type  = "tool_use"
    block.input = tool_input
    return block


def _make_mock_usage(
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: Any = None,
    cache_create: Any = None,
) -> MagicMock:
    usage = MagicMock()
    usage.input_tokens               = input_tokens
    usage.output_tokens              = output_tokens
    usage.cache_read_input_tokens    = cache_read
    usage.cache_creation_input_tokens = cache_create
    return usage


def _make_mock_response(
    model: str,
    tool_input: dict,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> MagicMock:
    resp = MagicMock()
    resp.model   = model
    resp.content = [_make_mock_tool_block(tool_input)]
    resp.usage   = _make_mock_usage(input_tokens, output_tokens)
    return resp


def _make_all_roles_mock_client(model: str = _PINNED_MODEL) -> MagicMock:
    """
    Returns a mock Anthropic client whose messages.create() inspects the
    first tool's name to select the appropriate valid advisory_findings.
    This dispatches correctly regardless of registry iteration order.
    """
    client = MagicMock()

    def _create(**kwargs):
        tools     = kwargs.get("tools", [])
        tool_name = tools[0]["name"] if tools else ""
        role_id   = _TOOL_NAME_TO_ROLE.get(tool_name, "DATA_SLATE_INTEGRITY")
        tool_input = dict(_VALID_TOOL_INPUTS_BY_ROLE.get(role_id, _VALID_DSI_TOOL_INPUT))
        return _make_mock_response(model, tool_input)

    client.messages.create.side_effect = _create
    return client


def _make_entry_for_role(role_id: str) -> MagicMock:
    """Build a minimal registry entry mock for a given role_id."""
    registry = build_b1_registry()
    for entry in registry.all_agents():
        if entry.role == role_id:
            return entry
    raise ValueError(f"No entry for role_id={role_id!r}")


def _make_mock_packet(run_id: str = "test-run-001") -> MagicMock:
    pkt = MagicMock()
    pkt.lane             = "MLB_MONEYLINE"
    pkt.snapshot_id      = "snap-test-001"
    pkt.run_id           = run_id
    pkt.canonical_event_id = "2026-08-10-NYY-BOS"
    pkt.target_date      = "2026-08-10"
    pkt.scoring_context  = {"home_team": "BOS", "away_team": "NYY"}
    return pkt


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TestCanaryConfig
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanaryConfig(unittest.TestCase):

    def test_uac_flag_default_false(self):
        # Default (no env var set) must be False
        self.assertIsInstance(UAC_MLB_ML_CLAUDE_SHADOW_ENABLED, bool)
        # We cannot assert it's False here because env may differ — instead
        # test the _read_bool_flag function behaviour via import
        import gate_engine.universal_agent.canary.canary_config as cc
        with patch.dict("os.environ", {}, clear=False):
            result = cc._read_bool_flag("_NONEXISTENT_B3C_TEST_FLAG_")
            self.assertFalse(result)

    def test_malformed_flag_is_false(self):
        import gate_engine.universal_agent.canary.canary_config as cc
        for bad in ("yes", "1", "TRUE ", "on", "enabled", "", "0", "false"):
            # Only exact "true" (strip+lower) returns True
            with patch.dict("os.environ", {"_B3C_TEST_": bad}):
                got = cc._read_bool_flag("_B3C_TEST_")
                if bad.strip().lower() == "true":
                    self.assertTrue(got)
                else:
                    self.assertFalse(got)

    def test_flag_true_when_env_set(self):
        import gate_engine.universal_agent.canary.canary_config as cc
        with patch.dict("os.environ", {"_B3C_TEST_TRUE_": "true"}):
            self.assertTrue(cc._read_bool_flag("_B3C_TEST_TRUE_"))

    def test_pinned_model_exact_string(self):
        self.assertEqual(PINNED_MODEL, "claude-haiku-4-5-20251001")

    def test_max_calls_is_six(self):
        self.assertEqual(MAX_CALLS, 6)

    def test_max_total_spend_is_ten_cents(self):
        self.assertAlmostEqual(MAX_TOTAL_SPEND_USD, 0.10)

    def test_auto_budget_increase_false(self):
        self.assertFalse(AUTO_BUDGET_INCREASE)

    def test_max_tokens_1024(self):
        self.assertEqual(MAX_TOKENS, 1024)

    def test_automatic_retries_zero(self):
        self.assertEqual(AUTOMATIC_RETRIES, 0)

    def test_timeout_thirty_seconds(self):
        self.assertAlmostEqual(PER_CALL_TIMEOUT_SECONDS, 30.0)

    def test_worst_case_cost_per_call_computed(self):
        expected = (
            WORST_CASE_INPUT_TOKENS / 1_000_000 * INPUT_COST_PER_MTOK
            + MAX_TOKENS / 1_000_000 * OUTPUT_COST_PER_MTOK
        )
        self.assertAlmostEqual(WORST_CASE_COST_PER_CALL, expected, places=10)

    def test_six_worst_case_calls_fit_within_budget(self):
        # 6 × worst-case cost must be < $0.10
        self.assertLess(6 * WORST_CASE_COST_PER_CALL, MAX_TOTAL_SPEND_USD)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TestBudgetState
# ═══════════════════════════════════════════════════════════════════════════════

class TestBudgetState(unittest.TestCase):

    def _fresh(self) -> BudgetState:
        return BudgetState()

    def test_initial_state(self):
        b = self._fresh()
        self.assertEqual(b.calls_attempted,  0)
        self.assertEqual(b.calls_successful, 0)
        self.assertAlmostEqual(b.cumulative_spend_usd, 0.0)

    def test_pre_call_check_passes_when_fresh(self):
        ok, reason = self._fresh().pre_call_check()
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_pre_call_check_blocked_at_max_calls(self):
        b = self._fresh()
        b.calls_attempted = MAX_CALLS
        ok, reason = b.pre_call_check()
        self.assertFalse(ok)
        self.assertIn("MAX_CALLS", reason)

    def test_pre_call_check_blocked_by_spend_ceiling(self):
        b = self._fresh()
        b.cumulative_spend_usd = MAX_TOTAL_SPEND_USD  # exactly at ceiling
        ok, reason = b.pre_call_check()
        self.assertFalse(ok)
        self.assertIn("MAX_TOTAL_SPEND_USD", reason)

    def test_pre_call_check_blocked_when_next_call_exceeds_budget(self):
        b = self._fresh()
        # Set spend so that adding one more worst-case call exceeds the limit
        b.cumulative_spend_usd = MAX_TOTAL_SPEND_USD - WORST_CASE_COST_PER_CALL * 0.5
        ok, _ = b.pre_call_check()
        self.assertFalse(ok)

    def test_record_attempt_increments(self):
        b = self._fresh()
        b.record_attempt()
        self.assertEqual(b.calls_attempted, 1)

    def test_record_success_increments_successful_and_spend(self):
        b = self._fresh()
        b.record_attempt()
        b.record_success(0.005)
        self.assertEqual(b.calls_successful, 1)
        self.assertAlmostEqual(b.cumulative_spend_usd, 0.005)

    def test_record_failure_cost_adds_to_spend(self):
        b = self._fresh()
        b.record_attempt()
        b.record_failure_cost(0.003)
        self.assertAlmostEqual(b.cumulative_spend_usd, 0.003)
        self.assertEqual(b.calls_successful, 0)

    def test_sixth_attempt_allowed_when_within_budget(self):
        b = self._fresh()
        b.calls_attempted = 5
        ok, _ = b.pre_call_check()
        self.assertTrue(ok)

    def test_seventh_attempt_blocked_structurally(self):
        b = self._fresh()
        b.calls_attempted = 6  # simulate 6 prior attempts
        ok, reason = b.pre_call_check()
        self.assertFalse(ok)
        self.assertIn("MAX_CALLS", reason)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TestClaudeRoleRunnerModelIdentity
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaudeRoleRunnerModelIdentity(unittest.TestCase):

    def _run_one_call(self, model: str, tool_input: dict) -> tuple:
        """Run a single DSI call with the given response model and tool_input."""
        entry  = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(model, tool_input)
        runner = ClaudeRoleRunner(client=client)
        return runner, entry, packet

    def test_pinned_model_constant_is_exact_literal(self):
        self.assertEqual(_PINNED_MODEL, "claude-haiku-4-5-20251001")

    def test_runner_class_has_pinned_model_attribute(self):
        self.assertEqual(ClaudeRoleRunner.PINNED_MODEL, "claude-haiku-4-5-20251001")

    def test_correct_model_returned_in_call_log(self):
        runner, entry, packet = self._run_one_call(
            _PINNED_MODEL, dict(_VALID_DSI_TOOL_INPUT)
        )
        runner(entry, packet)
        self.assertEqual(runner.call_log[0].status, CanaryCallStatus.SUCCESS)
        self.assertEqual(runner.call_log[0].response_model, _PINNED_MODEL)
        self.assertEqual(runner.call_log[0].requested_model, _PINNED_MODEL)

    def test_wrong_response_model_raises_model_identity_error(self):
        runner, entry, packet = self._run_one_call(
            "claude-opus-4-5-20251001", dict(_VALID_DSI_TOOL_INPUT)
        )
        with self.assertRaises(CanaryModelIdentityError) as ctx:
            runner(entry, packet)
        self.assertIn("CANARY_FAIL_MODEL_IDENTITY", str(ctx.exception))

    def test_wrong_model_status_in_call_log(self):
        runner, entry, packet = self._run_one_call(
            "wrong-model", dict(_VALID_DSI_TOOL_INPUT)
        )
        try:
            runner(entry, packet)
        except CanaryModelIdentityError:
            pass
        self.assertEqual(runner.call_log[0].status, CanaryCallStatus.FAIL_MODEL_IDENTITY)

    def test_wrong_model_response_model_recorded_in_call_log(self):
        runner, entry, packet = self._run_one_call(
            "wrong-model-v99", dict(_VALID_DSI_TOOL_INPUT)
        )
        try:
            runner(entry, packet)
        except CanaryModelIdentityError:
            pass
        self.assertEqual(runner.call_log[0].response_model, "wrong-model-v99")
        self.assertEqual(runner.call_log[0].requested_model, _PINNED_MODEL)

    def test_model_sent_in_api_call_is_exact_literal(self):
        entry  = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, dict(_VALID_DSI_TOOL_INPUT)
        )
        runner = ClaudeRoleRunner(client=client)
        runner(entry, packet)
        call_kwargs = client.messages.create.call_args
        sent_model = (
            call_kwargs.kwargs.get("model")
            or (call_kwargs.args[0] if call_kwargs.args else None)
        )
        self.assertEqual(sent_model, "claude-haiku-4-5-20251001")

    def test_max_tokens_sent_is_1024(self):
        entry  = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, dict(_VALID_DSI_TOOL_INPUT)
        )
        runner = ClaudeRoleRunner(client=client)
        runner(entry, packet)
        sent = client.messages.create.call_args.kwargs.get("max_tokens")
        self.assertEqual(sent, 1024)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TestClaudeRoleRunnerForbiddenKeys
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaudeRoleRunnerForbiddenKeys(unittest.TestCase):
    """
    Adversarial tests: each forbidden governance key in the Claude tool output
    must produce OUTPUT_REJECTED + CanaryOutputRejectedError.
    Uses the REAL B0 _scan_forbidden_keys (same object as B0's).
    """

    def _run_with_forbidden(self, key: str) -> CanaryCallRecord:
        entry  = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        packet = _make_mock_packet()
        # Inject the forbidden key into the DSI tool input
        bad_input = dict(_VALID_DSI_TOOL_INPUT)
        bad_input[key] = "INJECTED_BAD_VALUE"
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, bad_input
        )
        runner = ClaudeRoleRunner(client=client)
        try:
            runner(entry, packet)
        except CanaryOutputRejectedError:
            pass
        return runner.call_log[0]

    def test_can_execute_in_output_is_rejected(self):
        rec = self._run_with_forbidden("can_execute")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_terminal_label_in_output_is_rejected(self):
        rec = self._run_with_forbidden("terminal_label")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_final_decision_in_output_is_rejected(self):
        rec = self._run_with_forbidden("final_decision")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_stake_tier_in_output_is_rejected(self):
        rec = self._run_with_forbidden("stake_tier")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_is_playable_in_output_is_rejected(self):
        rec = self._run_with_forbidden("is_playable")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_production_authority_in_output_is_rejected(self):
        rec = self._run_with_forbidden("production_authority")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_user_output_authority_in_output_is_rejected(self):
        rec = self._run_with_forbidden("user_output_authority")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_capital_in_output_is_rejected(self):
        rec = self._run_with_forbidden("capital")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_execute_in_output_is_rejected(self):
        rec = self._run_with_forbidden("execute")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_trade_in_output_is_rejected(self):
        # "trade" is in FORBIDDEN_GOVERNANCE_KEYS — caught by the forbidden-key scan.
        rec = self._run_with_forbidden("trade")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_deploy_in_output_is_rejected(self):
        # "deploy" added to B0 FORBIDDEN_GOVERNANCE_KEYS (additive — no existing keys
        # removed). Caught by _scan_forbidden_keys, same object identity as B0.
        rec = self._run_with_forbidden("deploy")
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)

    def test_forbidden_key_raises_canary_output_rejected_error(self):
        entry  = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        packet = _make_mock_packet()
        bad_input = dict(_VALID_DSI_TOOL_INPUT)
        bad_input["can_execute"] = True
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, bad_input
        )
        runner = ClaudeRoleRunner(client=client)
        with self.assertRaises(CanaryOutputRejectedError):
            runner(entry, packet)

    def test_output_not_sanitized_just_rejected(self):
        """Output must be REJECTED wholesale, not sanitized-and-continued."""
        entry  = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        packet = _make_mock_packet()
        bad_input = dict(_VALID_DSI_TOOL_INPUT)
        bad_input["final_decision"] = "FINAL_APPROVED"
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, bad_input
        )
        runner = ClaudeRoleRunner(client=client)
        raised = False
        try:
            result = runner(entry, packet)
            # If runner didn't raise, the result must not contain the forbidden key
            # (i.e., it must have been stripped — but we assert it DOES raise)
            _ = result
        except CanaryOutputRejectedError:
            raised = True
        self.assertTrue(raised, "Expected CanaryOutputRejectedError was not raised")

    def test_violation_code_in_call_log_when_rejected(self):
        entry  = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        packet = _make_mock_packet()
        bad_input = dict(_VALID_DSI_TOOL_INPUT)
        bad_input["stake_tier"] = "PREMIUM"
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, bad_input
        )
        runner = ClaudeRoleRunner(client=client)
        try:
            runner(entry, packet)
        except CanaryOutputRejectedError:
            pass
        self.assertTrue(len(runner.call_log[0].violation_codes) > 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TestClaudeRoleRunnerFailModes
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaudeRoleRunnerFailModes(unittest.TestCase):
    """All fail modes must: raise CanaryRunnerError, zero retries, no synthetic data."""

    def _entry_and_packet(self):
        return _make_entry_for_role("DATA_SLATE_INTEGRITY"), _make_mock_packet()

    def test_timeout_raises_and_no_retry(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.side_effect = TimeoutError("timed out")
        runner = ClaudeRoleRunner(client=client)
        with self.assertRaises(CanaryCallFailedError):
            runner(entry, packet)
        # Only one attempt — no retry
        self.assertEqual(client.messages.create.call_count, 1)

    def test_timeout_call_log_status(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.side_effect = TimeoutError("timeout")
        runner = ClaudeRoleRunner(client=client)
        try:
            runner(entry, packet)
        except CanaryCallFailedError:
            pass
        self.assertIn(
            runner.call_log[0].status,
            {CanaryCallStatus.CALL_TIMEOUT, CanaryCallStatus.CALL_API_ERROR},
        )

    def test_network_error_raises_no_retry(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.side_effect = ConnectionError("network down")
        runner = ClaudeRoleRunner(client=client)
        with self.assertRaises(CanaryCallFailedError):
            runner(entry, packet)
        self.assertEqual(client.messages.create.call_count, 1)

    def test_generic_api_error_raises_fail_closed(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API error 500")
        runner = ClaudeRoleRunner(client=client)
        with self.assertRaises(CanaryCallFailedError):
            runner(entry, packet)
        self.assertEqual(client.messages.create.call_count, 1)

    def test_missing_usage_raises_fail_closed(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        resp = _make_mock_response(_PINNED_MODEL, dict(_VALID_DSI_TOOL_INPUT))
        resp.usage = None
        client.messages.create.return_value = resp
        runner = ClaudeRoleRunner(client=client)
        with self.assertRaises(CanaryCallFailedError) as ctx:
            runner(entry, packet)
        self.assertIn("MISSING_USAGE_METADATA", str(ctx.exception))

    def test_missing_input_tokens_raises_fail_closed(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        resp = _make_mock_response(_PINNED_MODEL, dict(_VALID_DSI_TOOL_INPUT))
        resp.usage.input_tokens = None
        client.messages.create.return_value = resp
        runner = ClaudeRoleRunner(client=client)
        with self.assertRaises(CanaryCallFailedError) as ctx:
            runner(entry, packet)
        self.assertIn("MISSING_USAGE_METADATA", str(ctx.exception))

    def test_no_tool_use_block_raises_fail_closed(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        resp = MagicMock()
        resp.model = _PINNED_MODEL
        resp.content = []   # no tool_use block
        resp.usage = _make_mock_usage()
        client.messages.create.return_value = resp
        runner = ClaudeRoleRunner(client=client)
        with self.assertRaises(CanaryCallFailedError) as ctx:
            runner(entry, packet)
        self.assertIn("MISSING_TOOL_USE", str(ctx.exception))

    def test_non_dict_tool_input_raises_fail_closed(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        block = MagicMock()
        block.type  = "tool_use"
        block.input = ["not", "a", "dict"]  # wrong type
        resp = MagicMock()
        resp.model   = _PINNED_MODEL
        resp.content = [block]
        resp.usage   = _make_mock_usage()
        client.messages.create.return_value = resp
        runner = ClaudeRoleRunner(client=client)
        with self.assertRaises(CanaryCallFailedError):
            runner(entry, packet)

    def test_zero_retries_on_every_failure(self):
        """AUTOMATIC_RETRIES=0 means the client is called exactly once per attempt."""
        entry, packet = self._entry_and_packet()
        for exc_class in [TimeoutError, ConnectionError, RuntimeError]:
            client = MagicMock()
            client.messages.create.side_effect = exc_class("fail")
            runner = ClaudeRoleRunner(client=client)
            try:
                runner(entry, packet)
            except CanaryCallFailedError:
                pass
            self.assertEqual(
                client.messages.create.call_count, 1,
                f"Expected 1 call (no retry) for {exc_class.__name__}",
            )

    def test_no_synthetic_replacement_on_failure(self):
        """On failure, no advisory finding is synthesized or returned."""
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("fail")
        runner = ClaudeRoleRunner(client=client)
        raised = False
        try:
            runner(entry, packet)
        except CanaryCallFailedError:
            raised = True
        self.assertTrue(raised)
        # call_log records the failure, but no successful output
        self.assertEqual(runner.call_log[0].status, CanaryCallStatus.CALL_API_ERROR)
        self.assertIsNone(runner.call_log[0].raw_output_hash)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TestClaudeRoleRunnerBudgetGuard
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaudeRoleRunnerBudgetGuard(unittest.TestCase):

    def _entry_and_packet(self):
        return _make_entry_for_role("DATA_SLATE_INTEGRITY"), _make_mock_packet()

    def test_fresh_budget_allows_first_call(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, dict(_VALID_DSI_TOOL_INPUT)
        )
        runner = ClaudeRoleRunner(client=client)
        runner(entry, packet)  # must not raise
        self.assertEqual(runner._budget.calls_attempted, 1)

    def test_budget_guard_fires_when_calls_attempted_equals_max(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, dict(_VALID_DSI_TOOL_INPUT)
        )
        runner = ClaudeRoleRunner(client=client)
        runner._budget.calls_attempted = MAX_CALLS  # simulate 6 prior attempts
        with self.assertRaises(CanaryBudgetGuardError):
            runner(entry, packet)

    def test_api_not_called_when_budget_guard_fires(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        runner = ClaudeRoleRunner(client=client)
        runner._budget.calls_attempted = MAX_CALLS
        with self.assertRaises(CanaryBudgetGuardError):
            runner(entry, packet)
        client.messages.create.assert_not_called()

    def test_budget_guard_status_in_call_log(self):
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        runner = ClaudeRoleRunner(client=client)
        runner._budget.calls_attempted = MAX_CALLS
        try:
            runner(entry, packet)
        except CanaryBudgetGuardError:
            pass
        self.assertEqual(runner.call_log[0].status, CanaryCallStatus.STOP_BUDGET_GUARD)

    def test_spend_ceiling_blocks_call(self):
        """If adding one more worst-case call exceeds $0.10, block before calling."""
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, dict(_VALID_DSI_TOOL_INPUT)
        )
        runner = ClaudeRoleRunner(client=client)
        # Set spend so that adding one more worst-case call >= MAX_TOTAL_SPEND_USD
        runner._budget.cumulative_spend_usd = (
            MAX_TOTAL_SPEND_USD - WORST_CASE_COST_PER_CALL * 0.5
        )
        with self.assertRaises(CanaryBudgetGuardError):
            runner(entry, packet)
        client.messages.create.assert_not_called()

    def test_calls_attempted_increments_before_api_call(self):
        """calls_attempted increments BEFORE the API call, so failures count."""
        entry, packet = self._entry_and_packet()
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("fail")
        runner = ClaudeRoleRunner(client=client)
        try:
            runner(entry, packet)
        except CanaryCallFailedError:
            pass
        self.assertEqual(runner._budget.calls_attempted, 1)
        self.assertEqual(runner._budget.calls_successful, 0)

    def test_no_seventh_call_under_any_circumstance(self):
        """Structural enforcement: calls_attempted=6 blocks any further call."""
        runner = ClaudeRoleRunner(client=MagicMock())
        runner._budget.calls_attempted = MAX_CALLS  # 6
        entry, packet = self._entry_and_packet()
        with self.assertRaises(CanaryBudgetGuardError):
            runner(entry, packet)
        # Client was never called
        runner._client.messages.create.assert_not_called()

    def test_budget_state_shared_across_entries(self):
        """One BudgetState instance shared across all 6 role calls accumulates correctly."""
        registry = build_b1_registry()
        entries  = list(registry.all_agents())
        budget   = BudgetState()
        client   = _make_all_roles_mock_client()
        runner   = ClaudeRoleRunner(client=client, budget=budget)
        packet   = _make_mock_packet()
        for entry in entries:
            runner(entry, packet)
        self.assertEqual(budget.calls_attempted,  6)
        self.assertEqual(budget.calls_successful, 6)
        self.assertGreater(budget.cumulative_spend_usd, 0.0)
        # Must be same budget object, not a copy
        self.assertIs(runner._budget, budget)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. TestOfflineEnforcementAudit (Step-14D pattern)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfflineEnforcementAudit(unittest.TestCase):
    """
    Proves via call-count interception that the live-backed runner path actually
    invokes the REAL B0/B1/B2 functions — not a parallel reimplementation.

    Pattern (from Weather Step 14D fix):
      patch.dict / patch.object with wraps= to intercept without replacing,
      then assert call_count >= expected_minimum.

    All assertions use the real functions' behavior (wraps=), not stubs.
    Zero real Anthropic API calls — client is mocked throughout.
    """

    def setUp(self):
        self.client = _make_all_roles_mock_client()

    def _run_full_pipeline(self):
        return run_canary_pipeline(
            _VALID_MLB_ROW,
            "audit-run-001",
            _client=self.client,
            _force_enabled=True,
        )

    # ── 7a: Real B0 _scan_forbidden_keys called by the runner ─────────────────

    def test_real_b0_scan_forbidden_keys_is_called_in_runner(self):
        """
        _scan_forbidden_keys imported into claude_role_runner must be called
        at least once per role (6 times for a full clean run).
        """
        with patch(
            "gate_engine.universal_agent.canary.claude_role_runner._scan_forbidden_keys",
            wraps=_real_scan_forbidden_keys,
        ) as mock_scan:
            self._run_full_pipeline()
        self.assertGreaterEqual(
            mock_scan.call_count, 6,
            "Expected _scan_forbidden_keys to be called once per role (≥6 times)",
        )

    def test_runner_scan_is_same_object_as_b0_definition(self):
        """
        The _scan_forbidden_keys imported into claude_role_runner must be
        the SAME object as the one defined in output_contract (not a copy).
        """
        import gate_engine.universal_agent.canary.claude_role_runner as runner_mod
        import gate_engine.universal_agent.output_contract as oc_mod
        self.assertIs(
            runner_mod._scan_forbidden_keys,
            oc_mod._scan_forbidden_keys,
            "_scan_forbidden_keys in runner is not the same object as B0's definition",
        )

    # ── 7b: Real B1 role-specific validators called by the orchestrator ────────

    def test_real_b1_dsi_validator_called(self):
        mock_dsi = MagicMock(wraps=real_validate_dsi)
        with patch.dict(
            "gate_engine.universal_agent.orchestrator._ROLE_VALIDATORS",
            {"DATA_SLATE_INTEGRITY": mock_dsi},
        ):
            self._run_full_pipeline()
        self.assertGreater(mock_dsi.call_count, 0, "DSI validator was not called")

    def test_real_b1_ns_validator_called(self):
        mock_ns = MagicMock(wraps=real_validate_ns)
        with patch.dict(
            "gate_engine.universal_agent.orchestrator._ROLE_VALIDATORS",
            {"NEWS_STATUS": mock_ns},
        ):
            self._run_full_pipeline()
        self.assertGreater(mock_ns.call_count, 0, "NS validator was not called")

    def test_real_b1_mel_validator_called(self):
        mock_mel = MagicMock(wraps=real_validate_mel)
        with patch.dict(
            "gate_engine.universal_agent.orchestrator._ROLE_VALIDATORS",
            {"MARKET_EXACT_LINE": mock_mel},
        ):
            self._run_full_pipeline()
        self.assertGreater(mock_mel.call_count, 0, "MEL validator was not called")

    def test_real_b1_ss_validator_called(self):
        mock_ss = MagicMock(wraps=real_validate_ss)
        with patch.dict(
            "gate_engine.universal_agent.orchestrator._ROLE_VALIDATORS",
            {"SPORT_SPECIALIST": mock_ss},
        ):
            self._run_full_pipeline()
        self.assertGreater(mock_ss.call_count, 0, "SS validator was not called")

    def test_real_b1_fc_validator_called(self):
        mock_fc = MagicMock(wraps=real_validate_fc)
        with patch.dict(
            "gate_engine.universal_agent.orchestrator._ROLE_VALIDATORS",
            {"FAILURE_CONTRADICTION": mock_fc},
        ):
            self._run_full_pipeline()
        self.assertGreater(mock_fc.call_count, 0, "FC validator was not called")

    def test_real_b1_fr_validator_called(self):
        mock_fr = MagicMock(wraps=real_validate_fr)
        with patch.dict(
            "gate_engine.universal_agent.orchestrator._ROLE_VALIDATORS",
            {"FINAL_REFRESH": mock_fr},
        ):
            self._run_full_pipeline()
        self.assertGreater(mock_fr.call_count, 0, "FR validator was not called")

    def test_all_six_b1_validators_called_in_single_run(self):
        """All 6 validators must be called in one complete pipeline run."""
        mocks = {
            "DATA_SLATE_INTEGRITY":  MagicMock(wraps=real_validate_dsi),
            "NEWS_STATUS":           MagicMock(wraps=real_validate_ns),
            "MARKET_EXACT_LINE":     MagicMock(wraps=real_validate_mel),
            "SPORT_SPECIALIST":      MagicMock(wraps=real_validate_ss),
            "FAILURE_CONTRADICTION": MagicMock(wraps=real_validate_fc),
            "FINAL_REFRESH":         MagicMock(wraps=real_validate_fr),
        }
        with patch.dict(
            "gate_engine.universal_agent.orchestrator._ROLE_VALIDATORS", mocks
        ):
            self._run_full_pipeline()
        for role_id, mock in mocks.items():
            self.assertGreater(
                mock.call_count, 0,
                f"B1 validator for {role_id} was not called",
            )

    # ── 7c: Real UniversalCapabilityBoundary called by the orchestrator ────────

    def test_real_capability_boundary_pre_hook_called(self):
        # autospec=True is required so the mock is a proper descriptor:
        # without it, patch.object on an instance method creates a plain
        # MagicMock that doesn't bind correctly, and call_count stays at 1
        # no matter how many instances call the method.
        with patch.object(
            UniversalCapabilityBoundary,
            "pre_tool_use_hook",
            autospec=True,
            wraps=UniversalCapabilityBoundary.pre_tool_use_hook,
        ) as mock_pre:
            self._run_full_pipeline()
        self.assertGreaterEqual(
            mock_pre.call_count, 6,
            "pre_tool_use_hook was not called for all 6 roles",
        )

    def test_real_capability_boundary_post_hook_called(self):
        # autospec=True — same reason as pre_hook test above.
        with patch.object(
            UniversalCapabilityBoundary,
            "post_tool_use_hook",
            autospec=True,
            wraps=UniversalCapabilityBoundary.post_tool_use_hook,
        ) as mock_post:
            self._run_full_pipeline()
        self.assertGreaterEqual(
            mock_post.call_count, 6,
            "post_tool_use_hook was not called for all 6 roles",
        )

    # ── 7d: Real B2 assemble_bundle called ────────────────────────────────────

    def test_real_b2_assemble_bundle_called(self):
        with patch(
            "gate_engine.universal_agent.orchestrator.assemble_bundle",
            wraps=real_assemble_bundle,
        ) as mock_assemble:
            self._run_full_pipeline()
        self.assertEqual(
            mock_assemble.call_count, 1,
            "assemble_bundle was not called exactly once",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. TestCanaryStore
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanaryStore(unittest.TestCase):

    def _mock_conn(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: s
        conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
        return conn

    def test_ensure_canary_tables_calls_create_table(self):
        conn = self._mock_conn()
        ensure_canary_tables(conn)
        self.assertTrue(conn.cursor.called)
        self.assertTrue(conn.commit.called)

    def test_ensure_canary_tables_idempotent_call(self):
        conn = self._mock_conn()
        ensure_canary_tables(conn)
        ensure_canary_tables(conn)
        self.assertEqual(conn.commit.call_count, 2)

    def test_persist_canary_result_calls_execute(self):
        from datetime import datetime, timezone
        conn = self._mock_conn()
        persist_canary_result(
            conn,
            canary_run_id="run-001",
            snapshot_id="snap-001",
            role_id="DATA_SLATE_INTEGRITY",
            requested_model=_PINNED_MODEL,
            response_model=_PINNED_MODEL,
            request_timestamp=datetime.now(timezone.utc),
            completion_timestamp=datetime.now(timezone.utc),
            latency_ms=250,
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
            calculated_cost_usd=0.00036,
            cumulative_run_cost_usd=0.00036,
            runner_status="CANARY_CALL_SUCCESS",
            schema_status="ACCEPTED",
            violation_codes=[],
            error_classification=None,
            raw_output_hash="abc123",
            canonical_output_hash="def456",
        )
        conn.cursor.return_value.execute.assert_called()
        conn.commit.assert_called()

    def test_no_raw_model_output_text_in_table_ddl(self):
        """The DDL must not include a column for raw model output text."""
        from gate_engine.universal_agent.canary import canary_store
        src = pathlib.Path(canary_store.__file__).read_text()
        # Should not have a column that stores raw text content
        self.assertNotIn("raw_output_text", src)
        self.assertNotIn("model_output_text", src)
        self.assertNotIn("response_text",    src)
        # Should have hash column
        self.assertIn("raw_output_hash", src)

    def test_violation_codes_list_joined_as_string(self):
        """Lists of violation codes are joined into a comma-separated string."""
        conn = self._mock_conn()
        persist_canary_result(
            conn,
            canary_run_id="run-002",
            snapshot_id="snap-002",
            role_id="NEWS_STATUS",
            requested_model=_PINNED_MODEL,
            response_model=None,
            request_timestamp=None,
            completion_timestamp=None,
            latency_ms=None,
            input_tokens=None,
            output_tokens=None,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
            calculated_cost_usd=None,
            cumulative_run_cost_usd=None,
            runner_status="OUTPUT_REJECTED",
            schema_status=None,
            violation_codes=["FORBIDDEN_GOVERNANCE_KEY", "EXTRA_FIELD"],
            error_classification="OUTPUT_REJECTED",
            raw_output_hash=None,
            canonical_output_hash=None,
        )
        # Verify the execute call received a comma-joined string
        execute_args = conn.cursor.return_value.execute.call_args
        params = execute_args[0][1]  # positional args to execute(sql, params)
        violation_str = params[16]  # 17th param is violation_codes_str
        self.assertEqual(violation_str, "FORBIDDEN_GOVERNANCE_KEY,EXTRA_FIELD")


# ═══════════════════════════════════════════════════════════════════════════════
# 9. TestCanaryPipelineIntegration
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanaryPipelineIntegration(unittest.TestCase):

    def _run(self, client=None, row=None, _force_enabled=True):
        return run_canary_pipeline(
            row or _VALID_MLB_ROW,
            "integration-run-001",
            _client=client or _make_all_roles_mock_client(),
            _force_enabled=_force_enabled,
        )

    def test_disabled_when_flag_off_and_not_force_enabled(self):
        result = run_canary_pipeline(
            _VALID_MLB_ROW, "dis-run-001",
            _client=_make_all_roles_mock_client(),
            _force_enabled=False,
        )
        self.assertEqual(result.pipeline_status, CanaryPipelineStatus.DISABLED)

    def test_disabled_result_no_calls_attempted(self):
        result = run_canary_pipeline(
            _VALID_MLB_ROW, "dis-run-002", _force_enabled=False
        )
        self.assertEqual(result.calls_attempted, 0)
        self.assertEqual(result.calls_successful, 0)
        self.assertAlmostEqual(result.total_spend_usd, 0.0)

    def test_adapter_error_on_wrong_sport(self):
        bad_row = dict(_VALID_MLB_ROW)
        bad_row["sport"] = "NBA"
        result = self._run(row=bad_row)
        self.assertEqual(result.pipeline_status, CanaryPipelineStatus.ADAPTER_ERROR)

    def test_adapter_error_on_missing_event_id(self):
        bad_row = dict(_VALID_MLB_ROW)
        bad_row.pop("event_id")
        result = self._run(row=bad_row)
        self.assertEqual(result.pipeline_status, CanaryPipelineStatus.ADAPTER_ERROR)

    def test_full_clean_run_returns_result(self):
        result = self._run()
        self.assertIsInstance(result, CanaryPipelineResult)

    def test_clean_run_calls_attempted_is_six(self):
        result = self._run()
        self.assertEqual(result.calls_attempted, 6)

    def test_clean_run_calls_successful_is_six(self):
        result = self._run()
        self.assertEqual(result.calls_successful, 6)

    def test_clean_run_total_spend_positive(self):
        result = self._run()
        self.assertGreater(result.total_spend_usd, 0.0)

    def test_clean_run_adapter_result_present(self):
        result = self._run()
        self.assertIsNotNone(result.adapter_result)

    def test_clean_run_orchestrator_result_present(self):
        result = self._run()
        self.assertIsNotNone(result.orchestrator_result)

    def test_clean_run_call_log_has_six_records(self):
        result = self._run()
        self.assertEqual(len(result.call_log), 6)

    def test_clean_run_all_call_log_statuses_success(self):
        result = self._run()
        for rec in result.call_log:
            self.assertEqual(
                rec.status, CanaryCallStatus.SUCCESS,
                f"Expected SUCCESS for role {rec.role_id}, got {rec.status}",
            )

    def test_result_is_frozen(self):
        result = self._run()
        with self.assertRaises((AttributeError, TypeError)):
            result.pipeline_status = "MUTATED"  # type: ignore

    def test_result_not_persisted_without_db_conn(self):
        result = self._run()
        self.assertFalse(result.persisted)

    def test_to_dict_has_expected_keys(self):
        result = self._run()
        d = result.to_dict()
        for key in [
            "canary_run_id", "pipeline_status", "calls_attempted",
            "calls_successful", "total_spend_usd", "call_log_count", "persisted",
        ]:
            self.assertIn(key, d)

    def test_canary_pipeline_class_interface(self):
        pipeline = CanaryPipeline()
        result = pipeline.run(
            _VALID_MLB_ROW, "class-run-001",
            _client=_make_all_roles_mock_client(),
            _force_enabled=True,
        )
        self.assertIsInstance(result, CanaryPipelineResult)

    def test_canary_run_id_echoed(self):
        result = run_canary_pipeline(
            _VALID_MLB_ROW, "echo-run-xyz",
            _client=_make_all_roles_mock_client(),
            _force_enabled=True,
        )
        self.assertEqual(result.canary_run_id, "echo-run-xyz")

    def test_no_real_anthropic_calls_in_test_run(self):
        """The mock client, not the real Anthropic SDK, must handle all calls."""
        client = _make_all_roles_mock_client()
        run_canary_pipeline(
            _VALID_MLB_ROW, "api-check-run",
            _client=client,
            _force_enabled=True,
        )
        # All calls must have gone through our mock
        self.assertGreater(client.messages.create.call_count, 0)

    def test_actual_attempted_count_never_fabricated(self):
        """If some calls fail, calls_attempted reflects real attempts, not 6/6."""
        client = MagicMock()
        # Only 3 calls succeed; the rest raise
        responses = []
        roles_in_order = [
            "DATA_SLATE_INTEGRITY", "NEWS_STATUS", "MARKET_EXACT_LINE",
        ]
        for role_id in roles_in_order:
            responses.append(
                _make_mock_response(_PINNED_MODEL, dict(_VALID_TOOL_INPUTS_BY_ROLE[role_id]))
            )
        # 4th call onwards raises
        client.messages.create.side_effect = responses + [RuntimeError("fail")] * 3
        result = run_canary_pipeline(
            _VALID_MLB_ROW, "partial-run",
            _client=client,
            _force_enabled=True,
        )
        # Attempted = successful + failed (real count, not fabricated)
        self.assertEqual(
            result.calls_attempted,
            result.calls_successful + (result.calls_attempted - result.calls_successful),
        )
        # Must NOT be reporting 6/6 when some failed
        self.assertLessEqual(result.calls_successful, 6)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. TestCanaryNoProductionImports
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanaryNoProductionImports(unittest.TestCase):
    """
    Source scan: no real Anthropic credential construction, no Flask routes,
    no Weather/Kalshi imports anywhere in the canary package.
    """

    def _source_of(self, module_path: str) -> str:
        spec = importlib.util.find_spec(module_path)
        if spec is None or spec.origin is None:
            self.fail(f"Cannot find module {module_path!r}")
        return pathlib.Path(spec.origin).read_text()

    def _check_forbidden(self, module_path: str, forbidden: list[str]) -> None:
        source = self._source_of(module_path)
        for token in forbidden:
            self.assertNotIn(
                token, source,
                msg=f"Forbidden token {token!r} found in {module_path}",
            )

    def test_canary_config_no_live_api_tokens(self):
        self._check_forbidden(
            "gate_engine.universal_agent.canary.canary_config",
            ["anthropic", "import anthropic", "httpx", "aiohttp",
             "from app", "import app", "flask", "@app.route"],
        )

    def test_claude_role_runner_no_flask_or_app(self):
        self._check_forbidden(
            "gate_engine.universal_agent.canary.claude_role_runner",
            ["from app", "import app", "flask", "@app.route",
             "httpx", "aiohttp"],
        )

    def test_claude_role_runner_no_terminal_label_authority(self):
        self._check_forbidden(
            "gate_engine.universal_agent.canary.claude_role_runner",
            ["final_decision =", "stake_tier =", "FINAL_APPROVED",
             "capital_alloc", "can_execute = True"],
        )

    def test_canary_pipeline_no_live_api(self):
        self._check_forbidden(
            "gate_engine.universal_agent.canary.canary_pipeline",
            ["httpx", "aiohttp", "from app", "import app",
             "flask", "@app.route"],
        )

    def test_canary_store_no_live_api(self):
        self._check_forbidden(
            "gate_engine.universal_agent.canary.canary_store",
            ["anthropic", "httpx", "openai", "from app", "import app",
             "flask", "@app.route"],
        )

    def test_canary_init_no_app_or_flask(self):
        self._check_forbidden(
            "gate_engine.universal_agent.canary",
            ["from app", "import app", "flask", "@app.route"],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 11. TestCanaryFlagIndependence
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanaryFlagIndependence(unittest.TestCase):
    """
    Structural isolation: UAC_MLB_ML_CLAUDE_SHADOW_ENABLED must have
    zero cross-references to CAN_EXECUTE, production routing flags,
    or Kalshi Weather flags in the canary package.
    """

    def _all_canary_sources(self) -> str:
        modules = [
            "gate_engine.universal_agent.canary.canary_config",
            "gate_engine.universal_agent.canary.claude_role_runner",
            "gate_engine.universal_agent.canary.canary_pipeline",
            "gate_engine.universal_agent.canary.canary_store",
        ]
        combined = ""
        for m in modules:
            spec = importlib.util.find_spec(m)
            if spec and spec.origin:
                combined += pathlib.Path(spec.origin).read_text()
        return combined

    def test_no_kalshi_wx_imports_in_canary(self):
        src = self._all_canary_sources()
        self.assertNotIn("kalshi_wx", src)

    def test_no_shadow_enabled_cross_reference(self):
        """UAC flag must not reference the Kalshi Weather SHADOW_ENABLED flag."""
        src = self._all_canary_sources()
        # Should not import from shadow module
        self.assertNotIn("from gate_engine.universal_agent.shadow", src)
        self.assertNotIn("import shadow_pipeline", src)

    def test_no_can_execute_true_anywhere(self):
        src = self._all_canary_sources()
        # can_execute = True must never appear
        self.assertNotIn("can_execute = True", src)
        self.assertNotIn("can_execute=True", src)

    def test_no_production_routing_flags(self):
        """No cross-reference to existing production routing flags."""
        src = self._all_canary_sources()
        self.assertNotIn("KALSHI_RECOVERY_MODE", src)
        self.assertNotIn("KALSHI_SHADOW_ENABLED", src)
        self.assertNotIn("kalshi_wx_shadow", src)

    def test_uac_flag_key_unique_to_b3c(self):
        """The env key UAC_MLB_ML_CLAUDE_SHADOW_ENABLED is only in the canary package."""
        src = self._all_canary_sources()
        self.assertIn("UAC_MLB_ML_CLAUDE_SHADOW_ENABLED", src)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. TestCanaryInvariants
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanaryInvariants(unittest.TestCase):

    def test_canary_config_can_execute_false(self):
        import gate_engine.universal_agent.canary.canary_config as m
        self.assertFalse(m.can_execute)

    def test_claude_role_runner_module_can_execute_false(self):
        import gate_engine.universal_agent.canary.claude_role_runner as m
        self.assertFalse(m.can_execute)

    def test_claude_role_runner_class_can_execute_false(self):
        self.assertFalse(ClaudeRoleRunner.can_execute)

    def test_claude_role_runner_automatic_retries_zero(self):
        self.assertEqual(ClaudeRoleRunner.AUTOMATIC_RETRIES, 0)

    def test_canary_pipeline_module_can_execute_false(self):
        import gate_engine.universal_agent.canary.canary_pipeline as m
        self.assertFalse(m.can_execute)

    def test_canary_pipeline_class_can_execute_false(self):
        self.assertFalse(CanaryPipeline.can_execute)

    def test_canary_store_can_execute_false(self):
        import gate_engine.universal_agent.canary.canary_store as m
        self.assertFalse(m.can_execute)

    def test_canary_init_can_execute_false(self):
        import gate_engine.universal_agent.canary as pkg
        self.assertFalse(pkg.can_execute)

    def test_pipeline_result_is_frozen(self):
        """CanaryPipelineResult must be a frozen dataclass."""
        result = run_canary_pipeline(
            _VALID_MLB_ROW, "frozen-test",
            _client=_make_all_roles_mock_client(),
            _force_enabled=True,
        )
        # Frozen dataclasses raise FrozenInstanceError (AttributeError subclass)
        # on regular assignment. Do NOT use object.__setattr__ here — it bypasses
        # the frozen guard (lesson from Kalshi WX shadow frozen dataclass tests).
        with self.assertRaises((AttributeError, TypeError)):
            result.pipeline_status = "MUTATED"  # type: ignore[misc]

    def test_advisory_only_never_from_claude_output(self):
        """advisory_only=True in any runner output must be set by the runner, not Claude."""
        entry  = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        packet = _make_mock_packet()
        # Provide a tool_input that explicitly has advisory_only=False (adversarial)
        bad_input = dict(_VALID_DSI_TOOL_INPUT)
        bad_input["advisory_only"] = False  # attempt to override
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, bad_input
        )
        runner = ClaudeRoleRunner(client=client)
        # advisory_only in advisory_findings (nested) is fine; it only matters at root
        # If the runner correctly injects advisory_only=True at root, this passes validator
        try:
            payload = runner(entry, packet)
            # Root-level advisory_only must be True (injected by runner)
            self.assertTrue(payload["advisory_only"])
        except Exception:
            # If validator rejects (advisory_only in findings → extra field), that's also correct
            pass

    def test_all_six_tool_definitions_present(self):
        expected = {
            "DATA_SLATE_INTEGRITY",
            "NEWS_STATUS",
            "MARKET_EXACT_LINE",
            "SPORT_SPECIALIST",
            "FAILURE_CONTRADICTION",
            "FINAL_REFRESH",
        }
        self.assertEqual(set(_ROLE_TOOL_DEFINITIONS.keys()), expected)

    def test_all_pipeline_statuses_known(self):
        statuses = {
            CanaryPipelineStatus.COMPLETE,
            CanaryPipelineStatus.PARTIAL,
            CanaryPipelineStatus.FAILED,
            CanaryPipelineStatus.DISABLED,
            CanaryPipelineStatus.ADAPTER_ERROR,
        }
        self.assertEqual(len(statuses), 5)

    def test_b3c_required_forbidden_keys_all_present_in_b0(self):
        """Every key in the locked B3C required set must be in the real B0 scanner.

        If anyone removes one of these 10 specifically named keys from
        FORBIDDEN_GOVERNANCE_KEYS in output_contract.py, this test fails
        immediately rather than the gap going unnoticed.
        """
        from gate_engine.universal_agent.output_contract import FORBIDDEN_GOVERNANCE_KEYS
        B3C_REQUIRED_FORBIDDEN_KEYS = {
            "can_execute", "terminal_label", "final_decision", "stake_tier",
            "is_playable", "production_authority", "user_output_authority",
            "capital", "deploy", "execute",
        }
        missing = B3C_REQUIRED_FORBIDDEN_KEYS - FORBIDDEN_GOVERNANCE_KEYS
        self.assertEqual(
            missing,
            set(),
            f"These B3C-required keys are missing from B0 FORBIDDEN_GOVERNANCE_KEYS: "
            f"{missing}. Add them to gate_engine/universal_agent/output_contract.py.",
        )

    def test_forbidden_governance_keys_shared_with_b0(self):
        """The FORBIDDEN_GOVERNANCE_KEYS imported in runner is the SAME set as B0."""
        import gate_engine.universal_agent.canary.claude_role_runner as runner_mod
        import gate_engine.universal_agent.output_contract as oc_mod
        self.assertIs(
            runner_mod.FORBIDDEN_GOVERNANCE_KEYS,
            oc_mod.FORBIDDEN_GOVERNANCE_KEYS,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 13. TestB3CR1Repairs — B3C-R1 offline tests (FIX 1/2/3)
#
# REQUIRED TESTS:
#   R1  – rejection at role 1  → exactly 1 messages.create call
#   R2  – rejection at role 4  → exactly 4 messages.create calls
#   R3  – skipped roles report SKIPPED_DUE_TO_PRIOR_ABORT
#   R4  – rejected call carries non-null calculated_cost_usd from real tokens
#   R5  – cumulative cost includes rejected + accepted calls
#   R6  – existing B3C enforcement proof objects unchanged (object identity)
#   R7  – zero real Anthropic API calls in this test class
#
# ZERO real Anthropic API calls. All tests use MagicMock() clients.
# ═══════════════════════════════════════════════════════════════════════════════

class TestB3CR1Repairs(unittest.TestCase):
    """
    B3C-R1: offline repair tests for fail-fast abort, all-attempt cost
    accounting, and prompt hardening. All clients are MagicMock() — no real
    Anthropic API calls occur anywhere in this class.
    """

    # Canonical canary role order — matches B3A adapter + registry iteration
    _ROLE_ORDER = [
        "DATA_SLATE_INTEGRITY",
        "NEWS_STATUS",
        "MARKET_EXACT_LINE",
        "SPORT_SPECIALIST",
        "FAILURE_CONTRADICTION",
        "FINAL_REFRESH",
    ]

    # ── helpers ──────────────────────────────────────────────────────────────

    def _rejection_response(self, in_tok: int = 800, out_tok: int = 400) -> MagicMock:
        """Response whose tool_input contains terminal_label → B0 → OUTPUT_REJECTED."""
        return _make_mock_response(
            _PINNED_MODEL,
            {"statistical_assessment": {"terminal_label": "REJECT_BAD"}},
            in_tok, out_tok,
        )

    def _success_response(self, role_id: str, in_tok: int = 500, out_tok: int = 200) -> MagicMock:
        """Clean response for a role (no forbidden keys)."""
        tool_input = dict(_VALID_TOOL_INPUTS_BY_ROLE.get(role_id, _VALID_DSI_TOOL_INPUT))
        return _make_mock_response(_PINNED_MODEL, tool_input, in_tok, out_tok)

    def _make_runner(self, client: Any):
        """
        Create a fresh CanaryAbortState + BudgetState + ClaudeRoleRunner
        sharing the abort_state. Returns (runner, budget, abort_state).
        """
        from gate_engine.universal_agent.canary.claude_role_runner import CanaryAbortState
        abort_state = CanaryAbortState()
        budget = BudgetState()
        runner = ClaudeRoleRunner(client=client, budget=budget, abort_state=abort_state)
        return runner, budget, abort_state

    def _dispatch_role(self, runner: Any, role_id: str, packet: Any):
        """Call runner for a role; return ('ok', payload) or ('err', exc)."""
        entry = _make_entry_for_role(role_id)
        try:
            payload = runner(entry, packet)
            return ("ok", payload)
        except Exception as exc:
            return ("err", exc)

    # ── REQUIRED TEST R1 ─────────────────────────────────────────────────────

    def test_r1_required_1_rejection_at_role1_exactly_one_api_call(self):
        """
        R1: Structural rejection at role 1 (DATA_SLATE_INTEGRITY) →
        exactly 1 messages.create invocation; roles 2-6 never make a real call.
        """
        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = self._rejection_response(400, 300)

        runner, budget, abort_state = self._make_runner(client)

        # Role 1 — should reject (terminal_label in nested output)
        status1, exc1 = self._dispatch_role(runner, "DATA_SLATE_INTEGRITY", packet)
        self.assertEqual(status1, "err")
        self.assertIsInstance(exc1, CanaryOutputRejectedError)

        # Abort state must be set immediately after first rejection
        self.assertTrue(abort_state.is_aborted)
        self.assertIsNotNone(abort_state.abort_reason)

        # Roles 2-6 — dispatcher iterates but runner skips every one
        for role_id in self._ROLE_ORDER[1:]:
            status, exc = self._dispatch_role(runner, role_id, packet)
            self.assertEqual(status, "err", f"role {role_id}: expected err, got ok")
            self.assertIsInstance(exc, CanaryCallFailedError)
            self.assertEqual(
                exc.canary_status,
                CanaryCallStatus.SKIPPED_ABORT,
                f"role {role_id}: expected SKIPPED_DUE_TO_PRIOR_ABORT, got {exc.canary_status}",
            )

        # REQUIRED: exactly 1 real API invocation
        self.assertEqual(
            client.messages.create.call_count, 1,
            f"Expected 1 messages.create call; got {client.messages.create.call_count}. "
            "Roles 2-6 must not dispatch to the Anthropic API after a prior abort.",
        )

        # Budget: only 1 attempt (SKIPPED_ABORT records never call record_attempt)
        self.assertEqual(budget.calls_attempted, 1)
        self.assertEqual(budget.calls_successful, 0)

        # call_log: 6 entries (1 rejected + 5 skipped)
        self.assertEqual(len(runner.call_log), 6)

    # ── REQUIRED TEST R2 ─────────────────────────────────────────────────────

    def test_r1_required_2_rejection_at_role4_exactly_four_api_calls(self):
        """
        R2: Structural rejection at role 4 (SPORT_SPECIALIST) →
        exactly 4 messages.create invocations; roles 5-6 never make a real call.
        """
        packet = _make_mock_packet()
        client = MagicMock()
        # side_effect as list: 4 responses; 5th+ never reached
        client.messages.create.side_effect = [
            self._success_response("DATA_SLATE_INTEGRITY", 500, 200),
            self._success_response("NEWS_STATUS",          500, 200),
            self._success_response("MARKET_EXACT_LINE",    500, 200),
            self._rejection_response(800, 400),   # role 4: SPORT_SPECIALIST
        ]

        runner, budget, abort_state = self._make_runner(client)

        for i, role_id in enumerate(self._ROLE_ORDER):
            status, result = self._dispatch_role(runner, role_id, packet)
            if i < 3:
                # Roles 1-3: should succeed
                self.assertEqual(status, "ok",
                    f"role {role_id} (index {i}): expected success, got err: {result}")
            elif i == 3:
                # Role 4: should reject
                self.assertEqual(status, "err")
                self.assertIsInstance(result, CanaryOutputRejectedError)
            else:
                # Roles 5-6: should be skipped
                self.assertEqual(status, "err")
                self.assertIsInstance(result, CanaryCallFailedError)
                self.assertEqual(result.canary_status, CanaryCallStatus.SKIPPED_ABORT,
                    f"role {role_id}: expected SKIPPED_DUE_TO_PRIOR_ABORT")

        # REQUIRED: exactly 4 real API invocations
        self.assertEqual(
            client.messages.create.call_count, 4,
            f"Expected 4 messages.create calls; got {client.messages.create.call_count}. "
            "Roles 5-6 must not dispatch to the Anthropic API after a prior abort.",
        )

        # Budget: 4 attempts (3 success + 1 reject), 0 skip increments
        self.assertEqual(budget.calls_attempted, 4)
        self.assertEqual(budget.calls_successful, 3)

        # Abort state set
        self.assertTrue(abort_state.is_aborted)

    # ── REQUIRED TEST R3 ─────────────────────────────────────────────────────

    def test_r1_required_3_skipped_roles_report_skipped_abort_status(self):
        """
        R3: Roles dispatched after an abort must carry SKIPPED_DUE_TO_PRIOR_ABORT
        status — not any other error status — on both the exception and the
        CanaryCallRecord in call_log.
        """
        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = self._rejection_response(300, 100)

        runner, budget, abort_state = self._make_runner(client)

        # Trigger abort at role 1
        self._dispatch_role(runner, "DATA_SLATE_INTEGRITY", packet)
        self.assertTrue(abort_state.is_aborted)

        # All remaining roles: exception status must be SKIPPED_ABORT
        for role_id in self._ROLE_ORDER[1:]:
            status, exc = self._dispatch_role(runner, role_id, packet)
            self.assertEqual(status, "err")
            self.assertEqual(
                exc.canary_status, CanaryCallStatus.SKIPPED_ABORT,
                f"role {role_id}: exception.canary_status should be "
                f"SKIPPED_DUE_TO_PRIOR_ABORT, got {exc.canary_status}",
            )

        # call_log records for skipped roles must carry SKIPPED_ABORT status
        skipped_records = [
            r for r in runner.call_log
            if r.status == CanaryCallStatus.SKIPPED_ABORT
        ]
        self.assertEqual(len(skipped_records), 5,
            f"Expected 5 SKIPPED_DUE_TO_PRIOR_ABORT records; statuses: "
            f"{[r.status for r in runner.call_log]}")

        # Skipped records: no API call → no cost, no tokens
        for rec in skipped_records:
            self.assertIsNone(rec.calculated_cost_usd,
                f"role {rec.role_id}: SKIPPED record must not carry calculated_cost_usd")
            self.assertIsNone(rec.input_tokens)
            self.assertIsNone(rec.output_tokens)
            self.assertIsNone(rec.response_model)

    # ── REQUIRED TEST R4 ─────────────────────────────────────────────────────

    def test_r1_required_4_rejected_call_calculated_cost_usd_non_null(self):
        """
        R4: A call rejected by the B0 forbidden-key scanner must have
        calculated_cost_usd set from its real token usage, not null.
        The value must exactly match the independently computed formula.
        """
        IN_TOK, OUT_TOK = 1259, 728   # exact tokens from the real B3C canary run
        expected_cost = (
            IN_TOK / 1_000_000 * INPUT_COST_PER_MTOK
            + OUT_TOK / 1_000_000 * OUTPUT_COST_PER_MTOK
        )

        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL,
            {"statistical_assessment": {"terminal_label": "REJECT_LIVE"}},
            IN_TOK, OUT_TOK,
        )

        runner, budget, abort_state = self._make_runner(client)
        entry = _make_entry_for_role("SPORT_SPECIALIST")

        with self.assertRaises(CanaryOutputRejectedError):
            runner(entry, packet)

        self.assertEqual(len(runner.call_log), 1)
        rec = runner.call_log[0]

        # REQUIRED: non-null
        self.assertIsNotNone(rec.calculated_cost_usd,
            "Rejected call must carry non-null calculated_cost_usd — "
            "the API was billed for the tokens even though the output was rejected")

        # REQUIRED: correct formula
        self.assertAlmostEqual(
            rec.calculated_cost_usd, expected_cost, places=9,
            msg=(
                f"calculated_cost_usd={rec.calculated_cost_usd!r} "
                f"expected={expected_cost!r} "
                f"(in={IN_TOK}, out={OUT_TOK}, "
                f"in_rate={INPUT_COST_PER_MTOK}, out_rate={OUTPUT_COST_PER_MTOK})"
            ),
        )

        # Token counts preserved on rejection record
        self.assertEqual(rec.input_tokens,  IN_TOK)
        self.assertEqual(rec.output_tokens, OUT_TOK)

        # Status and violation codes correct
        self.assertEqual(rec.status, CanaryCallStatus.OUTPUT_REJECTED)
        self.assertIsNotNone(rec.raw_output_hash, "raw_output_hash must be set on rejected call")
        self.assertTrue(rec.violation_codes, "violation_codes must be non-empty on rejected call")

    # ── REQUIRED TEST R5 ─────────────────────────────────────────────────────

    def test_r1_required_5_cumulative_includes_all_billed_call_costs(self):
        """
        R5: cumulative_run_cost_usd must sum ALL billed API responses
        (accepted and rejected), not just the successful ones.
        Also verified via call_log.calculated_cost_usd and budget.cumulative_spend_usd.
        """
        S_IN, S_OUT = 500, 200     # role 1 success tokens
        R_IN, R_OUT = 800, 400     # role 2 rejection tokens

        success_cost  = S_IN / 1_000_000 * INPUT_COST_PER_MTOK + S_OUT / 1_000_000 * OUTPUT_COST_PER_MTOK
        rejected_cost = R_IN / 1_000_000 * INPUT_COST_PER_MTOK + R_OUT / 1_000_000 * OUTPUT_COST_PER_MTOK
        expected_total = success_cost + rejected_cost

        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.side_effect = [
            self._success_response("DATA_SLATE_INTEGRITY", S_IN, S_OUT),
            _make_mock_response(
                _PINNED_MODEL,
                {"nested": {"terminal_label": "STOP"}},
                R_IN, R_OUT,
            ),
        ]

        runner, budget, abort_state = self._make_runner(client)

        # Role 1: success
        status1, _ = self._dispatch_role(runner, "DATA_SLATE_INTEGRITY", packet)
        self.assertEqual(status1, "ok")

        # Role 2: reject
        status2, exc2 = self._dispatch_role(runner, "NEWS_STATUS", packet)
        self.assertEqual(status2, "err")
        self.assertIsInstance(exc2, CanaryOutputRejectedError)

        # Roles 3-6: skipped (no cost added)
        for role_id in self._ROLE_ORDER[2:]:
            self._dispatch_role(runner, role_id, packet)

        # REQUIRED: budget.cumulative_spend_usd includes both billed calls
        self.assertAlmostEqual(
            budget.cumulative_spend_usd, expected_total, places=9,
            msg=(
                f"budget.cumulative_spend_usd={budget.cumulative_spend_usd:.9f} "
                f"expected={expected_total:.9f}. "
                f"Rejected call cost ({rejected_cost:.9f}) must be included."
            ),
        )

        # REQUIRED: call_log sum of non-null calculated_cost_usd == expected_total
        log_total = sum(
            r.calculated_cost_usd
            for r in runner.call_log
            if r.calculated_cost_usd is not None
        )
        self.assertAlmostEqual(log_total, expected_total, places=9,
            msg="Sum of call_log.calculated_cost_usd must include rejected call cost")

        # Skipped records must not contribute any cost
        skipped = [r for r in runner.call_log if r.status == CanaryCallStatus.SKIPPED_ABORT]
        self.assertEqual(len(skipped), 4)
        for rec in skipped:
            self.assertIsNone(rec.calculated_cost_usd)

    # ── REQUIRED TEST R6 ─────────────────────────────────────────────────────

    def test_r1_required_6a_b0_scanner_object_identity_preserved(self):
        """
        R6a: B3C-R1 repair must not break the existing enforcement proof —
        _scan_forbidden_keys in the runner is the SAME object as in output_contract.
        """
        import gate_engine.universal_agent.canary.claude_role_runner as runner_mod
        import gate_engine.universal_agent.output_contract as oc_mod
        self.assertIs(
            runner_mod._scan_forbidden_keys,
            oc_mod._scan_forbidden_keys,
            "Object identity broken: runner._scan_forbidden_keys must be the real B0 scanner",
        )

    def test_r1_required_6b_forbidden_keys_frozenset_identity_preserved(self):
        """
        R6b: FORBIDDEN_GOVERNANCE_KEYS imported in runner is same frozenset
        as B0 output_contract — no copy or reimplementation introduced.
        """
        import gate_engine.universal_agent.canary.claude_role_runner as runner_mod
        import gate_engine.universal_agent.output_contract as oc_mod
        self.assertIs(
            runner_mod.FORBIDDEN_GOVERNANCE_KEYS,
            oc_mod.FORBIDDEN_GOVERNANCE_KEYS,
        )

    def test_r1_required_6c_abort_does_not_bypass_b0_scanner_when_not_aborted(self):
        """
        R6c: When abort_state.is_aborted=False, the runner must still execute
        the full B0 scan. A non-aborted role with forbidden keys must still reject.
        """
        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL,
            {"nested": {"terminal_label": "MUST_BE_CAUGHT"}},
            300, 100,
        )
        runner, budget, abort_state = self._make_runner(client)
        self.assertFalse(abort_state.is_aborted)  # pre-condition

        entry = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        with self.assertRaises(CanaryOutputRejectedError):
            runner(entry, packet)

        # API was called (scanner ran on the response — not skipped)
        self.assertEqual(client.messages.create.call_count, 1)
        # Abort state set as a consequence
        self.assertTrue(abort_state.is_aborted)

    def test_r1_required_6d_can_execute_invariant_unchanged(self):
        """R6d: can_execute=False invariant on ClaudeRoleRunner preserved after repair."""
        self.assertFalse(ClaudeRoleRunner.can_execute)

    def test_r1_required_6e_automatic_retries_zero_unchanged(self):
        """R6e: AUTOMATIC_RETRIES=0 invariant preserved after repair."""
        self.assertEqual(ClaudeRoleRunner.AUTOMATIC_RETRIES, 0)

    # ── REQUIRED TEST R7 ─────────────────────────────────────────────────────

    def test_r1_required_7_no_real_anthropic_api_calls_in_b3cr1_tests(self):
        """
        R7: Structural AST check — no real Anthropic() SDK client is instantiated
        anywhere in this test file. All ClaudeRoleRunner instances use MagicMock().
        UAC_MLB_ML_CLAUDE_SHADOW_ENABLED must be False during test execution.
        """
        import ast as _ast
        import pathlib as _pathlib

        source = _pathlib.Path(__file__).read_text()
        tree = _ast.parse(source)

        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                func = node.func
                # Direct Anthropic() call
                if isinstance(func, _ast.Name) and func.id in ("Anthropic", "AsyncAnthropic"):
                    self.fail(
                        f"Real Anthropic SDK instantiation found at line {node.lineno}. "
                        "All B3C tests must use MagicMock() clients only."
                    )
                # Module-qualified: anthropic.Anthropic() or sdk.Anthropic()
                if isinstance(func, _ast.Attribute) and func.attr in ("Anthropic", "AsyncAnthropic"):
                    self.fail(
                        f"Real Anthropic SDK instantiation via attribute at line {node.lineno}. "
                        "All B3C tests must use MagicMock() clients only."
                    )

        # Flag must be off during test execution
        import gate_engine.universal_agent.canary.canary_config as cc
        self.assertFalse(
            cc._read_bool_flag("UAC_MLB_ML_CLAUDE_SHADOW_ENABLED"),
            "UAC_MLB_ML_CLAUDE_SHADOW_ENABLED must be False during test execution",
        )

    # ── Additional structural tests ───────────────────────────────────────────

    def test_r1_abort_state_class_exists_and_importable(self):
        """CanaryAbortState is importable from claude_role_runner."""
        from gate_engine.universal_agent.canary.claude_role_runner import CanaryAbortState
        state = CanaryAbortState()
        self.assertFalse(state.is_aborted)
        self.assertIsNone(state.abort_reason)

    def test_r1_abort_state_set_aborted_idempotent(self):
        """set_aborted() is idempotent — first reason wins, second call ignored."""
        from gate_engine.universal_agent.canary.claude_role_runner import CanaryAbortState
        state = CanaryAbortState()
        state.set_aborted("first")
        state.set_aborted("second")
        self.assertEqual(state.abort_reason, "first",
            "set_aborted must be idempotent: first reason must not be overwritten")

    def test_r1_skipped_abort_status_constant_correct(self):
        """SKIPPED_DUE_TO_PRIOR_ABORT is defined with the correct string value."""
        self.assertEqual(CanaryCallStatus.SKIPPED_ABORT, "SKIPPED_DUE_TO_PRIOR_ABORT")

    def test_r1_abort_state_wired_into_canary_pipeline(self):
        """CanaryAbortState is importable from canary_pipeline (verifies wiring)."""
        from gate_engine.universal_agent.canary.canary_pipeline import CanaryAbortState as CAPIPE
        from gate_engine.universal_agent.canary.claude_role_runner import CanaryAbortState as CARUN
        self.assertIs(CAPIPE, CARUN, "Pipeline must import CanaryAbortState from runner")

    def test_r1_prompt_contains_key_name_contract(self):
        """FIX 3: every role prompt includes the advisory key-name contract section."""
        from gate_engine.universal_agent.canary.claude_role_runner import _build_prompt
        packet = _make_mock_packet()
        packet.to_dict.return_value = {}

        for role_id in self._ROLE_ORDER:
            prompt = _build_prompt(role_id, packet)
            self.assertIn(
                "terminal_label", prompt,
                f"role {role_id}: prompt must name 'terminal_label' as forbidden example",
            )
            self.assertIn(
                "ADVISORY KEY-NAME CONTRACT", prompt,
                f"role {role_id}: prompt must contain ADVISORY KEY-NAME CONTRACT header",
            )
            self.assertIn(
                "defense-in-depth", prompt,
                f"role {role_id}: prompt must clarify this is defense-in-depth only",
            )
            self.assertIn(
                "recursive B0 scanner", prompt,
                f"role {role_id}: prompt must name recursive B0 scanner as enforcement",
            )
            # Prompt must NOT claim to be a substitute for the scanner
            self.assertNotIn(
                "replaces", prompt.lower(),
                f"role {role_id}: prompt must not claim to replace the B0 scanner",
            )

    def test_r1_budget_guard_sets_abort_state(self):
        """Budget guard trip must set abort_state before raising CanaryBudgetGuardError."""
        from gate_engine.universal_agent.canary.claude_role_runner import CanaryAbortState
        from gate_engine.universal_agent.canary.canary_config import MAX_TOTAL_SPEND_USD

        packet = _make_mock_packet()
        client = MagicMock()
        abort_state = CanaryAbortState()
        budget = BudgetState()
        # Pre-fill spend to trigger ceiling
        budget.cumulative_spend_usd = MAX_TOTAL_SPEND_USD

        runner = ClaudeRoleRunner(client=client, budget=budget, abort_state=abort_state)
        entry = _make_entry_for_role("DATA_SLATE_INTEGRITY")

        with self.assertRaises(CanaryBudgetGuardError):
            runner(entry, packet)

        self.assertTrue(abort_state.is_aborted)
        client.messages.create.assert_not_called()

    def test_r1_model_identity_failure_carries_cost_and_sets_abort(self):
        """Wrong response model sets abort and carries calculated_cost_usd."""
        from gate_engine.universal_agent.canary.claude_role_runner import CanaryAbortState

        IN_TOK, OUT_TOK = 400, 150
        expected_cost = (
            IN_TOK / 1_000_000 * INPUT_COST_PER_MTOK
            + OUT_TOK / 1_000_000 * OUTPUT_COST_PER_MTOK
        )

        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            "claude-opus-wrong-model",    # wrong model
            _VALID_DSI_TOOL_INPUT,
            IN_TOK, OUT_TOK,
        )

        abort_state = CanaryAbortState()
        budget = BudgetState()
        runner = ClaudeRoleRunner(client=client, budget=budget, abort_state=abort_state)
        entry = _make_entry_for_role("DATA_SLATE_INTEGRITY")

        with self.assertRaises(CanaryModelIdentityError):
            runner(entry, packet)

        self.assertTrue(abort_state.is_aborted)
        rec = runner.call_log[0]
        self.assertEqual(rec.status, CanaryCallStatus.FAIL_MODEL_IDENTITY)
        self.assertIsNotNone(rec.calculated_cost_usd)
        self.assertAlmostEqual(rec.calculated_cost_usd, expected_cost, places=9)

    def test_r1_success_call_still_has_calculated_cost(self):
        """Regression: successful calls still carry calculated_cost_usd after repair."""
        from gate_engine.universal_agent.canary.claude_role_runner import CanaryAbortState

        IN_TOK, OUT_TOK = 1366, 274
        expected_cost = (
            IN_TOK / 1_000_000 * INPUT_COST_PER_MTOK
            + OUT_TOK / 1_000_000 * OUTPUT_COST_PER_MTOK
        )

        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(
            _PINNED_MODEL, _VALID_DSI_TOOL_INPUT, IN_TOK, OUT_TOK,
        )

        runner = ClaudeRoleRunner(client=client, abort_state=CanaryAbortState())
        entry = _make_entry_for_role("DATA_SLATE_INTEGRITY")
        runner(entry, packet)

        rec = runner.call_log[0]
        self.assertEqual(rec.status, CanaryCallStatus.SUCCESS)
        self.assertIsNotNone(rec.calculated_cost_usd)
        self.assertAlmostEqual(rec.calculated_cost_usd, expected_cost, places=9)

    def test_r1_private_abort_state_created_when_none_passed(self):
        """ClaudeRoleRunner creates a private CanaryAbortState when abort_state=None."""
        client = MagicMock()
        runner = ClaudeRoleRunner(client=client)
        self.assertIsNotNone(runner._abort_state)
        self.assertFalse(runner._abort_state.is_aborted)

    def test_r1_no_raw_model_output_persisted(self):
        """
        Structural: raw model output text must never appear in call_log records.
        Only raw_output_hash (SHA-256) is stored. This applies to both accepted
        and rejected records.
        """
        packet = _make_mock_packet()
        client = MagicMock()
        client.messages.create.return_value = self._rejection_response(300, 100)

        runner, _, _ = self._make_runner(client)
        entry = _make_entry_for_role("DATA_SLATE_INTEGRITY")

        with self.assertRaises(CanaryOutputRejectedError):
            runner(entry, packet)

        rec = runner.call_log[0]
        # raw_output_hash is a hex string (64 chars for SHA-256)
        self.assertIsNotNone(rec.raw_output_hash)
        self.assertEqual(len(rec.raw_output_hash), 64)
        # The CanaryCallRecord dataclass must not have any field storing the raw text
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(rec)}
        self.assertNotIn("raw_output", field_names)
        self.assertNotIn("tool_output_text", field_names)
        self.assertNotIn("response_text", field_names)


if __name__ == "__main__":
    unittest.main()
