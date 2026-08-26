"""
gate_engine/universal_agent/roles/market_exact_line.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Role 3: Market/Exact-Line
Validates the precise market line from live sportsbook sources, records
odds and line movement, and flags suspended or missing markets.

Advisory output schema (advisory_findings):
  role_id             required  str          must be ROLE_ID
  schema_version      required  str          must be SCHEMA_VERSION
  line_confirmed      required  bool         True if a specific line value was found
  line_source         required  str          source identifier or "UNKNOWN"
  market_status       required  str          enum MARKET_STATUS_STATES
  confirmed_line      optional  float|None   None when line not confirmed
  over_odds           optional  int|float|None  None when unavailable
  under_odds          optional  int|float|None  None when unavailable
  line_movement_note  optional  str          plain-text note on line movement
  line_confidence     optional  str          enum CONFIDENCE_STATES

Missing/unknown values → None or "UNKNOWN" enum state, never fabricated.
"""
from __future__ import annotations

from typing import Any

from gate_engine.universal_agent.agent_registry import AgentRegistryEntry, BudgetConfig
from gate_engine.universal_agent.evidence_packet import Lane
from gate_engine.universal_agent.output_contract import OUTPUT_VALID, OutputContractViolation, valid_output_payload
from gate_engine.universal_agent.roles.role_base import (
    SCHEMA_VERSION,
    validate_role_advisory_output,
)

# ── Role identity ─────────────────────────────────────────────────────────────
ROLE_ID: str = "MARKET_EXACT_LINE"

# ── Advisory findings schema ──────────────────────────────────────────────────
MARKET_STATUS_STATES: frozenset[str] = frozenset({
    "OPEN", "SUSPENDED", "CLOSED", "UNKNOWN",
})
CONFIDENCE_STATES: frozenset[str] = frozenset({
    "HIGH", "MEDIUM", "LOW", "UNKNOWN",
})

_ADVISORY_ALLOWED: frozenset[str] = frozenset({
    "line_confirmed",
    "line_source",
    "market_status",
    # optional
    "confirmed_line",
    "over_odds",
    "under_odds",
    "line_movement_note",
    "line_confidence",
})

_ADVISORY_REQUIRED: frozenset[str] = frozenset({
    "line_confirmed",
    "line_source",
    "market_status",
})

# Note: confirmed_line/over_odds/under_odds may be None (market not found).
# Type checks skip None values — see role_base.validate_role_advisory_output.
_TYPE_CHECKS: dict[str, type] = {
    "line_confirmed":     bool,
    "line_source":        str,
    "line_movement_note": str,
}

_ENUM_CHECKS: dict[str, frozenset] = {
    "market_status":  MARKET_STATUS_STATES,
    "line_confidence": CONFIDENCE_STATES,
}


# ── Primary validator ─────────────────────────────────────────────────────────

def validate_market_exact_line_output(
    payload: Any,
) -> type(OUTPUT_VALID) | OutputContractViolation:
    """
    Validate a Market/Exact-Line role output payload.

    Phase 1 delegates to B0 validate_output_contract (includes recursive
    forbidden governance key scan over the entire payload).
    Phase 2 validates advisory_findings against this role's closed schema.
    """
    return validate_role_advisory_output(
        payload,
        role_id=ROLE_ID,
        advisory_allowed=_ADVISORY_ALLOWED,
        advisory_required=_ADVISORY_REQUIRED,
        type_checks=_TYPE_CHECKS,
        enum_checks=_ENUM_CHECKS,
    )


# ── Test helper ───────────────────────────────────────────────────────────────

def valid_market_exact_line_payload(**advisory_overrides: Any) -> dict:
    """
    Build a minimal valid Market/Exact-Line output payload.
    Accepts advisory_findings overrides as kwargs.
    """
    findings: dict[str, Any] = {
        "role_id":        ROLE_ID,
        "schema_version": SCHEMA_VERSION,
        "line_confirmed": True,
        "line_source":    "odds-api-primary",
        "market_status":  "OPEN",
        "confirmed_line": 24.5,
        "over_odds":      -115,
        "under_odds":     -105,
    }
    findings.update(advisory_overrides)
    return valid_output_payload(advisory_findings=findings)


# ── Registry entry ────────────────────────────────────────────────────────────
REGISTRY_ENTRY: AgentRegistryEntry = AgentRegistryEntry(
    agent_id="uac-market-exact-line-v1",
    role=ROLE_ID,
    lane=Lane.UNKNOWN,
    allowed_capabilities=["emit_market_exact_line"],
    input_schema_ref="gate_engine.universal_agent.roles.market_exact_line",
    output_schema_ref="gate_engine.universal_agent.roles.market_exact_line",
    model_module=None,
    budget=BudgetConfig(max_cost_usd=0.05),
)
