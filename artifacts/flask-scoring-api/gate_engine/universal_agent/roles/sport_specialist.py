"""
gate_engine/universal_agent/roles/sport_specialist.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Role 4: Sport Specialist
Provides sport-specific statistical assessment using the deterministic model
inputs captured in the evidence packet. Lane-agnostic at this level; specific
sport logic lives in B2+ lane adapters.

Advisory output schema (advisory_findings):
  role_id                required  str   must be ROLE_ID
  schema_version         required  str   must be SCHEMA_VERSION
  sport                  required  str   e.g. "NBA", "MLB", "WNBA", "TENNIS"
  statistical_assessment required  dict  sport-specific metrics + assessments
  key_metrics            required  list  list of metric name strings used
  missing_metrics        optional  list  list of metric name strings not obtainable
  assessment_confidence  optional  str   enum CONFIDENCE_STATES
  model_inputs_used      optional  dict  subset of deterministic_model_inputs used

Sport strings are free-form. The validator accepts any non-empty string to
avoid enum lock-in as new sports are onboarded (Lane strings follow same pattern).
Missing metrics preserved as explicit "UNKNOWN" / "MISSING" values in the
statistical_assessment dict rather than fabricated estimates.
"""
from __future__ import annotations

from typing import Any, Union

from gate_engine.universal_agent.agent_registry import AgentRegistryEntry, BudgetConfig
from gate_engine.universal_agent.evidence_packet import Lane
from gate_engine.universal_agent.output_contract import (
    OUTPUT_VALID, OutputContractViolation, OutputViolationCode, valid_output_payload,
)
from gate_engine.universal_agent.roles.role_base import (
    SCHEMA_VERSION,
    validate_role_advisory_output,
)

# ── Role identity ─────────────────────────────────────────────────────────────
ROLE_ID: str = "SPORT_SPECIALIST"

# ── Advisory findings schema ──────────────────────────────────────────────────
CONFIDENCE_STATES: frozenset[str] = frozenset({
    "HIGH", "MEDIUM", "LOW", "UNKNOWN",
})

_ADVISORY_ALLOWED: frozenset[str] = frozenset({
    "sport",
    "statistical_assessment",
    "key_metrics",
    # optional
    "missing_metrics",
    "assessment_confidence",
    "model_inputs_used",
})

_ADVISORY_REQUIRED: frozenset[str] = frozenset({
    "sport",
    "statistical_assessment",
    "key_metrics",
})

_TYPE_CHECKS: dict[str, type] = {
    "statistical_assessment": dict,
    "key_metrics":            list,
    "missing_metrics":        list,
    "model_inputs_used":      dict,
}

_ENUM_CHECKS: dict[str, frozenset] = {
    "assessment_confidence": CONFIDENCE_STATES,
}


# ── Primary validator ─────────────────────────────────────────────────────────

def validate_sport_specialist_output(
    payload: Any,
) -> Union[type(OUTPUT_VALID), OutputContractViolation]:
    """
    Validate a Sport Specialist role output payload.

    Phase 1 delegates to B0 validate_output_contract (includes recursive
    forbidden governance key scan over the entire payload, including
    statistical_assessment dict at any nesting depth).
    Phase 2 validates advisory_findings against this role's closed schema.
    """
    # Use shared two-phase validator from role_base.
    result = validate_role_advisory_output(
        payload,
        role_id=ROLE_ID,
        advisory_allowed=_ADVISORY_ALLOWED,
        advisory_required=_ADVISORY_REQUIRED,
        type_checks=_TYPE_CHECKS,
        enum_checks=_ENUM_CHECKS,
    )
    if result is not OUTPUT_VALID:
        return result

    # Additional check: sport must be a non-empty string.
    findings = payload.get("advisory_findings", {})
    sport = findings.get("sport", "")
    if not isinstance(sport, str) or not sport.strip():
        return OutputContractViolation(
            code=OutputViolationCode.WRONG_TYPE,
            message="advisory_findings.sport must be a non-empty string",
            path="advisory_findings.sport",
        )

    return OUTPUT_VALID


# ── Test helper ───────────────────────────────────────────────────────────────

def valid_sport_specialist_payload(**advisory_overrides: Any) -> dict:
    """
    Build a minimal valid Sport Specialist output payload.
    Accepts advisory_findings overrides as kwargs.
    """
    findings: dict[str, Any] = {
        "role_id":                ROLE_ID,
        "schema_version":         SCHEMA_VERSION,
        "sport":                  "NBA",
        "statistical_assessment": {
            "recent_avg":      24.5,
            "season_avg":      23.1,
            "vs_opponent_avg": "UNKNOWN",
        },
        "key_metrics": ["recent_avg", "season_avg", "vs_opponent_avg"],
    }
    findings.update(advisory_overrides)
    return valid_output_payload(advisory_findings=findings)


# ── Registry entry ────────────────────────────────────────────────────────────
REGISTRY_ENTRY: AgentRegistryEntry = AgentRegistryEntry(
    agent_id="uac-sport-specialist-v1",
    role=ROLE_ID,
    lane=Lane.UNKNOWN,
    allowed_capabilities=["emit_sport_specialist"],
    input_schema_ref="gate_engine.universal_agent.roles.sport_specialist",
    output_schema_ref="gate_engine.universal_agent.roles.sport_specialist",
    model_module=None,
    budget=BudgetConfig(max_cost_usd=0.10),
)
