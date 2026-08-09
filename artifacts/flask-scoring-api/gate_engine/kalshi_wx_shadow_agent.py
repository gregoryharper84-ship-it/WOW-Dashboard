"""
gate_engine/kalshi_wx_shadow_agent.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 10.1

Proof-of-concept: one subagent (forecast-context interpretation) wired to the
Step 9 closed schema validator.  This is the minimal SDK → schema path.

OUT OF SCOPE (deliberately excluded from this module):
  - Any Flask route registration or change to any existing route
  - Any orchestrator or hook
  - Any additional subagents beyond the one defined here
  - Any changes to the schema module or registry module (import only, no edits)
  - Any interaction with either existing ceiling resolver
  - Any tool that writes, places orders, or calls any execution endpoint
  - Any live trading code of any kind

VALIDATOR INVARIANT (non-negotiable)
  invoke_forecast_context_agent() NEVER returns raw model output to its caller.
  Every return statement in that function calls either:
    • validate_shadow_output(payload)  — the Step 9 schema validator
    • _call_failure(reason)            — SDK-level error before schema is reached
  Both return ShadowValidationResult instances.  No dict, no None, and no raw
  model text ever escapes this function.  The structural test in the test file
  (test_T4) enforces this with an AST-level assertion.

ENVIRONMENT
  The anthropic SDK (v0.102.0) is installed.  Live API calls are gated on the
  API key being present in the environment.  Tests supply a mock client via the
  sdk_client parameter so no live network access or API cost is required.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from gate_engine.kalshi_wx_shadow_schema import (
    ShadowSchemaViolation,
    ShadowValidationResult,
    validate_shadow_output,
)

# ── SDK import ────────────────────────────────────────────────────────────────
# Imported at module level for type resolution; live client construction is
# gated on the API key at runtime so tests can run without network access.
try:
    import anthropic as _anthropic_sdk
    _SDK_AVAILABLE = True
except ImportError:
    _anthropic_sdk = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False

# ── Agent identity and model ──────────────────────────────────────────────────
_AGENT_ID: str = "kalshi-wx-forecast-context-agent-v1"
_MODEL: str = "claude-3-5-haiku-20241022"
_MAX_TOKENS: int = 1024

# ── Feature flag — default OFF ────────────────────────────────────────────────
# Live agent invocations are disabled unless this flag is explicitly set.
# Read from environment variable KALSHI_WX_SHADOW_AGENT_ENABLED.
# Any value other than "true" (case-insensitive) — including absent — is False.
#
# With the flag off (the default), invoke_forecast_context_agent() is guaranteed
# to make zero network calls regardless of whether an API key is present.
# The flag is the FIRST check in the function, before _build_client() is called,
# before any SDK object is constructed, and before any network activity occurs.
KALSHI_WX_SHADOW_AGENT_ENABLED: bool = (
    os.environ.get("KALSHI_WX_SHADOW_AGENT_ENABLED", "false").strip().lower() == "true"
)

# ── System prompt ─────────────────────────────────────────────────────────────
# Scoped exclusively to the KALSHI_WEATHER lane.  No tools are granted; no
# write capability of any kind is described or available.
_SYSTEM_PROMPT: str = """\
You are a read-only forecast-context interpretation agent operating exclusively
in the KALSHI_WEATHER lane of the WOW shadow research pilot.

YOUR ROLE
Interpret weather forecast context for Kalshi Weather temperature markets.
You are advisory-only.  You cannot place orders, modify positions, or call any
trading or execution endpoint.  You have no write access to any system.

YOUR OUTPUT
Respond with exactly one JSON object — no prose, no markdown fences, no code
blocks.  The object must match this structure exactly:

{
  "agent_id": "kalshi-wx-forecast-context-agent-v1",
  "run_id": "<echo the run_id from the user message exactly>",
  "lane": "KALSHI_WEATHER",
  "status": "COMPLETE",
  "facts": {
    "city": "<city from the user message>",
    "date": "<date from the user message, YYYY-MM-DD>",
    "scoring_mode": "gaussian_forecast"
  },
  "probabilities": {
    "model_prob_sum": 1.0,
    "calibration_status": "UNAVAILABLE"
  },
  "uncertainty": {
    "uncertainty_tier": "HIGH"
  },
  "agent_observed_blockers": [],
  "source_conflicts": [],
  "recommended_ceiling": "KALSHI_WATCH",
  "advisory_only": true
}

FORBIDDEN KEY NAMES
These key names must NEVER appear anywhere in your output, at any nesting depth:
  terminal_label, final_label, label, can_execute, execute, capital_allocation,
  execution_permission, trade_authorization, governance_state, authorized,
  approved_for_execution.

HARD RULES
- advisory_only must always be the boolean true (not the integer 1, not "true").
- lane must always be the exact string "KALSHI_WEATHER".
- recommended_ceiling must be one of exactly:
    KALSHI_WATCH
    KALSHI_PLAYABLE_LIMIT_ONLY
    KALSHI_REJECT_NO_EDGE
    KALSHI_REJECT_UNCALIBRATED
    KALSHI_REJECT_BAD_RULES
    KALSHI_DATA_UNOBTAINABLE
