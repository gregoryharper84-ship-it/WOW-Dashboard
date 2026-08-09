"""
gate_engine/kalshi_wx_shadow_native_schema.py
Step 14C — Native per-subagent closed-schema validators.

One strict validator per subagent.  Each enforces:
  - No unknown properties  (additionalProperties = false)
  - All required fields present
  - Correct Python types for every field
  - Correct enum values where applicable
  - Ceiling values drawn from the canonical KALSHI_WX_TERMINAL_LABEL_REGISTRY

These validators are wired into _run_single_tool_subagent() AFTER the
CapabilityBoundary hooks and BEFORE the successful SubagentResult is
constructed.  A validation failure is fatal: the tool_input is discarded
and SubagentResult(success=False, failure_reason="NATIVE_SCHEMA_VIOLATION:
<reason>") is returned.  The invalid output is never persisted.

Public API
----------
validate_subagent_output(subagent_id: str, tool_input: dict)
    -> tuple[bool, str | None]

    Returns (True, None) on success.
    Returns (False, "<reason>") on any violation.

No Anthropic API calls.  No DB writes.  No Flask imports.  Pure Python.
"""
from __future__ import annotations

from typing import Any, Optional

from gate_engine.kalshi_wx_terminal_labels import KALSHI_WX_TERMINAL_LABEL_REGISTRY

# ── Ceiling-capable label set (single source of truth) ────────────────────────
_CEILING_CAPABLE_LABELS: frozenset[str] = KALSHI_WX_TERMINAL_LABEL_REGISTRY

# ── Per-subagent enum sets ─────────────────────────────────────────────────────
_SCORING_MODE_VALUES: frozenset[str] = frozenset({"gaussian_forecast", "binary_final_cli"})
_CALIBRATION_STATUS_VALUES: frozenset[str] = frozenset({"CALIBRATED", "PROVISIONAL", "UNAVAILABLE"})
_UNCERTAINTY_TIER_VALUES: frozenset[str] = frozenset({"LOW", "MODERATE", "HIGH"})
_RECONCILIATION_STATUS_VALUES: frozenset[str] = frozenset({"OK", "PARTIAL", "CONFLICT", "MISSING"})
_RELIABILITY_IMPACT_VALUES: frozenset[str] = frozenset({"NONE", "MINOR", "MODERATE", "SIGNIFICANT"})
_CEILING_IMPACT_VALUES: frozenset[str] = frozenset({"NONE", "MINOR", "MODERATE", "SIGNIFICANT"})

# ── Allowed key sets per subagent (additionalProperties = false) ───────────────
_FC_ALLOWED: frozenset[str] = frozenset({
    "scoring_mode", "calibration_status", "uncertainty_tier",
    "recommended_ceiling", "blockers", "notes",
})
_SR_ALLOWED: frozenset[str] = frozenset({
    "sources_present", "sources_missing", "conflicts",
    "reconciliation_status", "notes",
})
_CD_ALLOWED: frozenset[str] = frozenset({
    "contradictions_found", "ceiling_impacted", "revised_ceiling", "notes",
})
_UR_ALLOWED: frozenset[str] = frozenset({
    "regime_unusual", "regime_factors", "reliability_impact", "notes",
})
_UE_ALLOWED: frozenset[str] = frozenset({
    "uncertainty_tier", "uncertainty_sources", "ceiling_impact",
    "sigma_f_estimate", "horizon_hours_estimate", "notes",
})


# ═════════════════════════════════════════════════════════════════════════════
# Low-level field checkers — each returns (passed: bool, reason: str | None)
# ═════════════════════════════════════════════════════════════════════════════

def _no_extra_keys(d: dict, allowed: frozenset) -> tuple[bool, Optional[str]]:
    """Fail if d contains any key not in allowed."""
    unknown = sorted(set(d.keys()) - allowed)
    if unknown:
        return False, f"unknown properties not allowed: {unknown}"
    return True, None


