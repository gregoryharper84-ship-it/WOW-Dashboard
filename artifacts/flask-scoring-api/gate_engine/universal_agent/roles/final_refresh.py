"""
gate_engine/universal_agent/roles/final_refresh.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Role 6: Final Refresh
Synthesizes the advisory outputs of all prior roles and confirms that the
evidence snapshot remains valid before the handoff contract is issued.
This is the last role in the advisory pipeline; its output is a consolidated
readiness signal, not a decision.

Advisory output schema (advisory_findings):
  role_id                 required  str   must be ROLE_ID
  schema_version          required  str   must be SCHEMA_VERSION
  all_roles_completed     required  bool  True only if all expected roles ran
  roles_completed         required  list  list of role_id strings that ran
  roles_missing           required  list  list of role_id strings that did not run
  refresh_status          required  str   enum REFRESH_STATUS_STATES
  evidence_snapshot_valid required  bool  True if snapshot is still valid
  synthesis_note          optional  str   plain-text synthesis note
  role_outputs_summary    optional  dict  {role_id → brief summary string}

refresh_status="PARTIAL" is valid and expected when some roles are missing.
Never fabricate a completed role list — roles_missing must reflect reality.
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
ROLE_ID: str = "FINAL_REFRESH"

# ── Advisory findings schema ──────────────────────────────────────────────────
REFRESH_STATUS_STATES: frozenset[str] = frozenset({
    "COMPLETE", "PARTIAL", "FAILED", "UNKNOWN",
})

_ADVISORY_ALLOWED: frozenset[str] = frozenset({
    "all_roles_completed",
    "roles_completed",
    "roles_missing",
    "refresh_status",
    "evidence_snapshot_valid",
    # optional
    "synthesis_note",
    "role_outputs_summary",
})

_ADVISORY_REQUIRED: frozenset[str] = frozenset({
    "all_roles_completed",
    "roles_completed",
    "roles_missing",
    "refresh_status",
    "evidence_snapshot_valid",
})

_TYPE_CHECKS: dict[str, type] = {
    "all_roles_completed":    bool,
    "roles_completed":        list,
    "roles_missing":          list,
    "evidence_snapshot_valid": bool,
    "synthesis_note":         str,
    "role_outputs_summary":   dict,
}

_ENUM_CHECKS: dict[str, frozenset] = {
    "refresh_status": REFRESH_STATUS_STATES,
}


# ── Primary validator ─────────────────────────────────────────────────────────

def validate_final_refresh_output(
    payload: Any,
) -> Union[type(OUTPUT_VALID), OutputContractViolation]:
    """
    Validate a Final Refresh role output payload.

    Phase 1 delegates to B0 validate_output_contract (includes recursive
    forbidden governance key scan — catches governance keys nested inside
    role_outputs_summary dict at any depth).
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

def valid_final_refresh_payload(**advisory_overrides: Any) -> dict:
    """
    Build a minimal valid Final Refresh output payload.
    Accepts advisory_findings overrides as kwargs.
    """
    findings: dict[str, Any] = {
        "role_id":                ROLE_ID,
        "schema_version":         SCHEMA_VERSION,
        "all_roles_completed":    True,
        "roles_completed":        [
            "DATA_SLATE_INTEGRITY",
            "NEWS_STATUS",
            "MARKET_EXACT_LINE",
            "SPORT_SPECIALIST",
            "FAILURE_CONTRADICTION",
        ],
        "roles_missing":          [],
        "refresh_status":         "COMPLETE",
        "evidence_snapshot_valid": True,
    }
    findings.update(advisory_overrides)
    return valid_output_payload(advisory_findings=findings)


# ── Registry entry ────────────────────────────────────────────────────────────
REGISTRY_ENTRY: AgentRegistryEntry = AgentRegistryEntry(
    agent_id="uac-final-refresh-v1",
    role=ROLE_ID,
    lane=Lane.UNKNOWN,
    allowed_capabilities=["emit_final_refresh"],
    input_schema_ref="gate_engine.universal_agent.roles.final_refresh",
    output_schema_ref="gate_engine.universal_agent.roles.final_refresh",
    model_module=None,
    budget=BudgetConfig(max_cost_usd=0.05),
)
