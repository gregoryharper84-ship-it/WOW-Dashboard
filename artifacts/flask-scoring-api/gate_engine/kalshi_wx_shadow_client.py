"""
gate_engine/kalshi_wx_shadow_client.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — research client scaffold

Inert Claude Agent SDK client scaffold for the Kalshi Weather shadow research
feature.  This module defines the authority constants, feature-flag gate, and
class structure that future shadow research steps will build on.

WHAT THIS MODULE IS
  KalshiWxShadowResearchClient is a class-based wrapper around the Anthropic
  SDK client with three hardcoded authority constants (CAN_EXECUTE=False,
  PRODUCTION_AUTHORITY=False, USER_OUTPUT_AUTHORITY=False).  The class is
  safety-gated and currently inert — no agent behavior is wired to research().
  It cannot activate from credential presence alone.

WHAT THIS MODULE IS NOT / WHAT IS EXPLICITLY EXCLUDED
  • No tools are configured or registered on the client
  • No subagents are spawned or referenced
  • No orchestrator is defined or called
  • No Flask routes are registered or modified (no app import anywhere here)
  • No database writes occur (no psycopg2, SQLAlchemy, or DB import in this file)
  • No market access, order placement, or execution behavior of any kind
  • No user-facing output is produced
  • No existing analytical, scoring, settlement, label, or execution logic is touched

RELATIONSHIP TO THE DIRECT COMPLETION HELPER
  gate_engine/kalshi_wx_shadow_agent.py contains invoke_forecast_context_agent(),
  a bare function using client.messages.create() directly.  That function is
  the TEST-ONLY direct completion helper used in the Step 10.1 proof-of-concept.
  This module is the production scaffold foundation.

AUTHORITY CONSTANTS
  CAN_EXECUTE          = False   Cannot place orders or make trades
  PRODUCTION_AUTHORITY = False   Cannot write to production systems
  USER_OUTPUT_AUTHORITY = False  Cannot produce user-facing output

  These are hardcoded class constants — not configurable by callers.
  The authority guard in research() re-checks them at call time so a future
  subclass cannot accidentally inherit a True value unnoticed.

FEATURE FLAG
  Reads KALSHI_WX_SHADOW_AGENT_ENABLED from the environment (same variable as
  kalshi_wx_shadow_agent.py).  Absent or any value other than "true"
  (case-insensitive) evaluates to False.  With the flag off (the default),
  research() returns a closed failure immediately — before any SDK object is
  constructed or any network activity occurs.

VALIDATOR CONTRACT (for when behavior is wired in a future step)
  The only permitted path to a passing ShadowValidationResult inside research()
  is a direct call to validate_shadow_output(payload).  Raw model output must
  never be returned directly.  This contract is identical to the one enforced
  by the AST test (T4) in test_kalshi_wx_shadow_agent.py.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from gate_engine.kalshi_wx_shadow_schema import (
    ShadowSchemaViolation,
    ShadowValidationResult,
    validate_shadow_output,  # noqa: F401 — imported for future wiring; not yet called
)

# ── SDK import ────────────────────────────────────────────────────────────────
# Imported at module level for type resolution only.  No live client is
# constructed here — construction is deferred to research(), behind the flag.
try:
    import anthropic as _anthropic_sdk
    _SDK_AVAILABLE = True
except ImportError:
    _anthropic_sdk = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False

# ── Feature flag — default OFF ────────────────────────────────────────────────
# Same environment variable as kalshi_wx_shadow_agent.py; both modules gate on
# the same flag.  Stored as a module-level bool so tests can patch it directly:
#   patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", True/False)
#
# With the flag off (the default), research() is guaranteed to:
#   • make zero network calls
#   • construct no SDK client object
#   • consult no API key
#   • read no market data
#   • write nothing anywhere
_SHADOW_ENABLED: bool = (
    os.environ.get("KALSHI_WX_SHADOW_AGENT_ENABLED", "false").strip().lower() == "true"
)


# ── Failure helper ────────────────────────────────────────────────────────────

def _shadow_client_failure(reason: str) -> ShadowValidationResult:
    """
    Construct a closed shadow-failure result for scaffold-level errors.

    shadow_failure_only=True is always set.  This result MUST NOT reach any
    production route, ceiling resolver, or weather_scout_log write.

    Uses ShadowSchemaViolation.WRONG_TYPE as the violation tag (closest
    available enum member; the reason string carries the full context).
    """
    return ShadowValidationResult(
        passed=False,
        violation=ShadowSchemaViolation.WRONG_TYPE,
        failure_reason=f"SHADOW_CLIENT: {reason}",
        failure_path="$",
        shadow_failure_only=True,
    )


# ── Research client scaffold ──────────────────────────────────────────────────

class KalshiWxShadowResearchClient:
    """
    Inert Claude Agent SDK client scaffold for Kalshi Weather shadow research.

    Wraps an anthropic.Anthropic SDK client instance with three hardcoded
    authority constants and a feature-flag gate.  The class is currently a
    scaffold: research() is fully safety-gated but not yet wired to any agent
    behavior.  Future steps will add agent invocation inside research() and
    route the response through validate_shadow_output() before returning it.

    Authority constants
    -------------------
    CAN_EXECUTE          : bool = False — cannot place orders or make trades
    PRODUCTION_AUTHORITY : bool = False — cannot write to production systems
    USER_OUTPUT_AUTHORITY: bool = False — cannot produce user-facing output

    These are class-level constants, not instance parameters.  They cannot be
    overridden by passing arguments to __init__.
    """

    # ── Hardcoded authority constants — all False, not configurable ───────────
    CAN_EXECUTE:           bool = False
    PRODUCTION_AUTHORITY:  bool = False
    USER_OUTPUT_AUTHORITY: bool = False

    def __init__(self, *, sdk_client: Optional[Any] = None) -> None:
        """
        Parameters
        ----------
        sdk_client : Optional pre-built anthropic.Anthropic client instance.
                     When None, client construction from env vars is deferred to
                     research() and only happens after the feature flag passes.
                     Supply a mock here in tests.

        No live client is constructed at __init__ time.
        """
        # Store reference only — no SDK object is created here.
        self._sdk_client: Optional[Any] = sdk_client

    def research(
        self,
        city: str,
        date: str,
        run_id: str,
    ) -> ShadowValidationResult:
        """
        Entry point for shadow research.

        Currently inert — the three safety gates exist and are enforced, but no
        agent behavior is wired in this step.  Returns a closed shadow-failure
        result on every code path.

        Parameters
        ----------
        city   : City name for the weather market under evaluation.
        date   : ISO-8601 date string (YYYY-MM-DD).
        run_id : Caller-supplied run identifier.

        Returns
        -------
        ShadowValidationResult
            Always a failure (passed=False, shadow_failure_only=True) in the
            current scaffold step.  When agent behavior is wired in a future
            step, the only permitted passing path is:
                return validate_shadow_output(payload)

        SAFETY CONTRACT
            Every return statement in this method calls _shadow_client_failure().
            No dict, no None, and no raw model text ever escapes this method.
        """
        # ── Gate 1: feature flag ──────────────────────────────────────────────
        # Unconditional first check — before any SDK object is constructed or
        # any network activity occurs.  Credential presence does NOT bypass this.
        if not _SHADOW_ENABLED:
            return _shadow_client_failure(
                "SHADOW_AGENT_DISABLED: feature flag KALSHI_WX_SHADOW_AGENT_ENABLED "
                "is not set to 'true' — no research invocation will occur"
            )

        # ── Gate 2: authority constants ───────────────────────────────────────
        # Belt-and-suspenders re-check at call time.  The constants are
        # hardcoded False on this class; this guard catches any future subclass
        # that accidentally overrides one of them to True.
        if self.CAN_EXECUTE or self.PRODUCTION_AUTHORITY or self.USER_OUTPUT_AUTHORITY:
            return _shadow_client_failure(
                "SHADOW_CLIENT_AUTHORITY_VIOLATION: CAN_EXECUTE, "
                "PRODUCTION_AUTHORITY, and USER_OUTPUT_AUTHORITY must all be "
                "False on this scaffold — none may be set to True"
            )

        # ── Gate 3: scaffold not yet wired ────────────────────────────────────
        # Agent invocation is not connected in this step.  When a future step
        # wires behavior here, it must:
        #   1. Build or use self._sdk_client
        #   2. Invoke the agent
        #   3. return validate_shadow_output(parsed_payload)
        # That final return is the only permitted path to passed=True.
        return _shadow_client_failure(
            "SHADOW_CLIENT_NOT_WIRED: scaffold is in place but no agent behavior "
            "is connected in this step — this is expected and correct"
        )

    @classmethod
    def assert_inert(cls) -> None:
        """
        Assert all three authority constants are False.

        Raises AssertionError if any constant is True.  Can be called in tests
        or startup sanity checks to confirm the class has not been mutated.
        """
        assert not cls.CAN_EXECUTE, \
            f"CAN_EXECUTE must be False; got {cls.CAN_EXECUTE!r}"
        assert not cls.PRODUCTION_AUTHORITY, \
            f"PRODUCTION_AUTHORITY must be False; got {cls.PRODUCTION_AUTHORITY!r}"
        assert not cls.USER_OUTPUT_AUTHORITY, \
            f"USER_OUTPUT_AUTHORITY must be False; got {cls.USER_OUTPUT_AUTHORITY!r}"