def _require_string(
    d: dict,
    key: str,
    *,
    required: bool,
    enum: Optional[frozenset] = None,
) -> tuple[bool, Optional[str]]:
    if key not in d:
        if required:
            return False, f"missing required field '{key}'"
        return True, None
    v = d[key]
    if not isinstance(v, str):
        return False, (
            f"field '{key}' must be a string; "
            f"got {type(v).__name__} {v!r}"
        )
    if enum is not None and v not in enum:
        return False, (
            f"field '{key}' has invalid value {v!r}; "
            f"allowed values: {sorted(enum)}"
        )
    return True, None


def _require_bool(
    d: dict,
    key: str,
    *,
    required: bool,
) -> tuple[bool, Optional[str]]:
    if key not in d:
        if required:
            return False, f"missing required field '{key}'"
        return True, None
    v = d[key]
    # int is a subclass of bool in Python — isinstance(True, int) is True —
    # so we must check bool FIRST.
    if not isinstance(v, bool):
        return False, (
            f"field '{key}' must be a boolean (true/false); "
            f"got {type(v).__name__} {v!r}"
        )
    return True, None


def _require_array_of_strings(
    d: dict,
    key: str,
    *,
    required: bool,
) -> tuple[bool, Optional[str]]:
    if key not in d:
        if required:
            return False, f"missing required field '{key}'"
        return True, None
    v = d[key]
    if not isinstance(v, list):
        return False, (
            f"field '{key}' must be an array; "
            f"got {type(v).__name__}"
        )
    for i, item in enumerate(v):
        if not isinstance(item, str):
            return False, (
                f"field '{key}[{i}]' must be a string; "
                f"got {type(item).__name__} {item!r}"
            )
    return True, None


def _require_number(
    d: dict,
    key: str,
    *,
    required: bool,
) -> tuple[bool, Optional[str]]:
    if key not in d:
        if required:
            return False, f"missing required field '{key}'"
        return True, None
    v = d[key]
    # booleans are ints in Python — reject them explicitly
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False, (
            f"field '{key}' must be a number; "
            f"got {type(v).__name__} {v!r}"
        )
    return True, None


def _first_failure(
    *checks: tuple[bool, Optional[str]],
) -> tuple[bool, Optional[str]]:
    """Return the first failing check, or (True, None) if all pass."""
    for passed, reason in checks:
        if not passed:
            return passed, reason
    return True, None


# ═════════════════════════════════════════════════════════════════════════════
# Per-subagent validators
# ═════════════════════════════════════════════════════════════════════════════

def validate_forecast_context(tool_input: dict) -> tuple[bool, Optional[str]]:
    """
    Validates the output of emit_forecast_context.

    Required: scoring_mode, calibration_status, uncertainty_tier,
              recommended_ceiling, blockers
    Optional: notes
    No other keys allowed.
    """
    if not isinstance(tool_input, dict):
        return False, f"tool_input must be a dict; got {type(tool_input).__name__}"
    return _first_failure(
        _no_extra_keys(tool_input, _FC_ALLOWED),
        _require_string(tool_input, "scoring_mode",       required=True,  enum=_SCORING_MODE_VALUES),
        _require_string(tool_input, "calibration_status", required=True,  enum=_CALIBRATION_STATUS_VALUES),
        _require_string(tool_input, "uncertainty_tier",   required=True,  enum=_UNCERTAINTY_TIER_VALUES),
        _require_string(tool_input, "recommended_ceiling",required=True,  enum=_CEILING_CAPABLE_LABELS),
        _require_array_of_strings(tool_input, "blockers", required=True),
        _require_string(tool_input, "notes",              required=False),
    )


def validate_source_reconciliation(tool_input: dict) -> tuple[bool, Optional[str]]:
    """
    Validates the output of emit_source_reconciliation.

    Required: sources_present, sources_missing, conflicts, reconciliation_status
    Optional: notes
    No other keys allowed.
    """
    if not isinstance(tool_input, dict):
        return False, f"tool_input must be a dict; got {type(tool_input).__name__}"
    return _first_failure(
        _no_extra_keys(tool_input, _SR_ALLOWED),
        _require_array_of_strings(tool_input, "sources_present",      required=True),
        _require_array_of_strings(tool_input, "sources_missing",      required=True),
        _require_array_of_strings(tool_input, "conflicts",            required=True),
        _require_string(tool_input, "reconciliation_status", required=True,
                        enum=_RECONCILIATION_STATUS_VALUES),
        _require_string(tool_input, "notes", required=False),
    )


