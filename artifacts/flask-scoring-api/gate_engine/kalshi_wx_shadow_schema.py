"""
gate_engine/kalshi_wx_shadow_schema.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 9: closed schema + validator

Strict closed schema and validator for the output produced by a future Kalshi
Weather shadow research agent.  This module is schema/validation code only.

OUT OF SCOPE (not in this module):
  - Any Claude Agent SDK code or subagent definitions
  - Any orchestrator or hook
  - Any shadow ledger persistence (no DB writes here)
  - Any paired-snapshot fields (research_snapshot_id, canonical_event_id,
    timestamps — those are defined in a later step)
  - Any changes to existing routes or ceiling resolvers

SHADOW FAILURE INVARIANT
  A ShadowValidationResult with passed=False carries shadow_failure_only=True.
  That result MUST NEVER:
    - be written to the production weather_scout_log table
    - be returned in any production API response
    - influence any existing route's behaviour
  It belongs exclusively to the (not-yet-wired) shadow failure path.

ISOLATION INVARIANT
  This module must NOT be imported by or referenced from:
    gate_engine/wow_runtime_manifest.py
    gate_engine/command_center/cc_labels.py
    gate_engine/command_center/ceiling_resolver.py
"""
from __future__ import annotations

import dataclasses
import enum
from typing import Any, Optional, Tuple

from gate_engine.kalshi_wx_shadow_registry import CEILING_CAPABLE_LABELS

# ─────────────────────────────────────────────────────────────────────────────
# Forbidden governance-authority keys
#
# If ANY of these key names appear anywhere in the agent payload — at any
# nesting depth, inside any object or array — validation fails immediately.
# Presence of the key is the violation; the value is irrelevant.
# ─────────────────────────────────────────────────────────────────────────────
FORBIDDEN_GOVERNANCE_KEYS: frozenset[str] = frozenset({
    "terminal_label",
    "final_label",
    "label",               # bare governance-style key; bracket items use "bracket_range"
    "can_execute",
    "execute",
    "capital_allocation",
    "execution_permission",
    "trade_authorization",
    "governance_state",
    "authorized",
    "approved_for_execution",
})

# ─────────────────────────────────────────────────────────────────────────────
# Enumerated constants
# ─────────────────────────────────────────────────────────────────────────────
VALID_LANE: str = "KALSHI_WEATHER"

VALID_STATUSES: frozenset[str] = frozenset({
    "COMPLETE",
    "SCHEMA_FAIL",
    "TOOL_FAIL",
    "BLOCKED",
})

# ─────────────────────────────────────────────────────────────────────────────
# Root schema  (additionalProperties = false at root)
# Every required field is listed; allowed == required (no extras permitted).
# ─────────────────────────────────────────────────────────────────────────────
ROOT_REQUIRED_KEYS: frozenset[str] = frozenset({
    "agent_id",
    "run_id",
    "lane",
    "status",
    "facts",
    "probabilities",
    "uncertainty",
    "agent_observed_blockers",   # exact name — "blockers" is NOT an alias
    "source_conflicts",
    "recommended_ceiling",
    "advisory_only",
})
ROOT_ALLOWED_KEYS: frozenset[str] = ROOT_REQUIRED_KEYS   # additionalProperties = false

# ─────────────────────────────────────────────────────────────────────────────
# Nested object schemas  (additionalProperties = false at each nested level)
# ─────────────────────────────────────────────────────────────────────────────

# facts — what the shadow agent observed about weather data acquisition
FACTS_ALLOWED_KEYS: frozenset[str] = frozenset({
    "city",                  # str — city under evaluation
    "date",                  # str — ISO-8601 date (YYYY-MM-DD)
    "nws_station_code",      # str — NWS station identifier (e.g. "KNYC")
    "scoring_mode",          # str — "gaussian_forecast" | "binary_final_cli"
    "forecast_high_f",       # float — forecasted daily high in °F
    "cli_high_f",            # float — NWS CLI observed high (binary mode only)
    "forecast_source_tier",  # str — "tier1" | "tier2" | "tier3"
    "data_acquisition_notes",# list[str] — free-text acquisition notes
})

# probabilities — bracket-level probability assessments
PROBABILITIES_ALLOWED_KEYS: frozenset[str] = frozenset({
    "model_prob_sum",        # float — sum of model_prob across brackets (sanity check)
    "calibration_status",    # str — e.g. "CALIBRATED" | "PROVISIONAL" | "UNAVAILABLE"
})

# uncertainty — characterisation of forecast uncertainty
UNCERTAINTY_ALLOWED_KEYS: frozenset[str] = frozenset({
    "horizon_hours",      # float — forecast horizon in hours
    "sigma_f",            # float — forecast standard deviation in °F
    "uncertainty_tier",   # str — e.g. "LOW" | "MODERATE" | "HIGH"
    "notes",              # str — free-text uncertainty notes
})


