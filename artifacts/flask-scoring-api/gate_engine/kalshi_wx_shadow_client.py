"""
gate_engine/kalshi_wx_shadow_client.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — research client (canonical runtime path)

KalshiWxShadowResearchClient is the canonical runtime entry point for the Kalshi
Weather shadow research feature.  research() delegates to the shadow orchestrator,
which runs the five read-only research subagents and returns a ShadowValidationResult.

WHAT THIS MODULE IS
  Class-based runtime client with hardcoded authority constants, feature-flag gate,
  authority guard, and orchestrator delegation.

WHAT THIS MODULE IS NOT
  - No tools configured directly here (tools are in kalshi_wx_shadow_subagents.py)
  - No orchestration logic (lives in kalshi_wx_shadow_orchestrator.py)
  - No DB writes (ledger is in kalshi_wx_shadow_ledger.py; shadow-only)
  - No Flask route registration
  - No changes to any scoring, market, settlement, label, or execution logic

AUTHORITY CONSTANTS (hardcoded class-level, all False)
  CAN_EXECUTE           = False   cannot place orders or make trades
  PRODUCTION_AUTHORITY  = False   cannot write to production systems
  USER_OUTPUT_AUTHORITY = False   cannot produce user-facing output

FEATURE FLAG
  KALSHI_WX_SHADOW_AGENT_ENABLED (env var) defaults False.
  Must be explicitly set to "true" (case-insensitive) to enable live calls.
  The flag gate is the FIRST check in research() — before any SDK client is
  constructed, before any API key is consulted, before any network activity.

RELATIONSHIP TO TEST-ONLY DIRECT COMPLETION HELPER
  gate_engine/kalshi_wx_shadow_agent.py contains invoke_forecast_context_agent(),
  which uses client.messages.create() directly.  That function is the TEST-ONLY
  direct completion helper from Step 10.1.  This module is the canonical path.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from gate_engine.kalshi_wx_shadow_schema import (
    ShadowSchemaViolation,
    ShadowValidationResult,
    validate_shadow_output,  # noqa: F401 — used by orchestrator; imported for contract clarity
)

# ── SDK import ────────────────────────────────────────────────────────────────
try:
    import anthropic as _anthropic_sdk
    _SDK_AVAILABLE = True
except ImportError:
    _anthropic_sdk = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False

# ── Feature flag — default OFF ────────────────────────────────────────────────
# Stored as a module-level bool so tests can patch it:
#   patch("gate_engine.kalshi_wx_shadow_client._SHADOW_ENABLED", True/False)
_SHADOW_ENABLED: bool = (
    os.environ.get("KALSHI_WX_SHADOW_AGENT_ENABLED", "false").strip().lower() == "true"
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _shadow_client_failure(reason: str) -> ShadowValidationResult:
    """
    Closed shadow-failure result for client-level errors.
    shadow_failure_only=True is always set.
    MUST NOT reach any production route, ceiling resolver, or weather_scout_log.
    """
    return ShadowValidationResult(
        passed=False,
        violation=ShadowSchemaViolation.WRONG_TYPE,
        failure_reason=f"SHADOW_CLIENT: {reason}",
        failure_path="$",
        shadow_failure_only=True,
    )


def _build_sdk_client() -> Optional[Any]:
    """
    Build an Anthropic client from environment variables.
    Returns None if SDK not installed or no API key is available.

    Resolution order:
      1. AI_INTEGRATIONS_ANTHROPIC_API_KEY + AI_INTEGRATIONS_ANTHROPIC_BASE_URL
      2. ANTHROPIC_API_KEY
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


# ── Research client ───────────────────────────────────────────────────────────