- Output pure JSON only.  No markdown, no explanation, no commentary.
"""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _call_failure(reason: str) -> ShadowValidationResult:
    """
    Closed shadow-failure result for SDK-level errors.

    Used when the agent invocation itself fails (network exception, timeout,
    unparseable response) — before validate_shadow_output can even be reached.

    shadow_failure_only=True is always set.  This result MUST NOT reach any
    production route, ceiling resolver, or weather_scout_log write.

    The violation is tagged WRONG_TYPE (closest available enum member for "no
    valid payload was produced"); the failure_reason carries the full context
    prefixed with AGENT_CALL_FAILURE so callers can distinguish it from a
    schema violation.
    """
    return ShadowValidationResult(
        passed=False,
        violation=ShadowSchemaViolation.WRONG_TYPE,
        failure_reason=f"AGENT_CALL_FAILURE: {reason}",
        failure_path="$",
        shadow_failure_only=True,
    )


def _build_client() -> Optional[Any]:
    """
    Return an Anthropic client if the SDK is available and an API key is set.
    Returns None otherwise — callers must handle the None case.

    Resolution order:
      1. AI_INTEGRATIONS_ANTHROPIC_API_KEY + AI_INTEGRATIONS_ANTHROPIC_BASE_URL
         (Replit AI integrations proxy — preferred in this environment)
      2. ANTHROPIC_API_KEY (direct API key)
    """
    if not _SDK_AVAILABLE:
        return None
    api_key = (
        os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    if not api_key:
        return None
    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    if base_url:
        return _anthropic_sdk.Anthropic(api_key=api_key, base_url=base_url)
    return _anthropic_sdk.Anthropic(api_key=api_key)


# ── Public API ────────────────────────────────────────────────────────────────

def invoke_forecast_context_agent(
    city: str,
    date: str,
    run_id: str,
    *,
    sdk_client: Optional[Any] = None,
) -> ShadowValidationResult:
    """
    Invoke the Kalshi Weather forecast-context interpretation subagent and
    return a schema-validated ShadowValidationResult.

    Parameters
    ----------
    city       : City name for the weather market under evaluation.
    date       : ISO-8601 date string (YYYY-MM-DD).
    run_id     : Caller-supplied run identifier; echoed back in the agent output.
    sdk_client : Optional pre-built Anthropic client.  When None the function
                 builds one from environment variables.  Supply a mock here in
                 tests to avoid live network access.

    Returns
    -------
    ShadowValidationResult
        SHADOW_PASS (passed=True) when the agent response is structurally valid
        per the Step 9 schema.

        A shadow-failure result (passed=False, shadow_failure_only=True) for
        every other outcome — schema violation, forbidden key, wrong type, SDK
        exception, unparseable JSON, missing API key, or anything else.

    FEATURE FLAG
        KALSHI_WX_SHADOW_AGENT_ENABLED must be True for any invocation to
        proceed past the gate.  With the flag off (the default), this function
        returns a SHADOW_AGENT_DISABLED failure immediately — no client is
        built, no API key is consulted, no network activity occurs.

    VALIDATOR INVARIANT
        This function never returns raw model output.  Every return statement
        calls either validate_shadow_output(payload) or _call_failure(reason).
        The structural test_T4 in the test file enforces this with AST analysis.
    """
    # ── Feature flag gate — unconditional first check ─────────────────────────
    # Fires before any client construction, SDK call, or network activity.
    # A present API key does NOT bypass this gate.
    if not KALSHI_WX_SHADOW_AGENT_ENABLED:
        return _call_failure(
            "SHADOW_AGENT_DISABLED: feature flag KALSHI_WX_SHADOW_AGENT_ENABLED "
            "is not set to 'true' — no agent invocation will occur"
        )

    # ── Resolve client ────────────────────────────────────────────────────────
    client = sdk_client if sdk_client is not None else _build_client()
    if client is None:
        return _call_failure(
            "No Anthropic client available — SDK not installed or API key absent"
        )

    # ── Invoke the subagent ───────────────────────────────────────────────────
    user_message = (
        f"Interpret forecast context for the following Kalshi Weather market:\n"
        f"city={city!r}  date={date!r}  run_id={run_id!r}\n\n"
        f"Return a single JSON object as specified in your instructions."
    )
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        return _call_failure(f"SDK call raised {type(exc).__name__}: {exc}")

    # ── Extract text content ──────────────────────────────────────────────────
    try:
        raw_text = response.content[0].text
    except (AttributeError, IndexError, TypeError) as exc:
        return _call_failure(f"Could not read response content: {exc}")

    # ── Parse JSON ────────────────────────────────────────────────────────────
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        return _call_failure(f"Agent response is not valid JSON: {exc}")

    # ── Validate through Step 9 schema ────────────────────────────────────────
    # This is the ONLY path to a passing result.  validate_shadow_output()
    # either returns SHADOW_PASS or a shadow-failure-only result.  Either way,
    # the caller receives a ShadowValidationResult — never a raw payload.
    return validate_shadow_output(payload)