# ─────────────────────────────────────────────────────────────────────────────
# Violation taxonomy
# ─────────────────────────────────────────────────────────────────────────────
class ShadowSchemaViolation(enum.Enum):
    FORBIDDEN_GOVERNANCE_KEY = "FORBIDDEN_GOVERNANCE_KEY"
    """A banned governance-authority key was found at any depth in the payload."""

    INVALID_LANE             = "INVALID_LANE"
    """lane is not exactly the string "KALSHI_WEATHER"."""

    INVALID_STATUS           = "INVALID_STATUS"
    """status is not one of the four allowed literals."""

    INVALID_CEILING          = "INVALID_CEILING"
    """recommended_ceiling is not in CEILING_CAPABLE_LABELS."""

    ADVISORY_ONLY_NOT_TRUE   = "ADVISORY_ONLY_NOT_TRUE"
    """advisory_only is not the boolean literal True."""

    MISSING_REQUIRED_FIELD   = "MISSING_REQUIRED_FIELD"
    """A required top-level field is absent."""

    WRONG_TYPE               = "WRONG_TYPE"
    """A field has the wrong Python type."""

    EXTRA_FIELD              = "EXTRA_FIELD"
    """An unexpected key violates additionalProperties=false."""


# ─────────────────────────────────────────────────────────────────────────────
# Validation result
# ─────────────────────────────────────────────────────────────────────────────
@dataclasses.dataclass(frozen=True)
class ShadowValidationResult:
    """
    Structured outcome of validate_shadow_output().

    On failure:
      passed            = False
      violation         = the ShadowSchemaViolation that fired first
      failure_reason    = human-readable explanation
      failure_path      = JSONPath-style location (e.g. "$.facts.level1.terminal_label")
      shadow_failure_only = True  ← MUST NEVER reach production paths

    On success:
      passed            = True
      violation         = None
      failure_reason    = None
      failure_path      = None
      shadow_failure_only = False  (pass results have no failure path to enforce)
    """
    passed:             bool
    violation:          Optional[ShadowSchemaViolation]
    failure_reason:     Optional[str]
    failure_path:       Optional[str]
    shadow_failure_only: bool


# Singleton for the passing case — avoid allocating a new object on every PASS.
SHADOW_PASS = ShadowValidationResult(
    passed=True,
    violation=None,
    failure_reason=None,
    failure_path=None,
    shadow_failure_only=False,
)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fail(
    violation: ShadowSchemaViolation,
    reason: str,
    path: str,
) -> ShadowValidationResult:
    """Return a shadow-only failure result."""
    return ShadowValidationResult(
        passed=False,
        violation=violation,
        failure_reason=reason,
        failure_path=path,
        shadow_failure_only=True,   # invariant: NEVER reaches production paths
    )


