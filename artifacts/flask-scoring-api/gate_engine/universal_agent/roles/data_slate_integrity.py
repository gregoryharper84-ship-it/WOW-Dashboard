"""
gate_engine/universal_agent/roles/data_slate_integrity.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Role 1: Data/Slate Integrity
Verifies that the evidence packet's data is fresh, complete, and internally
consistent before any downstream role runs.

Advisory output schema (advisory_findings):
  role_id                 required  str  must be ROLE_ID
  schema_version          required  str  must be SCHEMA_VERSION
  data_freshness_status   required  str  enum FRESHNESS_STATES
  slate_consistency_check required  str  enum CONSISTENCY_STATES
  source_coverage         required  dict {source_key → "available"|"missing"|...}
  data_gaps_identified    required  list list of gap descriptor strings (may be [])
  stale_sources           optional  list list of stale source keys
  timestamp_audit         optional  dict {source_key → ISO-8601 str or "UNKNOWN"}
  integrity_confidence    optional  str  enum CONFIDENCE_STATES

Missing/unknown values are preserved as explicit enum strings ("UNKNOWN",
"MISSING") rather than fabricated data.
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
ROLE_ID: str = "DATA_SLATE_INTEGRITY"

# ── Advisory findings schema ──────────────────────────────────────────────────
FRESHNESS_STATES: frozenset[str] = frozenset({
    "FRESH", "STALE", "UNKNOWN", "MISSING",
})
CONSISTENCY_STATES: frozenset[str] = frozenset({
    "CONSISTENT", "INCONSISTENT", "UNKNOWN",
})
CONFIDENCE_STATES: frozenset[str] = frozenset({
    "HIGH", "MEDIUM", "LOW", "UNKNOWN",
})

_ADVISORY_ALLOWED: frozenset[str] = frozenset({
    # common (role_id + schema_version added by role_base)
    "data_freshness_status",
    "slate_consistency_check",
    "source_coverage",
    "data_gaps_identified",
    # optional
    "stale_sources",
    "timestamp_audit",
    "integrity_confidence",
})

_ADVISORY_REQUIRED: frozenset[str] = frozenset({
    "data_freshness_status",
    "slate_consistency_check",
    "source_coverage",
    "data_gaps_identified",
})

_TYPE_CHECKS: dict[str, type] = {
    "source_coverage":      dict,
    "data_gaps_identified": list,
    "stale_sources":        list,
    "timestamp_audit":      dict,
}

_ENUM_CHECKS: dict[str, frozenset] = {
    "data_freshness_status":   FRESHNESS_STATES,
    "slate_consistency_check": CONSISTENCY_STATES,
    "integrity_confidence":    CONFIDENCE_STATES,
}


# ── Primary validator ─────────────────────────────────────────────────────────

def validate_data_slate_integrity_output(
    payload: Any,
) -> type(OUTPUT_VALID) | OutputContractViolation:
    """
    Validate a Data/Slate Integrity role output payload.

    Delegates Phase 1 to B0 validate_output_contract (includes recursive
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

def valid_data_slate_integrity_payload(**advisory_overrides: Any) -> dict:
    """
    Build a minimal valid Data/Slate Integrity output payload.
    Used by test harnesses. Accepts advisory_findings overrides as kwargs.
    Shared with production validator path (Weather Step 14D pattern).
    """
    findings: dict[str, Any] = {
        "role_id":                 ROLE_ID,
        "schema_version":          SCHEMA_VERSION,
        "data_freshness_status":   "FRESH",
        "slate_consistency_check": "CONSISTENT",
        "source_coverage":         {"primary": "available"},
        "data_gaps_identified":    [],
    }
    findings.update(advisory_overrides)
    return valid_output_payload(advisory_findings=findings)


# ── Registry entry ────────────────────────────────────────────────────────────
# Advisory-only; model_module=None (no model wired at B1 — B2+ concern).
REGISTRY_ENTRY: AgentRegistryEntry = AgentRegistryEntry(
    agent_id="uac-data-slate-integrity-v1",
    role=ROLE_ID,
    lane=Lane.UNKNOWN,
    allowed_capabilities=["emit_data_slate_integrity"],
    input_schema_ref="gate_engine.universal_agent.roles.data_slate_integrity",
    output_schema_ref="gate_engine.universal_agent.roles.data_slate_integrity",
    model_module=None,
    budget=BudgetConfig(max_cost_usd=0.05),
)
