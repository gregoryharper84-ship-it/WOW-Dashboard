"""
gate_engine/universal_agent/roles/role_base.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

Shared two-phase role output validator and common constants for all six
universal advisory role contracts.

Design decisions:
- Phase 1 delegates to B0's validate_output_contract() — root-level forbidden
  key scan + allowlist + required fields + types. NOT reimplemented here.
- Phase 2 validates advisory_findings against role-specific closed schemas.
  Allowlist enforcement on advisory_findings uses the same EXTRA_FIELD violation
  code as B0 for consistency.
- _scan_forbidden_keys is imported from output_contract (single source of truth).
  Tests can assert-is this reference to prove no reimplementation.
- Missing/unknown evidence is preserved as explicit "UNKNOWN" or "MISSING"
  enum values, never silently substituted with fabricated data.
- Fail-closed: unexpected exceptions produce INTERNAL_ERROR, never silent pass.
- No LLM calls, no routing, no app.py imports.

Public API:
  SCHEMA_VERSION            — canonical schema version for all B1 role schemas
  RoleViolationCode         — role-specific violation code constants
  EvidenceAvailability      — canonical enum for evidence availability states
  validate_role_advisory_output() — shared two-phase validator (call from each role)
"""
from __future__ import annotations

from typing import Any, Optional, Union

# ── B0 imports (reuse, never duplicate) ──────────────────────────────────────
# _scan_forbidden_keys is the SAME function object imported by capability_boundary.py.
# Tests verify this with assertIs.
from gate_engine.universal_agent.output_contract import (
    OUTPUT_VALID,
    OutputContractViolation,
    OutputViolationCode,
    _scan_forbidden_keys,          # noqa: F401 — re-exported for tests to assertIs
    validate_output_contract,      # noqa: F401 — re-exported for tests to assertIs
)


# ── Schema version ────────────────────────────────────────────────────────────
SCHEMA_VERSION: str = "1.0"


# ── Role-specific violation codes ─────────────────────────────────────────────
class RoleViolationCode:
    """Violation codes specific to B1 role validation (Phase 2)."""
    ROLE_ID_MISMATCH    = "ROLE_ID_MISMATCH"
    INVALID_ENUM_VALUE  = "INVALID_ENUM_VALUE"
    # Phase 1 codes are in OutputViolationCode (B0) — not redefined here.


# ── Canonical evidence availability states ────────────────────────────────────
class EvidenceAvailability:
    """
    Canonical enum for evidence availability states.
    Used in role schemas to preserve missing/unknown evidence explicitly
    rather than fabricating values.

    Rules:
    - UNKNOWN  — data exists but value cannot be determined (ambiguous)
    - MISSING  — data was expected but not found in any source
    - AVAILABLE — data was successfully acquired and is usable
    """
    AVAILABLE = "AVAILABLE"
    UNKNOWN   = "UNKNOWN"
    MISSING   = "MISSING"


# ── Common advisory_findings required fields (all roles) ──────────────────────
_ADVISORY_COMMON_REQUIRED: frozenset[str] = frozenset({"role_id", "schema_version"})


# ── Shared two-phase role output validator ────────────────────────────────────