def _scan_forbidden_keys(obj: Any, path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Recursively scan `obj` for any key in FORBIDDEN_GOVERNANCE_KEYS.

    Returns (forbidden_key, json_path) on the first hit, or (None, None) if clean.
    Descends into nested dicts and lists at any depth.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in FORBIDDEN_GOVERNANCE_KEYS:
                return key, f"{path}.{key}"
            hit, hit_path = _scan_forbidden_keys(value, f"{path}.{key}")
            if hit is not None:
                return hit, hit_path
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            hit, hit_path = _scan_forbidden_keys(item, f"{path}[{i}]")
            if hit is not None:
                return hit, hit_path
    return None, None


def _check_extra_keys(
    obj: dict,
    allowed: frozenset,
    path: str,
) -> Optional[ShadowValidationResult]:
    """
    Return a failure result if `obj` contains any key not in `allowed`.
    Returns None if clean.
    """
    extra = set(obj.keys()) - allowed
    if extra:
        key = sorted(extra)[0]   # deterministic: pick alphabetically first
        return _fail(
            ShadowSchemaViolation.EXTRA_FIELD,
            f"Unexpected key {key!r} violates additionalProperties=false",
            f"{path}.{key}",
        )
    return None


def _validate_facts(obj: Any, path: str) -> Optional[ShadowValidationResult]:
    """Validate the facts nested object."""
    if not isinstance(obj, dict):
        return _fail(ShadowSchemaViolation.WRONG_TYPE,
                     "facts must be an object", path)
    err = _check_extra_keys(obj, FACTS_ALLOWED_KEYS, path)
    if err:
        return err
    # Type checks on present fields
    str_fields = ("city", "date", "nws_station_code", "scoring_mode", "forecast_source_tier")
    for f in str_fields:
        if f in obj and not isinstance(obj[f], str):
            return _fail(ShadowSchemaViolation.WRONG_TYPE,
                         f"facts.{f} must be a string", f"{path}.{f}")
    num_fields = ("forecast_high_f", "cli_high_f")
    for f in num_fields:
        if f in obj:
            v = obj[f]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return _fail(ShadowSchemaViolation.WRONG_TYPE,
                             f"facts.{f} must be a number", f"{path}.{f}")
    if "data_acquisition_notes" in obj:
        notes = obj["data_acquisition_notes"]
        if not isinstance(notes, list):
            return _fail(ShadowSchemaViolation.WRONG_TYPE,
                         "facts.data_acquisition_notes must be an array", f"{path}.data_acquisition_notes")
        for i, n in enumerate(notes):
            if not isinstance(n, str):
                return _fail(ShadowSchemaViolation.WRONG_TYPE,
                             f"facts.data_acquisition_notes[{i}] must be a string",
                             f"{path}.data_acquisition_notes[{i}]")
    return None


def _validate_probabilities(obj: Any, path: str) -> Optional[ShadowValidationResult]:
    """Validate the probabilities nested object."""
    if not isinstance(obj, dict):
        return _fail(ShadowSchemaViolation.WRONG_TYPE,
                     "probabilities must be an object", path)
    err = _check_extra_keys(obj, PROBABILITIES_ALLOWED_KEYS, path)
    if err:
        return err
    if "model_prob_sum" in obj:
        v = obj["model_prob_sum"]
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return _fail(ShadowSchemaViolation.WRONG_TYPE,
                         "probabilities.model_prob_sum must be a number",
                         f"{path}.model_prob_sum")
    if "calibration_status" in obj and not isinstance(obj["calibration_status"], str):
        return _fail(ShadowSchemaViolation.WRONG_TYPE,
                     "probabilities.calibration_status must be a string",
                     f"{path}.calibration_status")
    return None


def _validate_uncertainty(obj: Any, path: str) -> Optional[ShadowValidationResult]:
    """Validate the uncertainty nested object."""
    if not isinstance(obj, dict):
        return _fail(ShadowSchemaViolation.WRONG_TYPE,
                     "uncertainty must be an object", path)
    err = _check_extra_keys(obj, UNCERTAINTY_ALLOWED_KEYS, path)
    if err:
        return err
    num_fields = ("horizon_hours", "sigma_f")
    for f in num_fields:
        if f in obj:
            v = obj[f]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return _fail(ShadowSchemaViolation.WRONG_TYPE,
                             f"uncertainty.{f} must be a number", f"{path}.{f}")
    str_fields = ("uncertainty_tier", "notes")
    for f in str_fields:
        if f in obj and not isinstance(obj[f], str):
            return _fail(ShadowSchemaViolation.WRONG_TYPE,
                         f"uncertainty.{f} must be a string", f"{path}.{f}")
    return None


def _validate_string_array(
    value: Any,
    field_name: str,
    path: str,
) -> Optional[ShadowValidationResult]:
    """Validate that `value` is a list of strings."""
    if not isinstance(value, list):
        return _fail(ShadowSchemaViolation.WRONG_TYPE,
                     f"{field_name} must be an array", path)
    for i, item in enumerate(value):
        if not isinstance(item, str):
            return _fail(ShadowSchemaViolation.WRONG_TYPE,
                         f"{field_name}[{i}] must be a string", f"{path}[{i}]")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def validate_shadow_output(payload: Any) -> ShadowValidationResult:
    """
    Validate a shadow research agent output payload against the closed schema.

    Validation order (each step aborts on first failure):
      1. Root type must be dict.
      2. Forbidden governance-authority key scan — recursive, entire payload,
         before any other check.  Presence of a forbidden key fails regardless
         of its value or the surrounding structure's validity.
      3. additionalProperties=false at root (no extra keys).
      4. All required root fields must be present.
      5. String type checks on scalar fields (agent_id, run_id, lane, status,
         recommended_ceiling).
      6. lane must be exactly "KALSHI_WEATHER".
      7. status must be in VALID_STATUSES.
      8. advisory_only must be the boolean literal True (not 1, not "true").
      9. recommended_ceiling must be in CEILING_CAPABLE_LABELS.
     10. facts — nested schema + additionalProperties=false.
     11. probabilities — nested schema + additionalProperties=false.
     12. uncertainty — nested schema + additionalProperties=false.
     13. agent_observed_blockers — must be an array of strings.
     14. source_conflicts — must be an array of strings.

    Returns SHADOW_PASS on success.
    Returns a ShadowValidationResult with passed=False and shadow_failure_only=True
    on any failure.  The failure result carries a ShadowSchemaViolation, a
    human-readable failure_reason, and a JSONPath-style failure_path.
    """
    # ── 1. Root type ──────────────────────────────────────────────────────────
    if not isinstance(payload, dict):
        return _fail(
            ShadowSchemaViolation.WRONG_TYPE,
            f"Payload must be a JSON object (dict), got {type(payload).__name__}",
            "$",
        )

    # ── 2. Forbidden governance-authority key scan ────────────────────────────
    # Runs unconditionally before all structural checks — even an extra/unknown
    # key that also happens to be a forbidden key must surface as
    # FORBIDDEN_GOVERNANCE_KEY, not as EXTRA_FIELD.
    forbidden_key, forbidden_path = _scan_forbidden_keys(payload, "$")
    if forbidden_key is not None:
        return _fail(
            ShadowSchemaViolation.FORBIDDEN_GOVERNANCE_KEY,
            (
                f"Forbidden governance-authority key {forbidden_key!r} found at "
                f"{forbidden_path}. Shadow agents must never assert governance "
                f"authority — this key is unconditionally rejected at any depth."
            ),
            forbidden_path,
        )

    # ── 3. additionalProperties = false at root ───────────────────────────────
    err = _check_extra_keys(payload, ROOT_ALLOWED_KEYS, "$")
    if err:
        return err

    # ── 4. Required fields ────────────────────────────────────────────────────
    for field in sorted(ROOT_REQUIRED_KEYS):
        if field not in payload:
            return _fail(
                ShadowSchemaViolation.MISSING_REQUIRED_FIELD,
                f"Required field {field!r} is absent",
                f"$.{field}",
            )

    # ── 5. Scalar string types ────────────────────────────────────────────────
    for field in ("agent_id", "run_id", "lane", "status", "recommended_ceiling"):
        if not isinstance(payload[field], str):
            return _fail(
                ShadowSchemaViolation.WRONG_TYPE,
                f"{field!r} must be a string, got {type(payload[field]).__name__}",
                f"$.{field}",
            )

    # ── 6. lane ───────────────────────────────────────────────────────────────
    if payload["lane"] != VALID_LANE:
        return _fail(
            ShadowSchemaViolation.INVALID_LANE,
            (
                f"lane must be exactly {VALID_LANE!r}; "
                f"got {payload['lane']!r}"
            ),
            "$.lane",
        )

    # ── 7. status ─────────────────────────────────────────────────────────────
    if payload["status"] not in VALID_STATUSES:
        return _fail(
            ShadowSchemaViolation.INVALID_STATUS,
            (
                f"status {payload['status']!r} is not one of "
                f"{sorted(VALID_STATUSES)}"
            ),
            "$.status",
        )

    # ── 8. advisory_only must be exactly the boolean literal True ─────────────
    # type(True) is bool; type(1) is int; True is True; 1 is not True.
    advisory = payload["advisory_only"]
    if not (type(advisory) is bool and advisory is True):
        return _fail(
            ShadowSchemaViolation.ADVISORY_ONLY_NOT_TRUE,
            (
                f"advisory_only must be the boolean literal True; "
                f"got {advisory!r} (type {type(advisory).__name__}). "
                f"Shadow agent output is advisory-only by definition — "
                f"False or missing is a hard schema failure."
            ),
            "$.advisory_only",
        )

    # ── 9. recommended_ceiling ────────────────────────────────────────────────
    ceiling = payload["recommended_ceiling"]
    if ceiling not in CEILING_CAPABLE_LABELS:
        return _fail(
            ShadowSchemaViolation.INVALID_CEILING,
            (
                f"recommended_ceiling {ceiling!r} is not in CEILING_CAPABLE_LABELS "
                f"(the terminal_projection.kalshi_weather set). "
                f"OperationalState values (e.g. SHADOW_ONLY), ModelReadiness values "
                f"(e.g. WEATHER_SCOUT), and invented strings are all rejected."
            ),
            "$.recommended_ceiling",
        )

    # ── 10. facts ─────────────────────────────────────────────────────────────
    err = _validate_facts(payload["facts"], "$.facts")
    if err:
        return err

    # ── 11. probabilities ─────────────────────────────────────────────────────
    err = _validate_probabilities(payload["probabilities"], "$.probabilities")
    if err:
        return err

    # ── 12. uncertainty ───────────────────────────────────────────────────────
    err = _validate_uncertainty(payload["uncertainty"], "$.uncertainty")
    if err:
        return err

    # ── 13. agent_observed_blockers ───────────────────────────────────────────
    err = _validate_string_array(
        payload["agent_observed_blockers"],
        "agent_observed_blockers",
        "$.agent_observed_blockers",
    )
    if err:
        return err

    # ── 14. source_conflicts ──────────────────────────────────────────────────
    err = _validate_string_array(
        payload["source_conflicts"],
        "source_conflicts",
        "$.source_conflicts",
    )
    if err:
        return err

    return SHADOW_PASS
