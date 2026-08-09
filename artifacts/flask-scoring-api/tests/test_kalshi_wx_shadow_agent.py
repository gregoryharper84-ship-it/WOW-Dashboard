"""
tests/test_kalshi_wx_shadow_agent.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 10.1 tests

Tests for gate_engine/kalshi_wx_shadow_agent.py.

No live API calls are made.  Every test supplies a mock sdk_client so the
Anthropic SDK is exercised only at the interface boundary — the actual HTTP
request is never issued.  No network access required, no API cost incurred.

Test plan
─────────
T1: Mocked subagent returns a fully valid, schema-compliant payload.
    Confirm the adapter returns SHADOW_PASS (passed=True) and the result is
    the same object returned by validate_shadow_output (not a raw dict).

T2: Mocked subagent returns a payload containing a forbidden governance key
    ("can_execute") buried inside the probabilities object.
    Confirm the adapter returns a shadow-failure result (passed=False,
    shadow_failure_only=True, violation=FORBIDDEN_GOVERNANCE_KEY).
    Confirm the raw payload itself is NOT returned.

T3: Mocked SDK call raises an exception (RuntimeError simulating a network
    timeout).
    Confirm the adapter catches it, returns a shadow-failure ShadowValidationResult
    (passed=False, shadow_failure_only=True), and does NOT raise, does NOT
    return None, and does NOT return a raw dict.

T4: Structural / AST check — every return statement in
    invoke_forecast_context_agent calls either validate_shadow_output() or
    _call_failure().  No return path can return a raw dict, None, or any value
    that has not passed through one of those two functions.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import textwrap
import unittest
from unittest.mock import MagicMock

# ── path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gate_engine.kalshi_wx_shadow_agent import (
    invoke_forecast_context_agent,
    _call_failure,
    _AGENT_ID,
)
from gate_engine.kalshi_wx_shadow_schema import (
    SHADOW_PASS,
    ShadowSchemaViolation,
    ShadowValidationResult,
    validate_shadow_output,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _valid_agent_payload(run_id: str = "run-test-001") -> dict:
    """
    A fully schema-compliant payload matching every constraint in Step 9.
    This is what a well-behaved subagent should return.
    """
    return {
        "agent_id":               _AGENT_ID,
        "run_id":                 run_id,
        "lane":                   "KALSHI_WEATHER",
        "status":                 "COMPLETE",
        "facts": {
            "city":               "NYC",
            "date":               "2026-08-08",
            "scoring_mode":       "gaussian_forecast",
            "forecast_high_f":    88.0,
        },
        "probabilities": {
            "model_prob_sum":     1.0,
            "calibration_status": "CALIBRATED",
        },
        "uncertainty": {
            "horizon_hours":      18.0,
            "sigma_f":            4.2,
            "uncertainty_tier":   "MODERATE",
        },
        "agent_observed_blockers": [],
        "source_conflicts":        [],
        "recommended_ceiling":     "KALSHI_WATCH",
        "advisory_only":           True,
    }


def _make_mock_client(payload: dict) -> MagicMock:
    """
    Return a mock Anthropic client whose messages.create() returns a response
    containing the JSON-serialised payload as its first content block.
    """
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_content_block = MagicMock()
    mock_content_block.text = json.dumps(payload)
    mock_response.content = [mock_content_block]
    mock_client.messages.create.return_value = mock_response
    return mock_client


def _make_raising_mock_client(exc: Exception) -> MagicMock:
    """
    Return a mock Anthropic client whose messages.create() raises `exc`.
    """
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = exc
    return mock_client


# ─────────────────────────────────────────────────────────────────────────────
# T1 — Valid payload passes through validator unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestT1ValidPayloadPassesThrough(unittest.TestCase):

    def test_T1_valid_payload_passes_through_validator(self):
        """
        When the mocked subagent returns a fully valid payload, the adapter
        must return SHADOW_PASS — the same singleton object returned by
        validate_shadow_output on success.

        Assertions:
          1. result.passed is True
          2. result is the SHADOW_PASS singleton (not a copy, not a new object)
          3. result is a ShadowValidationResult instance
          4. The mock's messages.create was called exactly once (adapter did
             not retry or call it multiple times)
        """
        payload = _valid_agent_payload(run_id="run-test-t1")
        mock_client = _make_mock_client(payload)

        result = invoke_forecast_context_agent(
            city="NYC",
            date="2026-08-08",
            run_id="run-test-t1",
            sdk_client=mock_client,
        )

        # 1. Must pass
        self.assertTrue(
            result.passed,
            f"Expected SHADOW_PASS but got passed=False: "
            f"violation={result.violation}, reason={result.failure_reason!r}",
        )

        # 2. Must be the SHADOW_PASS singleton — not a raw dict, not a copy
        self.assertIs(
            result, SHADOW_PASS,
            "Adapter must return the SHADOW_PASS singleton on a valid payload, "
            "not a newly-constructed object or a raw dict.",
        )

        # 3. Type check (belt-and-suspenders)
        self.assertIsInstance(result, ShadowValidationResult)

        # 4. SDK called exactly once — no silent retry
        mock_client.messages.create.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# T2 — Forbidden governance key returns shadow-failure, not raw payload
# ─────────────────────────────────────────────────────────────────────────────

class TestT2ForbiddenKeyReturnsShadowFailure(unittest.TestCase):

    def test_T2_forbidden_key_returns_shadow_failure_not_raw_payload(self):
        """
        When the mocked subagent returns a payload containing a forbidden
        governance key ("can_execute": False inside probabilities), the adapter
        must return a closed shadow-failure result — not the raw payload dict.

        Assertions:
          1. result.passed is False
          2. result.shadow_failure_only is True
          3. result.violation is FORBIDDEN_GOVERNANCE_KEY
          4. result is a ShadowValidationResult, not a dict
          5. The raw payload dict is NOT returned (confirm result is not the
             injected payload and not any dict subtype)
        """
        payload = _valid_agent_payload(run_id="run-test-t2")
        # Inject a forbidden governance key inside probabilities
        payload["probabilities"]["can_execute"] = False   # FORBIDDEN

        mock_client = _make_mock_client(payload)

        result = invoke_forecast_context_agent(
            city="NYC",
            date="2026-08-08",
            run_id="run-test-t2",
            sdk_client=mock_client,
        )

        # 1. Must fail
        self.assertFalse(result.passed,
                         "Payload with forbidden key must not pass validation")

        # 2. shadow_failure_only must be True
        self.assertTrue(
            result.shadow_failure_only,
            "Failure result must have shadow_failure_only=True",
        )

        # 3. Correct violation type
        self.assertEqual(
            result.violation,
            ShadowSchemaViolation.FORBIDDEN_GOVERNANCE_KEY,
            f"Expected FORBIDDEN_GOVERNANCE_KEY, got {result.violation}",
        )

        # 4. Result is a ShadowValidationResult, never a raw dict
        self.assertIsInstance(result, ShadowValidationResult)
        self.assertNotIsInstance(result, dict)

        # 5. The raw payload was not returned
        self.assertIsNot(result, payload,
                         "Adapter must not return the raw payload dict")


# ─────────────────────────────────────────────────────────────────────────────
# T3 — SDK exception produces closed failure, not a crash or None
# ─────────────────────────────────────────────────────────────────────────────

class TestT3SdkExceptionReturnsClosed(unittest.TestCase):

    def test_T3_sdk_exception_returns_closed_failure_not_none(self):
        """
        When the underlying SDK call raises an exception (RuntimeError
        simulating a network timeout), the adapter must:
          - Not re-raise the exception
          - Not return None
          - Not return a raw dict
          - Return a ShadowValidationResult with passed=False and
            shadow_failure_only=True

        Assertions:
          1. No exception is raised by invoke_forecast_context_agent
          2. result is not None
          3. result is a ShadowValidationResult instance
          4. result.passed is False
          5. result.shadow_failure_only is True
          6. result.failure_reason contains the AGENT_CALL_FAILURE prefix and
             the exception class name, confirming it's a call failure not a
             schema failure
        """
        exc = RuntimeError("simulated network timeout")
        mock_client = _make_raising_mock_client(exc)

        # Assertion 1 — must not raise
        try:
            result = invoke_forecast_context_agent(
                city="CHI",
                date="2026-08-09",
                run_id="run-test-t3",
                sdk_client=mock_client,
            )
        except Exception as unexpected:
            self.fail(
                f"invoke_forecast_context_agent raised an unexpected exception: "
                f"{type(unexpected).__name__}: {unexpected}"
            )

        # 2. Not None
        self.assertIsNotNone(result, "Adapter must not return None on SDK exception")

        # 3. Correct type
        self.assertIsInstance(result, ShadowValidationResult)

        # 4. Must fail
        self.assertFalse(result.passed)

        # 5. shadow_failure_only must be True
        self.assertTrue(
            result.shadow_failure_only,
            "Call-failure result must have shadow_failure_only=True",
        )

        # 6. failure_reason identifies this as an AGENT_CALL_FAILURE and names
        #    the exception class so the caller can distinguish SDK errors from
        #    schema violations
        self.assertIn(
            "AGENT_CALL_FAILURE",
            result.failure_reason,
            f"failure_reason should start with AGENT_CALL_FAILURE prefix; "
            f"got {result.failure_reason!r}",
        )
        self.assertIn(
            "RuntimeError",
            result.failure_reason,
            f"failure_reason should name the exception class; "
            f"got {result.failure_reason!r}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# T4 — Structural / AST check: every return path goes through the validator
# ─────────────────────────────────────────────────────────────────────────────

class TestT4StructuralReturnPathCheck(unittest.TestCase):

    def test_T4_every_return_path_goes_through_validator(self):
        """
        AST-level assertion: every return statement in
        invoke_forecast_context_agent must be a direct call to either
        validate_shadow_output() or _call_failure().

        This rules out:
          - `return None`          (bare return or explicit None)
          - `return payload`       (raw variable — dict not validated)
          - `return {}`            (dict literal)
          - `return result`        (intermediate variable — bypasses validator)
          - any other non-call return

        The only two allowed call targets are:
          • validate_shadow_output  — the Step 9 schema validator
          • _call_failure           — the SDK-error closed-failure helper,
                                      which itself constructs ShadowValidationResult

        Approach: parse the function source with ast.parse, find every
        ast.Return node inside the function body, and assert each one's value
        is an ast.Call whose function name is in the allowed set.
        """
        _ALLOWED_RETURN_CALLS = {"validate_shadow_output", "_call_failure"}

        # Get the function source and dedent so ast.parse sees a module-level def
        raw_source = inspect.getsource(invoke_forecast_context_agent)
        source = textwrap.dedent(raw_source)

        tree = ast.parse(source)

        # Locate the function definition node
        func_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "invoke_forecast_context_agent"
        ]
        self.assertEqual(
            len(func_defs), 1,
            "Expected exactly one FunctionDef named invoke_forecast_context_agent",
        )
        func_def = func_defs[0]

        # Collect all Return nodes inside the function
        return_nodes = [
            node for node in ast.walk(func_def)
            if isinstance(node, ast.Return)
        ]
        self.assertGreater(
            len(return_nodes), 0,
            "No return statements found — function must have at least one",
        )

        violations = []
        for ret_node in return_nodes:
            val = ret_node.value

            # Bare `return` with no value
            if val is None:
                violations.append(
                    f"Line {ret_node.lineno}: bare `return` (no value) — "
                    f"must return validate_shadow_output(...) or _call_failure(...)"
                )
                continue

            # Return value must be a Call node
            if not isinstance(val, ast.Call):
                violations.append(
                    f"Line {ret_node.lineno}: `return {ast.unparse(val)}` — "
                    f"return value is not a function call; "
                    f"must call validate_shadow_output or _call_failure"
                )
                continue

            # The called function must be in the allowed set
            called_name = ast.unparse(val.func)
            if called_name not in _ALLOWED_RETURN_CALLS:
                violations.append(
                    f"Line {ret_node.lineno}: `return {ast.unparse(val)}` — "
                    f"calls {called_name!r} which is not in the allowed set "
                    f"{_ALLOWED_RETURN_CALLS}"
                )

        if violations:
            self.fail(
                f"The following return statements bypass the validator:\n"
                + "\n".join(f"  • {v}" for v in violations)
            )


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