def validate_contradiction_detection(tool_input: dict) -> tuple[bool, Optional[str]]:
    """
    Validates the output of emit_contradiction_detection.

    Required: contradictions_found, ceiling_impacted
    Optional: revised_ceiling (only meaningful when ceiling_impacted=True),
              notes
    No other keys allowed.
    """
    if not isinstance(tool_input, dict):
        return False, f"tool_input must be a dict; got {type(tool_input).__name__}"
    return _first_failure(
        _no_extra_keys(tool_input, _CD_ALLOWED),
        _require_array_of_strings(tool_input, "contradictions_found", required=True),
        _require_bool(tool_input, "ceiling_impacted", required=True),
        # revised_ceiling is optional at the schema level; validate type+enum if present
        _require_string(tool_input, "revised_ceiling", required=False,
                        enum=_CEILING_CAPABLE_LABELS),
        _require_string(tool_input, "notes", required=False),
    )


def validate_unusual_regime(tool_input: dict) -> tuple[bool, Optional[str]]:
    """
    Validates the output of emit_regime_assessment.

    Required: regime_unusual, regime_factors, reliability_impact
    Optional: notes
    No other keys allowed.
    """
    if not isinstance(tool_input, dict):
        return False, f"tool_input must be a dict; got {type(tool_input).__name__}"
    return _first_failure(
        _no_extra_keys(tool_input, _UR_ALLOWED),
        _require_bool(tool_input, "regime_unusual",    required=True),
        _require_array_of_strings(tool_input, "regime_factors", required=True),
        _require_string(tool_input, "reliability_impact", required=True,
                        enum=_RELIABILITY_IMPACT_VALUES),
        _require_string(tool_input, "notes", required=False),
    )


def validate_uncertainty_explanation(tool_input: dict) -> tuple[bool, Optional[str]]:
    """
    Validates the output of emit_uncertainty_summary.

    Required: uncertainty_tier, uncertainty_sources, ceiling_impact
    Optional: sigma_f_estimate (number), horizon_hours_estimate (number), notes
    No other keys allowed.
    """
    if not isinstance(tool_input, dict):
        return False, f"tool_input must be a dict; got {type(tool_input).__name__}"
    return _first_failure(
        _no_extra_keys(tool_input, _UE_ALLOWED),
        _require_string(tool_input, "uncertainty_tier", required=True,
                        enum=_UNCERTAINTY_TIER_VALUES),
        _require_array_of_strings(tool_input, "uncertainty_sources", required=True),
        _require_string(tool_input, "ceiling_impact", required=True,
                        enum=_CEILING_IMPACT_VALUES),
        _require_number(tool_input, "sigma_f_estimate",       required=False),
        _require_number(tool_input, "horizon_hours_estimate", required=False),
        _require_string(tool_input, "notes", required=False),
    )


# ═════════════════════════════════════════════════════════════════════════════
# Public dispatcher
# ═════════════════════════════════════════════════════════════════════════════

_VALIDATORS = {
    "forecast_context":        validate_forecast_context,
    "source_reconciliation":   validate_source_reconciliation,
    "contradiction_detection": validate_contradiction_detection,
    "unusual_regime":          validate_unusual_regime,
    "uncertainty_explanation": validate_uncertainty_explanation,
}


def validate_subagent_output(
    subagent_id: str,
    tool_input: Any,
) -> tuple[bool, Optional[str]]:
    """
    Dispatch to the correct per-subagent validator.

    Returns (True, None) on success.
    Returns (False, "<reason>") on validation failure or unrecognised subagent_id.

    This is the only public entry point.  All callers should use this function,
    not the individual validators, so the dispatch table is the single source
    of truth for which subagent_ids are recognised.
    """
    validator = _VALIDATORS.get(subagent_id)
    if validator is None:
        return False, (
            f"UNKNOWN_SUBAGENT_ID: {subagent_id!r} has no registered native validator; "
            f"registered ids: {sorted(_VALIDATORS)}"
        )
    return validator(tool_input)
