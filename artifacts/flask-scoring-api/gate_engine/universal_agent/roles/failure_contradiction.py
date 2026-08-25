"""
gate_engine/universal_agent/roles/failure_contradiction.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Role 5: Failure/Contradiction
Detects conflicts and failures across all evidence sources. Summarises
contradictions between sources and acquisition failures, then issues a
resolution recommendation for the downstream Final Refresh role.

Advisory output schema (advisory_findings):
  role_id                  required  str   must be ROLE_ID
  schema_version           required  str   must be SCHEMA_VERSION
  contradiction_detected   required  bool  True if any source contradiction found
  failure_detected         required  bool  True if any acquisition failure found
  resolution_recommendation required str  enum RESOLUTION_STATES
  contradictions           optional  list  list of contradiction descriptor dicts
  failures                 optional  list  list of failure descriptor dicts
  contradiction_severity   optional  str   enum SEVERITY_STATES

"UNKNOWN" is a valid resolution_recommendation when evidence is too ambiguous
to classify. Never fabricate a recommendation from insufficient evidence.
"""
from __future__ import annotations

from typing import Any, Union

from gate_engine.universal_agent.agent_registry import AgentRegistryEntry, BudgetConfig
from gate_engine.universal_agent.evidence_packet import Lane
from gate_engine.universal_agent.output_contract import OUTPUT_VALID, OutputContractViolation, valid_output_payload
from gate_engine.universal_agent.roles.role_base import (
    SCHEMA_VERSION,
    validate_role_advisory_output,
)

# ── Role identity ─────────────────────────────────────────────────────────────
ROLE_ID: str = "FAILURE_CONTRADICTION"

# ── Advisory findings schema ──────────────────────────────────────────────────
RESOLUTION_STATES: frozenset[str] = frozenset({
    "PROCEED", "HOLD", "ABORT", "UNKNOWN",
})
SEVERITY_STATES: frozenset[str] = frozenset({
    "NONE", "LOW", "MEDIUM", "HIGH", "UNKNOWN",
})

_ADVISORY_ALLOWED: frozenset[str] = frozenset({
    "contradiction_detected",
    "failure_detected",
    "resolution_recommendation",
    # optional
    "contradictions",
    "failures",
    "contradiction_severity",
})

_ADVISORY_REQUIRED: frozenset[str] = frozenset({
    "contradiction_detected",
    "failure_detected",
    "resolution_recommendation",
})

_TYPE_CHECKS: dict[str, type] = {
    "contradiction_detected": bool,
    "failure_detected":        bool,
    "contradictions":          list,
    "failures":                list,
}

_ENUM_CHECKS: dict[str, frozenset] = {
    "resolution_recommendation": RESOLUTION_STATES,
    "contradiction_severity":    SEVERITY_STATES,
}


# ── Primary validator ─────────────────────────────────────────────────────────

def validate_failure_contradiction_output(
    payload: Any,
) -> Union[type(OUTPUT_VALID), OutputContractViolation]:
    """
    Validate a Failure/Contradiction role output payload.

    Phase 1 delegates to B0 validate_output_contract (includes recursive
    forbidden governance key scan — catches governance keys nested inside
    the contradictions or failures lists at any depth).
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

def valid_failure_contradiction_payload(**advisory_overrides: Any) -> dict:
    """
    Build a minimal valid Failure/Contradiction output payload.
    Accepts advisory_findings overrides as kwargs.
    """
    findings: dict[str, Any] = {
        "role_id":                   ROLE_ID,
        "schema_version":            SCHEMA_VERSION,
        "contradiction_detected":    False,
        "failure_detected":          False,
        "resolution_recommendation": "PROCEED",
    }
    findings.update(advisory_overrides)
    return valid_output_payload(advisory_findings=findings)


# ── Registry entry ────────────────────────────────────────────────────────────
REGISTRY_ENTRY: AgentRegistryEntry = AgentRegistryEntry(
    agent_id="uac-failure-contradiction-v1",
    role=ROLE_ID,
    lane=Lane.UNKNOWN,
    allowed_capabilities=["emit_failure_contradiction"],
    input_schema_ref="gate_engine.universal_agent.roles.failure_contradiction",
    output_schema_ref="gate_engine.universal_agent.roles.failure_contradiction",
    model_module=None,
    budget=BudgetConfig(max_cost_usd=0.05),
)
