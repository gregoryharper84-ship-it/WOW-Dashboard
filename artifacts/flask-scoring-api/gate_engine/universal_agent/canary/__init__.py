"""
gate_engine/universal_agent/canary/__init__.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C

B3C Canary package — bounded real-Claude infrastructure for offline audit.

Invariants (all modules in this package):
  can_execute    = False   — no wagers, orders, capital, trading, deployment
  advisory_only  = True    — always; never sourced from model output
  UAC_MLB_ML_CLAUDE_SHADOW_ENABLED = False (default) — live dispatch off
  AUTOMATIC_RETRIES = 0    — hardcoded, not configurable
  PINNED_MODEL   = "claude-haiku-4-5-20251001" — exact literal, not a variable

No app.py import, no Flask route wiring. No Weather/Kalshi imports.
No cross-reference to CAN_EXECUTE, production routing flags, or weather-lane flags.
"""
from __future__ import annotations

can_execute    = False
advisory_only  = True
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False
CAPITAL_AUTHORITY     = False
NO_AUTO_PROMOTION     = True
PATCH_ID              = "WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C"
CANARY_PACKAGE = "gate_engine.universal_agent.canary"

# ── Public re-exports ─────────────────────────────────────────────────────────

from gate_engine.universal_agent.canary.canary_config import (  # noqa: E402
    UAC_MLB_ML_CLAUDE_SHADOW_ENABLED,
    PINNED_MODEL,
    MAX_CALLS,
    MAX_TOTAL_SPEND_USD,
    AUTO_BUDGET_INCREASE,
    MAX_TOKENS,
    PER_CALL_TIMEOUT_SECONDS,
    AUTOMATIC_RETRIES,
)

from gate_engine.universal_agent.canary.claude_role_runner import (  # noqa: E402
    BudgetState,
    CanaryCallRecord,
    CanaryCallStatus,
    ClaudeRoleRunner,
    CanaryBudgetGuardError,
    CanaryModelIdentityError,
    CanaryOutputRejectedError,
    CanaryCallFailedError,
)

from gate_engine.universal_agent.canary.canary_pipeline import (  # noqa: E402
    CanaryPipelineStatus,
    CanaryPipelineResult,
    run_canary_pipeline,
    CanaryPipeline,
)

from gate_engine.universal_agent.canary.canary_store import (  # noqa: E402
    ensure_canary_tables,
    persist_canary_result,
)

__all__ = [
    "can_execute",
    "advisory_only",
    "UAC_MLB_ML_CLAUDE_SHADOW_ENABLED",
    "PINNED_MODEL",
    "MAX_CALLS",
    "MAX_TOTAL_SPEND_USD",
    "AUTO_BUDGET_INCREASE",
    "MAX_TOKENS",
    "PER_CALL_TIMEOUT_SECONDS",
    "AUTOMATIC_RETRIES",
    "BudgetState",
    "CanaryCallRecord",
    "CanaryCallStatus",
    "ClaudeRoleRunner",
    "CanaryBudgetGuardError",
    "CanaryModelIdentityError",
    "CanaryOutputRejectedError",
    "CanaryCallFailedError",
    "CanaryPipelineStatus",
    "CanaryPipelineResult",
    "run_canary_pipeline",
    "CanaryPipeline",
    "ensure_canary_tables",
    "persist_canary_result",
]