def validate_role_advisory_output(
    payload: Any,
    *,
    role_id: str,
    advisory_allowed: frozenset,
    advisory_required: frozenset,
    enum_checks: Optional[dict[str, frozenset]] = None,
    type_checks: Optional[dict[str, type]] = None,
) -> Union[type(OUTPUT_VALID), OutputContractViolation]:
    """
    Two-phase validator for universal agent role outputs.

    Phase 1 — B0 root validation (delegates entirely to validate_output_contract):
      • payload must be a dict
      • Recursive forbidden governance key scan at unlimited depth
        (catches governance keys inside advisory_findings at any nesting)
      • advisory_only must be exactly True
      • Root-level allowlist (additionalProperties=false)
      • Root required fields
      • Root type checks

    Phase 2 — role-specific advisory_findings validation:
      • role_id must match the expected role
      • advisory_findings allowlist (additionalProperties=false for findings)
      • advisory_findings required fields
      • Type checks for well-known advisory_findings fields
      • Enum validation for constrained-value fields

    The B0 forbidden key scan (Phase 1) runs over the ENTIRE payload including
    advisory_findings and all nested structures. Phase 2 does NOT re-scan for
    forbidden keys — that would duplicate B0's responsibility. Tests verify this
    by injecting governance keys into advisory_findings and confirming the
    FORBIDDEN_GOVERNANCE_KEY violation code comes from Phase 1.

    Returns OUTPUT_VALID (truthy) on success.
    Returns OutputContractViolation (falsy) on any failure.
    Fail-closed: any unexpected exception → INTERNAL_ERROR violation.
    """
    try:
        # ── Phase 1: full B0 root validation ─────────────────────────────────
        # This includes recursive forbidden key scan over the entire payload
        # (advisory_findings included at any nesting depth).
        result = validate_output_contract(payload)
        if result is not OUTPUT_VALID:
            return result

        # ── Phase 2: advisory_findings role-specific validation ───────────────
        findings = payload.get("advisory_findings", {})
        if not isinstance(findings, dict):
            # B0 already checks this type; reaching here means logic error.
            return OutputContractViolation(
                code=OutputViolationCode.WRONG_TYPE,
                message="advisory_findings must be a dict",
                path="advisory_findings",
            )

        # 2a — role_id must match
        actual_role_id = findings.get("role_id")
        if actual_role_id != role_id:
            return OutputContractViolation(
                code=RoleViolationCode.ROLE_ID_MISMATCH,
                message=(
                    f"advisory_findings.role_id must be '{role_id}', "
                    f"got {actual_role_id!r}"
                ),
                path="advisory_findings.role_id",
            )

        # 2b — advisory_findings allowlist (additionalProperties=false)
        full_allowed = advisory_allowed | _ADVISORY_COMMON_REQUIRED
        for k in findings:
            if k not in full_allowed:
                return OutputContractViolation(
                    code=OutputViolationCode.EXTRA_FIELD,
                    message=(
                        f"Unknown field '{k}' in advisory_findings "
                        f"(not in allowlist for role '{role_id}')"
                    ),
                    path=f"advisory_findings.{k}",
                )

        # 2c — required fields in advisory_findings
        full_required = advisory_required | _ADVISORY_COMMON_REQUIRED
        for req in sorted(full_required):
            if req not in findings:
                return OutputContractViolation(
                    code=OutputViolationCode.MISSING_REQUIRED_FIELD,
                    message=(
                        f"Required field '{req}' missing from advisory_findings "
                        f"(role '{role_id}')"
                    ),
                    path=f"advisory_findings.{req}",
                )

        # 2d — type checks for well-known advisory_findings fields
        if type_checks:
            for field, expected_type in type_checks.items():
                val = findings.get(field)
                if val is not None and not isinstance(val, expected_type):
                    return OutputContractViolation(
                        code=OutputViolationCode.WRONG_TYPE,
                        message=(
                            f"advisory_findings.{field} must be "
                            f"{expected_type.__name__}, got {type(val).__name__}"
                        ),
                        path=f"advisory_findings.{field}",
                    )

        # 2e — enum validation for constrained-value fields
        if enum_checks:
            for field, valid_values in enum_checks.items():
                val = findings.get(field)
                if val is not None and val not in valid_values:
                    return OutputContractViolation(
                        code=RoleViolationCode.INVALID_ENUM_VALUE,
                        message=(
                            f"advisory_findings.{field}={val!r} is not a valid "
                            f"value. Valid: {sorted(valid_values)}"
                        ),
                        path=f"advisory_findings.{field}",
                    )

        return OUTPUT_VALID

    except Exception as exc:  # noqa: BLE001
        return OutputContractViolation(
            code=OutputViolationCode.INTERNAL_ERROR,
            message=f"Unexpected internal role validation error: {exc}",
            path="root",
        )
