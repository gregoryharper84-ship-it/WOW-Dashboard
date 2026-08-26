"""
gate_engine/universal_agent/canary/canary_config.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C

Canary configuration constants.

STRUCTURAL ISOLATION GUARANTEES
--------------------------------
- UAC_MLB_ML_CLAUDE_SHADOW_ENABLED is a NEW, INDEPENDENT environment flag.
  It has ZERO cross-references to:
    - CAN_EXECUTE (no import, no reference)
    - Production routing flags (no import, no reference)
    - Any weather-lane flag (no weather or WX shadow imports)
  grep-verify: no "CAN_EXECUTE", "SHADOW_ENABLED" in this file.

- All budget/model/timeout constants are MODULE-LEVEL CONSTANTS, not configurable
  by callers. No setter, no override path, no environment variable for them
  (except UAC_MLB_ML_CLAUDE_SHADOW_ENABLED which is the dispatch gate).

- PINNED_MODEL is an exact string literal. It is not a variable that can be
  reassigned from outside this module. Both requested_model (sent in API call)
  and response_model (from API response) must match it exactly.

- AUTO_BUDGET_INCREASE = False. No override path. The value cannot become True
  through any code path in this package.
"""
from __future__ import annotations

import os

can_execute    = False
advisory_only  = True


# ── Live dispatch flag ────────────────────────────────────────────────────────
# Read ONCE at module load. Missing key → False. Malformed value → False.
# Only the exact string "true" (case-insensitive) → True.
# Structurally independent from all other flags in this codebase.

def _read_bool_flag(env_key: str) -> bool:
    """Read an environment flag; missing or malformed → False."""
    raw = os.environ.get(env_key, "")
    return isinstance(raw, str) and raw.strip().lower() == "true"


UAC_MLB_ML_CLAUDE_SHADOW_ENABLED: bool = _read_bool_flag(
    "UAC_MLB_ML_CLAUDE_SHADOW_ENABLED"
)


# ── Pinned model (exact literal — not reassignable from outside) ──────────────
# Both requested_model (what we send) and response_model (what comes back)
# must equal this string exactly. Any mismatch → CANARY_FAIL_MODEL_IDENTITY.

PINNED_MODEL: str = "claude-haiku-4-5-20251001"


# ── Budget constants (NOT configurable by callers) ────────────────────────────

MAX_CALLS: int          = 6      # Structural maximum. No 7th call possible.
MAX_TOTAL_SPEND_USD: float = 0.10  # Hard ceiling. Enforced before every call.
AUTO_BUDGET_INCREASE: bool = False  # No override path. Cannot become True.


# ── Per-call settings (hardcoded, NOT configurable) ───────────────────────────

MAX_TOKENS: int               = 1024  # Uniform across all 6 roles. No per-role override.
PER_CALL_TIMEOUT_SECONDS: float = 30.0  # API call wall-clock timeout.
AUTOMATIC_RETRIES: int        = 0     # Zero retries. Hardcoded. Not configurable.


# ── Pricing model (for worst-case pre-call budget check) ─────────────────────
# Haiku pricing as of 2026-08-10. Used only for conservative pre-call estimation.

INPUT_COST_PER_MTOK: float    = 1.0   # $1.00 per million input tokens
OUTPUT_COST_PER_MTOK: float   = 5.0   # $5.00 per million output tokens
WORST_CASE_INPUT_TOKENS: int  = 4096  # Conservative input estimate for budget check

# Precomputed worst-case cost per call (used in budget guard assertion):
#   WORST_CASE_INPUT_TOKENS/1e6 * INPUT_COST + MAX_TOKENS/1e6 * OUTPUT_COST
#   = 4096/1e6 * 1.0 + 1024/1e6 * 5.0 = 0.004096 + 0.005120 = $0.009216
# 6 × $0.009216 = $0.055296 < $0.10 ✓ (fits within budget under clean conditions)
WORST_CASE_COST_PER_CALL: float = (
    WORST_CASE_INPUT_TOKENS / 1_000_000 * INPUT_COST_PER_MTOK
    + MAX_TOKENS / 1_000_000 * OUTPUT_COST_PER_MTOK
)
