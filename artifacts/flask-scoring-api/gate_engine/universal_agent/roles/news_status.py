"""
gate_engine/universal_agent/roles/news_status.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Role 2: News/Status
Captures player/team status, injury information, and recent relevant news
from evidence sources. Preserves unknown/missing status explicitly.

Advisory output schema (advisory_findings):
  role_id           required  str   must be ROLE_ID
  schema_version    required  str   must be SCHEMA_VERSION
  player_status     required  str   enum PLAYER_STATUS_STATES
  status_source     required  str   source identifier (or "UNKNOWN")
  status_as_of      required  str   ISO-8601 datetime or "UNKNOWN"
  injury_flag       required  bool  True if any injury indicator present
  news_items        optional  list  list of news summary strings
  status_confidence optional  str   enum CONFIDENCE_STATES
  dnp_risk          optional  bool  True if DNP (Did Not Play) is a risk

Missing/unknown values → "UNKNOWN" / "MISSING" enum states, never fabricated.
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
ROLE_ID: str = "NEWS_STATUS"

# ── Advisory findings schema ──────────────────────────────────────────────────
PLAYER_STATUS_STATES: frozenset[str] = frozenset({
    "ACTIVE", "QUESTIONABLE", "DOUBTFUL", "OUT",
    "UNKNOWN", "MISSING",
})
CONFIDENCE_STATES: frozenset[str] = frozenset({
    "HIGH", "MEDIUM", "LOW", "UNKNOWN",
})

_ADVISORY_ALLOWED: frozenset[str] = frozenset({
    "player_status",
    "status_source",
    "status_as_of",
    "injury_flag",
    # optional
    "news_items",
    "status_confidence",
    "dnp_risk",
})

_ADVISORY_REQUIRED: frozenset[str] = frozenset({
    "player_status",
    "status_source",
    "status_as_of",
    "injury_flag",
})

_TYPE_CHECKS: dict[str, type] = {
    "status_source": str,
    "status_as_of":  str,
    "injury_flag":   bool,
    "news_items":    list,
    "dnp_risk":      bool,
}

_ENUM_CHECKS: dict[str, frozenset] = {
    "player_status":     PLAYER_STATUS_STATES,
    "status_confidence": CONFIDENCE_STATES,
}


# ── Primary validator ─────────────────────────────────────────────────────────

def validate_news_status_output(
    payload: Any,
) -> type(OUTPUT_VALID) | OutputContractViolation:
    """
    Validate a News/Status role output payload.

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

def valid_news_status_payload(**advisory_overrides: Any) -> dict:
    """
    Build a minimal valid News/Status output payload.
    Accepts advisory_findings overrides as kwargs.
    """
    findings: dict[str, Any] = {
        "role_id":        ROLE_ID,
        "schema_version": SCHEMA_VERSION,
        "player_status":  "ACTIVE",
        "status_source":  "espn-api",
        "status_as_of":   "2026-08-09T12:00:00+00:00",
        "injury_flag":    False,
    }
    findings.update(advisory_overrides)
    return valid_output_payload(advisory_findings=findings)


# ── Registry entry ────────────────────────────────────────────────────────────
REGISTRY_ENTRY: AgentRegistryEntry = AgentRegistryEntry(
    agent_id="uac-news-status-v1",
    role=ROLE_ID,
    lane=Lane.UNKNOWN,
    allowed_capabilities=["emit_news_status"],
    input_schema_ref="gate_engine.universal_agent.roles.news_status",
    output_schema_ref="gate_engine.universal_agent.roles.news_status",
    model_module=None,
    budget=BudgetConfig(max_cost_usd=0.05),
)