class KalshiWxShadowResearchClient:
    """
    Canonical runtime client for the Kalshi Weather shadow research feature.

    Authority constants (hardcoded class-level, not configurable):
      CAN_EXECUTE           = False
      PRODUCTION_AUTHORITY  = False
      USER_OUTPUT_AUTHORITY = False

    research() gate order:
      Gate 1 — Feature flag (_SHADOW_ENABLED): must be True to proceed.
               Credential presence CANNOT bypass this gate.
      Gate 2 — Authority constants: all three must be False.
               Catches subclasses that accidentally override a constant.
      Gate 3 — SDK client: build from env vars if not injected.
      Gate 4 — Orchestrator delegation: runs the 5 research subagents.

    Returns a ShadowValidationResult in all cases.  On flag-off, returns a
    closed failure without any network activity.  On success, returns
    SHADOW_PASS or a schema-validated BLOCKED/SCHEMA_FAIL result.
    """

    CAN_EXECUTE:           bool = False
    PRODUCTION_AUTHORITY:  bool = False
    USER_OUTPUT_AUTHORITY: bool = False

    def __init__(self, *, sdk_client: Optional[Any] = None) -> None:
        """
        Parameters
        ----------
        sdk_client : Optional pre-built anthropic.Anthropic client.
                     When None, _build_sdk_client() is called inside research()
                     after the feature flag passes.  Supply a mock in tests.
        """
        self._sdk_client: Optional[Any] = sdk_client

    def research(
        self,
        city: str,
        date: str,
        run_id: str,
    ) -> ShadowValidationResult:
        """
        Run the full Kalshi Weather shadow research pipeline.

        Parameters
        ----------
        city   : City name for the weather market.
        date   : ISO-8601 date string (YYYY-MM-DD).
        run_id : Caller-supplied run identifier.

        Returns
        -------
        ShadowValidationResult
            SHADOW_PASS when all 5 subagents succeed and the assembled payload
            is schema-valid.
            A schema-validated BLOCKED result if any subagent fails.
            A closed shadow-failure result (passed=False, shadow_failure_only=True)
            for gate violations, missing client, or orchestrator errors.

        SAFETY CONTRACT
            This method never returns raw model output.  Every return path
            produces a ShadowValidationResult.
        """
        # ── Gate 1: feature flag ──────────────────────────────────────────────
        # Unconditional first check.  No SDK object is constructed here.
        # A present API key does NOT bypass this gate.
        if not _SHADOW_ENABLED:
            return _shadow_client_failure(
                "SHADOW_AGENT_DISABLED: feature flag KALSHI_WX_SHADOW_AGENT_ENABLED "
                "is not set to 'true' — no research invocation will occur"
            )

        # ── Gate 2: authority constants ───────────────────────────────────────
        # Belt-and-suspenders: all three must be False on the class.
        # Catches any subclass that accidentally overrides a constant to True.
        if self.CAN_EXECUTE or self.PRODUCTION_AUTHORITY or self.USER_OUTPUT_AUTHORITY:
            return _shadow_client_failure(
                "SHADOW_CLIENT_AUTHORITY_VIOLATION: CAN_EXECUTE, "
                "PRODUCTION_AUTHORITY, and USER_OUTPUT_AUTHORITY must all be False"
            )

        # ── Gate 3: SDK client ────────────────────────────────────────────────
        client = self._sdk_client if self._sdk_client is not None else _build_sdk_client()
        if client is None:
            return _shadow_client_failure(
                "NO_SDK_CLIENT: Anthropic SDK not installed or API key absent — "
                "set AI_INTEGRATIONS_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY"
            )

        # ── Gate 4: orchestrator delegation ───────────────────────────────────
        # Lazy imports keep this module loadable without the orchestrator being
        # present, and avoid circular import issues at module load time.
        try:
            from gate_engine.kalshi_wx_shadow_capability_boundary import (
                CapabilityBoundary,
            )
            from gate_engine.kalshi_wx_shadow_ledger import get_default_ledger
            from gate_engine.kalshi_wx_shadow_orchestrator import (
                run_shadow_orchestrator,
            )
        except ImportError as exc:
            return _shadow_client_failure(
                f"ORCHESTRATOR_IMPORT_ERROR: {exc}"
            )

        try:
            return run_shadow_orchestrator(
                city=city,
                date=date,
                run_id=run_id,
                sdk_client=client,
                capability_boundary=CapabilityBoundary(),
                ledger=get_default_ledger(),
            )
        except Exception as exc:
            return _shadow_client_failure(
                f"ORCHESTRATOR_ERROR: {type(exc).__name__}: {exc}"
            )

    @classmethod
    def assert_inert(cls) -> None:
        """
        Assert all three authority constants are False.
        Raises AssertionError if any is True.
        """
        assert not cls.CAN_EXECUTE, \
            f"CAN_EXECUTE must be False; got {cls.CAN_EXECUTE!r}"
        assert not cls.PRODUCTION_AUTHORITY, \
            f"PRODUCTION_AUTHORITY must be False; got {cls.PRODUCTION_AUTHORITY!r}"
        assert not cls.USER_OUTPUT_AUTHORITY, \
            f"USER_OUTPUT_AUTHORITY must be False; got {cls.USER_OUTPUT_AUTHORITY!r}"
