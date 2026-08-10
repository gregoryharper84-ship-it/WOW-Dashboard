"""
gate_engine/universal_agent/output_contract.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Closed Output Contract — allowlist-based validator for universal agent output.

Design decisions (from Weather shadow pilot lessons):
1. ALLOWLIST (not blocklist): every field must appear in _ROOT_ALLOWED.
   Unknown fields are rejected with EXTRA_FIELD. Blocklist alone was proven
   insufficient during Weather Step 14C; allowlist diff is the only safe pattern.
2. FORBIDDEN SCAN RUNS FIRST: before the allowlist check.
   A banned governance key reports FORBIDDEN_GOVERNANCE_KEY, not EXTRA_FIELD.
   This matches the scan order in kalshi_wx_shadow_schema.py (lines 386-400).
3. RECURSIVE FORBIDDEN SCAN at any nesting depth (dicts and lists).
4. OUTPUT_VALID singleton on success (mirrors SHADOW_PASS singleton pattern).
5. FAIL CLOSED: any unexpected exception → INTERNAL_ERROR violation.
6. advisory_only must be exactly True (not truthy — exactly bool True).
7. No terminal_label authority. No can_execute. No capital-allocation fields.
   Applies at any nesting depth, not just root level.

Public API:
  FORBIDDEN_GOVERNANCE_KEYS  — frozenset shared with capability_boundary.py
  validate_output_contract()  — primary validator
  OUTPUT_VALID               — singleton returned on success
  OutputContractViolation    — returned on failure
  OutputViolationCode        — violation code constants
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union


# ── Forbidden governance key set ──────────────────────────────────────────────
# Shared with capability_boundary.py — single source of truth.
# Applied recursively at every nesting depth.
# Scan runs BEFORE allowlist check so a banned key gets FORBIDDEN_GOVERNANCE_KEY
# rather than the misleading EXTRA_FIELD code.
# All entries lowercase; comparison uses k.lower() for case-insensitive matching.

FORBIDDEN_GOVERNANCE_KEYS: frozenset[str] = frozenset({
    # Terminal label / label authority
    "terminal_label", "final_label", "label", "qualifying_label",
    "gate_label", "outcome_label", "score_label",
    # Execution / capital authority
    "can_execute", "execute", "execution",
    "capital", "capital_allocation", "capital_authorized",
    "trade", "trading", "live_trading",
    "authorized", "approved", "approved_for_execution",
    "production_authority", "user_output_authority", "deployment_authority", "deploy",
    # Governance state / decisions
    "governance_state", "governance_override",
    "final_decision", "stake_tier", "is_playable",
})


# ── Root-level allowed key set (additionalProperties=false) ──────────────────
# ONLY these keys may appear at the root of an agent output payload.
# Unknown keys are rejected. To add a new field, add it here explicitly.

_ROOT_ALLOWED: frozenset[str] = frozenset({
    "agent_id",           # required — string
    "advisory_only",      # required — must be exactly True
    "lane",               # required — string
    "snapshot_id",        # required — string
    "run_id",             # required — string
    "advisory_findings",  # required — dict of lane-specific advisory data
    "confidence_note",    # optional — plain-text confidence note
    "data_gaps",          # optional — list of identified data gaps
    "source_conflicts",   # optional — list of source conflicts found
    "model_id",           # optional — model identifier string
    "input_tokens",       # optional — int
    "output_tokens",      # optional — int
    "estimated_cost_usd", # optional — float
    "latency_ms",         # optional — int
    "schema_version",     # optional — version string
})

_ROOT_REQUIRED: frozenset[str] = frozenset({
    "agent_id",
    "advisory_only",
    "lane",
    "snapshot_id",
    "run_id",
    "advisory_findings",
})


# ── Result types ──────────────────────────────────────────────────────────────

class OutputViolationCode:
    FORBIDDEN_GOVERNANCE_KEY = "FORBIDDEN_GOVERNANCE_KEY"
    EXTRA_FIELD              = "EXTRA_FIELD"
    MISSING_REQUIRED_FIELD   = "MISSING_REQUIRED_FIELD"
    WRONG_TYPE               = "WRONG_TYPE"
    ADVISORY_ONLY_NOT_TRUE   = "ADVISORY_ONLY_NOT_TRUE"
    NOT_A_DICT               = "NOT_A_DICT"
    INTERNAL_ERROR           = "INTERNAL_ERROR"


@dataclass(frozen=True)
class OutputContractViolation:
    """
    Returned by validate_output_contract() when validation fails.
    Immutable; contains the violation code, human-readable message, and JSON path.
    """
    code:    str
    message: str
    path:    str = ""   # JSON path of the violation, e.g. "root.advisory_findings.sub"

    def __bool__(self) -> bool:
        return False    # A violation is falsy; OUTPUT_VALID is truthy.


@dataclass(frozen=True)
class _OutputValid:
    """
    Singleton returned when validation passes. Truthy.
    Mirrors the SHADOW_PASS singleton from kalshi_wx_shadow_schema.py.
    """
    passed: bool = True

    def __bool__(self) -> bool:
        return True


OUTPUT_VALID: _OutputValid = _OutputValid()


# ── Validation helpers ────────────────────────────────────────────────────────

def _scan_forbidden_keys(
    obj: Any,
    path: str = "root",
) -> Union[OutputContractViolation, None]:
    """
    Recursively scan obj for any forbidden governance key.
    Returns the first violation found, or None if clean.

    Descends into dicts (checking all keys) and lists/tuples (checking all items)
    at unlimited nesting depth. Key comparison is case-insensitive (k.lower()).
    """
    if isinstance(obj, dict):
        for k in obj:
            if isinstance(k, str) and k.lower() in FORBIDDEN_GOVERNANCE_KEYS:
                return OutputContractViolation(
                    code=OutputViolationCode.FORBIDDEN_GOVERNANCE_KEY,
                    message=f"Forbidden governance key '{k}' at path '{path}.{k}'",
                    path=f"{path}.{k}",
                )
        # Recurse into values
        for k, v in obj.items():
            result = _scan_forbidden_keys(v, f"{path}.{k}")
            if result is not None:
                return result
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            result = _scan_forbidden_keys(item, f"{path}[{i}]")
            if result is not None:
                return result
    return None


def _check_extra_keys(
    payload: dict[str, Any],
    allowed: frozenset[str],
    path: str = "root",
) -> Union[OutputContractViolation, None]:
    """
    additionalProperties=false: reject any key not in the allowed set.
    Exact string match (not lowercased) — field names are case-sensitive.
    """
    for k in payload:
        if k not in allowed:
            return OutputContractViolation(
                code=OutputViolationCode.EXTRA_FIELD,
                message=(
                    f"Unknown field '{k}' at '{path}' is not permitted "
                    f"(additionalProperties=false / allowlist enforcement)"
                ),
                path=f"{path}.{k}",
            )
    return None


# ── Primary validator ─────────────────────────────────────────────────────────

def validate_output_contract(
    payload: Any,
) -> Union[_OutputValid, OutputContractViolation]:
    """
    Validate an agent output payload against the closed output contract.

    Validation order (forbidden scan runs FIRST, per Weather schema pattern):
      1. payload must be a dict
      2. Recursive forbidden governance key scan (any depth)
      3. advisory_only must be exactly bool True
      4. Extra-key check (allowlist diff, additionalProperties=false)
      5. Required-field presence check
      6. Type checks for well-known fields

    Returns OUTPUT_VALID (truthy singleton) on success.
    Returns OutputContractViolation (falsy) on any failure.
    Fail-closed: any unexpected exception → INTERNAL_ERROR violation.
    """
    try:
        # Step 1 — must be a dict
        if not isinstance(payload, dict):
            return OutputContractViolation(
                code=OutputViolationCode.NOT_A_DICT,
                message=f"Output payload must be a dict, got {type(payload).__name__}",
                path="root",
            )

        # Step 2 — forbidden governance key scan (BEFORE extra-key check)
        # This ensures a banned key reports FORBIDDEN_GOVERNANCE_KEY,
        # not the misleading EXTRA_FIELD code.
        violation = _scan_forbidden_keys(payload, path="root")
        if violation is not None:
            return violation

        # Step 3 — advisory_only must be exactly True
        if "advisory_only" in payload:
            if payload["advisory_only"] is not True:
                return OutputContractViolation(
                    code=OutputViolationCode.ADVISORY_ONLY_NOT_TRUE,
                    message=(
                        f"advisory_only must be exactly True (bool), "
                        f"got {payload['advisory_only']!r} ({type(payload['advisory_only']).__name__})"
                    ),
                    path="root.advisory_only",
                )

        # Step 4 — allowlist diff (additionalProperties=false)
        violation = _check_extra_keys(payload, _ROOT_ALLOWED, path="root")
        if violation is not None:
            return violation

        # Step 5 — required fields
        for req in sorted(_ROOT_REQUIRED):   # sorted for deterministic error order
            if req not in payload:
                return OutputContractViolation(
                    code=OutputViolationCode.MISSING_REQUIRED_FIELD,
                    message=f"Required field '{req}' is missing from output payload",
                    path=f"root.{req}",
                )

        # Step 6 — type checks for well-known fields
        if not isinstance(payload.get("agent_id", ""), str):
            return OutputContractViolation(
                code=OutputViolationCode.WRONG_TYPE,
                message="agent_id must be a string",
                path="root.agent_id",
            )
        if not isinstance(payload.get("lane", ""), str):
            return OutputContractViolation(
                code=OutputViolationCode.WRONG_TYPE,
                message="lane must be a string",
                path="root.lane",
            )
        if not isinstance(payload.get("advisory_findings", {}), dict):
            return OutputContractViolation(
                code=OutputViolationCode.WRONG_TYPE,
                message="advisory_findings must be a dict",
                path="root.advisory_findings",
            )
        for int_field in ("input_tokens", "output_tokens", "latency_ms"):
            if int_field in payload and not isinstance(payload[int_field], int):
                return OutputContractViolation(
                    code=OutputViolationCode.WRONG_TYPE,
                    message=f"{int_field} must be an int",
                    path=f"root.{int_field}",
                )
        if "estimated_cost_usd" in payload and not isinstance(
            payload["estimated_cost_usd"], (int, float)
        ):
            return OutputContractViolation(
                code=OutputViolationCode.WRONG_TYPE,
                message="estimated_cost_usd must be a number (int or float)",
                path="root.estimated_cost_usd",
            )

        return OUTPUT_VALID

    except Exception as exc:  # noqa: BLE001
        return OutputContractViolation(
            code=OutputViolationCode.INTERNAL_ERROR,
            message=f"Unexpected internal validation error: {exc}",
            path="root",
        )


def valid_output_payload(
    *,
    agent_id: str = "test-agent",
    lane: str = "PLAYER_PROPS",
    snapshot_id: str = "snap-001",
    run_id: str = "run-001",
    advisory_findings: Optional[dict] = None,  # type: ignore[name-defined]
    **extras: Any,
) -> dict[str, Any]:
    """
    Test helper: build a minimal valid output payload.
    Used by test harnesses to construct correct payloads before injecting mutations.
    This function is deliberately shared with tests so they exercise the same
    structure as production output (Weather Step 14D lesson).
    """
    payload: dict[str, Any] = {
        "agent_id":        agent_id,
        "advisory_only":   True,
        "lane":            lane,
        "snapshot_id":     snapshot_id,
        "run_id":          run_id,
        "advisory_findings": advisory_findings if advisory_findings is not None else {},
    }
    payload.update(extras)
    return payload


# Make valid_output_payload importable without Optional needing re-import
from typing import Optional  # noqa: E402 — intentional late import for forward ref
