"""
gate_engine/prob_ledger_enforcer.py
Probability-Ledger Completeness Enforcer — Stage A (offline module only)

Validates that any row bearing a probability-qualifying label exposes a
complete, valid, and non-manufactured probability ledger before the label
may be committed.

CONTRACT (all ten fields required):
  Stage-2 schema fields (7):
    raw_probability, calibrated_probability, lower_bound, upper_bound,
    model_timestamp, source_snapshot_id, calibration_method
  Required probability components (3):
    market_no_vig, l10_distribution, role_usage

ENFORCEMENT RULES:
  - All 10 fields must be present, non-None, non-empty-string.
  - Numeric fields must be finite floats in the open interval (0, 1).
  - lower_bound ≤ calibrated_probability ≤ upper_bound.
  - lower_bound ≤ upper_bound.
  - Boolean values are rejected as probability numbers.
  - Components are validated from the `components` list in ledger_payload.
  - If a raw_probability_derivation field is set to a prohibited source
    (L5_AVG, L10_AVG, MARKET_NO_VIG), the enforcer flags a source violation.
  - If the registered model cannot produce a complete ledger, the row must
    receive a scoped technical failure — not a qualifying label.

OFFLINE INVARIANTS:
  - This module has no terminal-label authority.
  - This module does not mutate the input row or ledger dict.
  - This module has no dependency on app.py, classifier.py, pipeline.py,
    or any settlement / B4 / universal_agent module.

PROBABILITY-BEARING LABEL CLASS:
  The class of probability-bearing / qualifying labels is defined here as
  PROBABILITY_BEARING_LABELS (frozenset).  MODEL_QUALIFIED_HOLD,
  MARKET_VERIFIED_HOLD, MONEY_QUALIFIED, and FINAL_CONFIDENCE_HIGH are
  mandatory regression fixtures within this class.  Any future qualifying
  label must be added to PROBABILITY_BEARING_LABELS for the contract to
  apply to it — the four named labels are fixtures, not the definition.

Stage B wiring (into the pre-label choke point in classifier.py) is a
separate, independently-authorized task.

can_execute           = False
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Import reusable validation logic from prob_ledger — no duplication.
from .prob_ledger import (
    _validate_stage2_schema,   # noqa: PLC2701  (private by convention, shared internally)
    REQUIRED_COMPONENTS,
    STAGE2_REQUIRED_FIELDS,
)

# ---------------------------------------------------------------------------
# Module-level governance invariants
# ---------------------------------------------------------------------------

can_execute           = False   # offline/advisory only — never change
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False

# ---------------------------------------------------------------------------
# Probability-bearing label class
# ---------------------------------------------------------------------------

# Every label that requires a complete probability ledger before it can be
# committed to a row.  MODEL_QUALIFIED_HOLD, MARKET_VERIFIED_HOLD,
# MONEY_QUALIFIED, and FINAL_CONFIDENCE_HIGH are mandatory regression
# fixtures.  FINAL_APPROVED and MARKET_VERIFIED_HOLD_STALE are also
# probability-bearing because they represent dispositions reached via a
# quantitative model output.  Add future qualifying labels here.
PROBABILITY_BEARING_LABELS: frozenset[str] = frozenset({
    "MODEL_QUALIFIED_HOLD",
    "MARKET_VERIFIED_HOLD",
    "MARKET_VERIFIED_HOLD_STALE",
    "MONEY_QUALIFIED",
    "FINAL_APPROVED",
    "FINAL_CONFIDENCE_HIGH",
})

# ---------------------------------------------------------------------------
# All required ledger fields (ten total)
# ---------------------------------------------------------------------------

# Stage-2 schema fields (subset validated by _validate_stage2_schema):
_STAGE2_FIELDS: tuple[str, ...] = STAGE2_REQUIRED_FIELDS   # 7 fields

# Required component names (must appear in ledger_payload["components"]):
_REQUIRED_COMPONENT_NAMES: frozenset[str] = REQUIRED_COMPONENTS  # 3 names

# Full ordered list for audit reporting:
ALL_REQUIRED_LEDGER_FIELDS: tuple[str, ...] = _STAGE2_FIELDS + tuple(
    sorted(_REQUIRED_COMPONENT_NAMES)
)  # 10 fields total

# Prohibited derivation sources for raw_probability.
# If ledger_payload["raw_probability_derivation"] matches one of these, it
# indicates the probability was derived directly from L5/L10 averages or
# market odds — not from the registered model.
_PROHIBITED_DERIVATION_SOURCES: frozenset[str] = frozenset({
    "L5_AVG",
    "L10_AVG",
    "L10_MEAN",
    "L5_MEAN",
    "MARKET_NO_VIG",
    "MARKET_ODDS",
    "SPORTSBOOK_ODDS",
    "DIRECT_MARKET",
})

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnforcementResult:
    """
    Structured result from enforce().

    enforcer_passed     — True only when all 10 fields are present and valid.
    label_is_probability_bearing — True when the supplied label is in
                          PROBABILITY_BEARING_LABELS.
    violations          — combined list of all violation strings.
    missing_fields      — fields that are absent or None/empty-string.
    invalid_fields      — fields that fail type, range, or invariant checks.
    source_violations   — manufactured-value (derivation source) violations.
    enforcement_code    — short machine-readable outcome code.
    enforcement_detail  — human-readable detail string.

    Governance:
      terminal_label_authority = False  (the enforcer never assigns a label)
      can_execute              = False
    """
    enforcer_passed: bool
    label_is_probability_bearing: bool
    violations: tuple[str, ...]
    missing_fields: tuple[str, ...]
    invalid_fields: tuple[str, ...]
    source_violations: tuple[str, ...]
    enforcement_code: str
    enforcement_detail: str
    # Governance invariants — always False; frozen dataclass prevents mutation.
    terminal_label_authority: bool = False
    can_execute: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_probability_bearing_label(label: str | None) -> bool:
    """
    Return True if label belongs to the probability-bearing / qualifying label
    class.  Uses PROBABILITY_BEARING_LABELS (frozenset), not a hardcoded list
    of four names.
    """
    if not label:
        return False
    return label.strip().upper() in PROBABILITY_BEARING_LABELS


def enforce(
    ledger_payload: dict[str, Any],
    row: dict[str, Any] | None = None,
    label: str | None = None,
) -> EnforcementResult:
    """
    Validate the probability ledger against all ten required fields.

    Args:
        ledger_payload — the `model_probability_ledger` dict (or equivalent
                         payload dict) that supplies all ten required fields.
        row            — the prop row dict, used as a fallback source for
                         Stage-2 fields (mirrors _validate_stage2_schema
                         semantics).  Pass None for pure-ledger validation.
        label          — the terminal label the row is about to receive.
                         Used to populate label_is_probability_bearing.

    Returns:
        EnforcementResult — frozen, no row mutations.

    Governance:
        This function never writes to the row, never assigns a label,
        and never raises on bad input (it returns a FAIL result instead).
    """
    if not isinstance(ledger_payload, dict):
        ledger_payload = {} if ledger_payload is None else {}
    row_fallback: dict[str, Any] = row if isinstance(row, dict) else {}

    violations:      list[str] = []
    missing_fields:  list[str] = []
    invalid_fields:  list[str] = []
    source_viols:    list[str] = []

    # ── 1. Stage-2 schema validation (7 fields) ──────────────────────────
    try:
        schema = _validate_stage2_schema(ledger_payload, row_fallback)
        missing_fields.extend(schema.get("missing_fields") or [])
        for tv in schema.get("type_violations") or []:
            invalid_fields.append(tv)
        for bv in schema.get("bound_violations") or []:
            invalid_fields.append(bv)
        violations.extend(schema.get("violations") or [])
    except Exception as exc:  # pragma: no cover — defensive
        violations.append(f"ENFORCER_SCHEMA_ERROR:{type(exc).__name__}:{exc!s:.80}")

    # ── 2. Non-finite check for numeric probability fields ────────────────
    # _validate_stage2_schema uses float() + range check which catches inf/nan
    # via the range guard (nan comparisons are always False).  Double-check
    # explicitly to be robust against future schema changes.
    _PROB_NUMERIC = ("raw_probability", "calibrated_probability",
                     "lower_bound", "upper_bound")
    for fname in _PROB_NUMERIC:
        val = ledger_payload.get(fname)
        if val is None:
            val = row_fallback.get(fname)
        if val is not None and not isinstance(val, bool):
            try:
                fval = float(val)
                if not math.isfinite(fval):
                    viol = f"non_finite:{fname}={fval}"
                    if viol not in invalid_fields:
                        invalid_fields.append(viol)
                        violations.append(f"invalid:{viol}")
            except (TypeError, ValueError):
                pass  # Already caught by schema type_violations above.

    # ── 3. Required components validation (3 fields) ──────────────────────
    # Normalise: components must be a list; entries must be dicts.
    # Non-list or non-dict entries are treated as invalid/missing rather than
    # raising AttributeError — this enforces the "never raises on bad input"
    # contract stated in the module docstring.
    _raw_components = ledger_payload.get("components")
    if not isinstance(_raw_components, list):
        _raw_components = []
    components: list[dict] = []
    malformed_component_count = 0
    for _c in _raw_components:
        if isinstance(_c, dict):
            components.append(_c)
        else:
            malformed_component_count += 1
    if malformed_component_count:
        violations.append(
            f"malformed_components:{malformed_component_count}_non_dict_entries"
        )
        invalid_fields.append(f"components:malformed_entries={malformed_component_count}")

    present_component_names: set[str] = {
        str(c.get("name", "")).lower() for c in components
    }
    for req_comp in sorted(_REQUIRED_COMPONENT_NAMES):
        if req_comp.lower() not in present_component_names:
            missing_fields.append(f"component:{req_comp}")
            violations.append(f"missing:component:{req_comp}")

    # ── 4. Source provenance check — manufactured-value guard ─────────────
    derivation = (ledger_payload.get("raw_probability_derivation") or "").upper().strip()
    if derivation and derivation in _PROHIBITED_DERIVATION_SOURCES:
        viol = (
            f"manufactured_probability:raw_probability_derivation={derivation!r} "
            f"is a prohibited source; raw_probability must come from the registered "
            f"model, not from L5/L10 averages or market odds"
        )
        source_viols.append(viol)
        violations.append(f"source_violation:{viol}")

    # ── Build result ───────────────────────────────────────────────────────
    enforcer_passed = len(violations) == 0

    if enforcer_passed:
        code   = "ENFORCER_PASS"
        detail = "All 10 required probability-ledger fields are present and valid."
    elif source_viols:
        code   = "ENFORCER_FAIL_MANUFACTURED_PROBABILITY"
        detail = f"Source violation: {'; '.join(source_viols)}"
    elif missing_fields:
        code   = "ENFORCER_FAIL_INCOMPLETE_LEDGER"
        detail = f"Missing/empty fields: {', '.join(missing_fields)}"
    else:
        code   = "ENFORCER_FAIL_INVALID_VALUES"
        detail = f"Invalid field values: {'; '.join(invalid_fields)}"

    return EnforcementResult(
        enforcer_passed=enforcer_passed,
        label_is_probability_bearing=is_probability_bearing_label(label),
        violations=tuple(violations),
        missing_fields=tuple(missing_fields),
        invalid_fields=tuple(invalid_fields),
        source_violations=tuple(source_viols),
        enforcement_code=code,
        enforcement_detail=detail,
        terminal_label_authority=False,
        can_execute=False,
    )


def enforce_for_label(
    ledger_payload: dict[str, Any],
    label: str,
    row: dict[str, Any] | None = None,
) -> EnforcementResult:
    """
    Validate the probability ledger for a row that is about to receive
    `label`.  If the label is not probability-bearing, returns a PASS result
    immediately (no enforcement needed for non-qualifying labels).

    This is the intended call site for Stage B wiring.  The caller (the
    pre-label gate) should call this before writing label to the row.

    Returns:
        EnforcementResult with enforcer_passed=True for non-probability labels.
    """
    if not is_probability_bearing_label(label):
        return EnforcementResult(
            enforcer_passed=True,
            label_is_probability_bearing=False,
            violations=(),
            missing_fields=(),
            invalid_fields=(),
            source_violations=(),
            enforcement_code="ENFORCER_SKIP_NON_PROBABILITY_LABEL",
            enforcement_detail=(
                f"Label {label!r} is not in the probability-bearing class; "
                f"no ledger enforcement required."
            ),
            terminal_label_authority=False,
            can_execute=False,
        )
    return enforce(ledger_payload, row=row, label=label)
