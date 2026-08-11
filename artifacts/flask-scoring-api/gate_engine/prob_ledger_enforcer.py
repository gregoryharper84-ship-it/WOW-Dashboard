"""
gate_engine/prob_ledger_enforcer.py
WOW-PATCH-2026-08-10-STAGE-A-PROBABILITY-LEDGER-OUTLIER-RECOMPUTE

Probability-ledger completeness enforcer — offline, advisory only.

Registry design (taxonomy-driven, two-layer)
---------------------------------------------
The probability-bearing label class is built at module load time from two
layers into PROBABILITY_BEARING_LABELS (frozenset[str]):

  Layer 1 — _PROB_BEARING_PROP_LABELS
    PropLabel enum members (gate_engine/labels.py, the canonical label
    registry) that require a complete probability ledger.  Taxonomy audit
    (2026-08-10): MODEL_QUALIFIED_HOLD, MARKET_VERIFIED_HOLD, MONEY_QUALIFIED,
    FINAL_APPROVED.

  Layer 2 — _PROB_BEARING_EXTENDED
    Qualifying labels identified in the taxonomy audit that exist as string
    literals in the codebase but are not yet PropLabel enum members:
    MARKET_VERIFIED_HOLD_STALE, FINAL_CONFIDENCE_HIGH, FINAL_LOCK,
    EDGE_QUALIFIED.

Enforcement logic:   label in PROBABILITY_BEARING_LABELS
Never:               if label == "MODEL_QUALIFIED_HOLD" or ...
Adding a new qualifying label requires only extending _PROB_BEARING_PROP_LABELS
or _PROB_BEARING_EXTENDED — no change to enforcement logic.

Mandatory regression fixtures (test-enforced):
  MODEL_QUALIFIED_HOLD, MARKET_VERIFIED_HOLD, MONEY_QUALIFIED,
  FINAL_CONFIDENCE_HIGH.
A synthetic 5th label (not one of those four) is also governed — tested to
prove the registry is not hardcoded to exactly four strings.

Ledger contract (10 required fields)
-------------------------------------
Stage-2 schema (7): raw_probability, calibrated_probability,
  lower_bound, upper_bound, model_timestamp, source_snapshot_id,
  calibration_method.
Required components (3): market_no_vig, l10_distribution, role_usage.

Validity invariants:
  - No null/None for any required field
  - No NaN/Infinity for any numeric probability field
  - Probability values in open interval (0, 1) — booleans rejected
  - lower_bound ≤ calibrated_probability ≤ upper_bound
  - lower_bound ≤ upper_bound
  - No manufactured derivation source (L5_AVG, L10_AVG, MARKET_NO_VIG, …)
  - Non-dict or malformed components entries treated as invalid/missing

Governance
----------
  can_execute              = False   (advisory/validation only)
  PRODUCTION_AUTHORITY     = False
  USER_OUTPUT_AUTHORITY    = False
  TERMINAL_LABEL_AUTHORITY = False   (never assigns or changes any label)

Zero dependency on FOLLOWUP_193/194/195 or B4 code:
  no imports from app.py, classifier.py, pipeline.py, settlement_worker.py,
  universal_agent/*, pipeline_state.py, pipeline_gateway.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from .labels import PropLabel
from .prob_ledger import (
    REQUIRED_COMPONENTS,
    STAGE2_REQUIRED_FIELDS,
    _validate_stage2_schema,
)

# ---------------------------------------------------------------------------
# Governance constants
# ---------------------------------------------------------------------------
can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False   # never assigns or changes any label

# ---------------------------------------------------------------------------
# Registry: probability-bearing label class — two layers, taxonomy-driven
# ---------------------------------------------------------------------------

# Layer 1: PropLabel enum members whose qualifying semantics require a complete
# probability ledger.  Source: gate_engine/labels.py (canonical enum).
# Taxonomy audit 2026-08-10 identified these four as the PropLabel members
# at or above the MODEL_QUALIFIED tier in the qualifying hierarchy.
_PROB_BEARING_PROP_LABELS: frozenset[PropLabel] = frozenset({
    PropLabel.MODEL_QUALIFIED_HOLD,
    PropLabel.MARKET_VERIFIED_HOLD,
    PropLabel.MONEY_QUALIFIED,
    PropLabel.FINAL_APPROVED,
})

# Layer 2: Qualifying labels found in the taxonomy audit that exist as string
# literals in the codebase but are not yet PropLabel enum members.
# Sources: classifier.py, route_registry.py, wow_runtime_manifest.py, app.py.
_PROB_BEARING_EXTENDED: frozenset[str] = frozenset({
    "MARKET_VERIFIED_HOLD_STALE",   # stale variant of MARKET_VERIFIED_HOLD
    "FINAL_CONFIDENCE_HIGH",         # high-confidence qualifying variant
    "FINAL_LOCK",                    # terminal approval state
    "EDGE_QUALIFIED",                # edge-qualified approval state
})

# Authoritative probability-bearing label registry — union of both layers,
# built once at import time.  Enforcement is a single membership check:
#   label in PROBABILITY_BEARING_LABELS
# No if/elif on specific label names anywhere in this module.
PROBABILITY_BEARING_LABELS: frozenset[str] = frozenset(
    {lbl.value for lbl in _PROB_BEARING_PROP_LABELS}
    | _PROB_BEARING_EXTENDED
)

# Mandatory regression fixtures — tests assert these are always present.
MANDATORY_REGRESSION_FIXTURES: tuple[str, ...] = (
    "MODEL_QUALIFIED_HOLD",
    "MARKET_VERIFIED_HOLD",
    "MONEY_QUALIFIED",
    "FINAL_CONFIDENCE_HIGH",
)

# All required ledger fields (10 = 7 stage-2 + 3 component names).
# Exported for tests; derived from canonical constants in prob_ledger.py.
ALL_REQUIRED_LEDGER_FIELDS: frozenset[str] = (
    frozenset(STAGE2_REQUIRED_FIELDS)
    | frozenset(f"component:{c}" for c in REQUIRED_COMPONENTS)
)

# Prohibited raw_probability_derivation sources — manufactured probabilities.
_PROHIBITED_DERIVATION_SOURCES: frozenset[str] = frozenset({
    "L5_AVG",
    "L10_AVG",
    "MARKET_NO_VIG",
    "L5_AVERAGE",
    "L10_AVERAGE",
    "ROLLING_AVERAGE",
    "SIMPLE_AVERAGE",
    "NAIVE_HIT_RATE",
    "HIT_RATE",
})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnforcementResult:
    """
    Immutable result returned by enforce() / enforce_for_label().

    Governance invariants are hard-coded to False — the frozen dataclass
    prevents mutation after construction.
    """
    enforcer_passed:              bool
    enforcement_code:             str    # "ENFORCER_PASS", "ENFORCER_FAIL_*", "ENFORCER_SKIP_*"
    label_is_probability_bearing: bool
    violations:                   tuple[str, ...]
    missing_fields:               tuple[str, ...]
    invalid_fields:               tuple[str, ...]
    source_violations:            tuple[str, ...]
    # Governance — always False; frozen prevents mutation.
    terminal_label_authority:     bool = False
    can_execute:                  bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_probability_bearing_label(label: Any) -> bool:
    """
    Return True iff label is a member of the probability-bearing registry.

    Accepts any input — non-string or blank/None always returns False.
    """
    if not isinstance(label, str):
        return False
    return label.strip() in PROBABILITY_BEARING_LABELS


def enforce(
    ledger_payload: Any,
    row: Optional[dict] = None,
) -> EnforcementResult:
    """
    Validate a model_probability_ledger dict against the full probability
    contract.  Never raises on any input — always returns EnforcementResult.

    Parameters
    ----------
    ledger_payload : the ledger dict to validate.  Non-dict (None, int, str,
                     list, object()) is treated as an empty ledger; all 10
                     required fields will be reported missing.
    row            : optional prop-row dict used as a Stage-2 field fallback
                     (mirrors _validate_stage2_schema fallback semantics).

    Returns
    -------
    EnforcementResult — frozen.  Input dicts are not mutated.
    """
    if not isinstance(ledger_payload, dict):
        ledger_payload = {}
    row_fallback: dict[str, Any] = row if isinstance(row, dict) else {}

    violations:   list[str] = []
    missing:      list[str] = []
    invalid:      list[str] = []
    source_viols: list[str] = []

    # ── 1. Stage-2 schema validation (7 fields, numeric bounds) ──────────
    try:
        schema = _validate_stage2_schema(ledger_payload, row_fallback)
        missing.extend(schema.get("missing_fields") or [])
        for tv in schema.get("type_violations") or []:
            invalid.append(tv)
        for bv in schema.get("bound_violations") or []:
            invalid.append(bv)
        violations.extend(schema.get("violations") or [])
    except Exception as exc:          # defensive — _validate_stage2_schema is a lib call
        violations.append(
            f"ENFORCER_SCHEMA_ERROR:{type(exc).__name__}:{str(exc)[:80]}"
        )

    # ── 2. Explicit non-finite guard (belt-and-suspenders over schema) ────
    _NUMERIC_FIELDS = ("raw_probability", "calibrated_probability",
                       "lower_bound", "upper_bound")
    for fname in _NUMERIC_FIELDS:
        val = ledger_payload.get(fname)
        if val is None:
            val = row_fallback.get(fname)
        if val is not None and not isinstance(val, bool):
            try:
                fval = float(val)
                if not math.isfinite(fval):
                    tag = f"non_finite:{fname}={fval}"
                    if tag not in invalid:
                        invalid.append(tag)
                        violations.append(f"invalid:{tag}")
            except (TypeError, ValueError):
                pass   # already caught by schema type_violations

    # ── 3. Required components (3 names; entries must be dicts) ──────────
    _raw_components = ledger_payload.get("components")
    if not isinstance(_raw_components, list):
        _raw_components = []

    valid_components: list[dict] = []
    malformed_count = 0
    for _c in _raw_components:
        if isinstance(_c, dict):
            valid_components.append(_c)
        else:
            malformed_count += 1

    if malformed_count:
        tag = f"malformed_components:{malformed_count}_non_dict_entries"
        violations.append(tag)
        invalid.append(f"components:{tag}")

    present_names: set[str] = {
        str(c.get("name", "")).lower() for c in valid_components
    }
    for req in sorted(REQUIRED_COMPONENTS):
        if req.lower() not in present_names:
            missing.append(f"component:{req}")
            violations.append(f"missing:component:{req}")

    # ── 4. Manufactured-probability source guard ──────────────────────────
    derivation = (ledger_payload.get("raw_probability_derivation") or "").upper().strip()
    if derivation and derivation in _PROHIBITED_DERIVATION_SOURCES:
        sv = (
            f"manufactured_probability:raw_probability_derivation={derivation!r} "
            f"is a prohibited source; raw_probability must come from the "
            f"registered model, not from L5/L10 averages or market odds"
        )
        source_viols.append(sv)
        violations.append(f"source_violation:{sv}")

    # ── Build result ──────────────────────────────────────────────────────
    passed = (len(violations) == 0)

    if source_viols:
        code = "ENFORCER_FAIL_MANUFACTURED_PROBABILITY"
    elif violations:
        code = "ENFORCER_FAIL_INCOMPLETE_LEDGER"
    else:
        code = "ENFORCER_PASS"

    return EnforcementResult(
        enforcer_passed=passed,
        enforcement_code=code,
        label_is_probability_bearing=False,   # not label-specific; use enforce_for_label
        violations=tuple(violations),
        missing_fields=tuple(missing),
        invalid_fields=tuple(invalid),
        source_violations=tuple(source_viols),
        terminal_label_authority=False,
        can_execute=False,
    )


def enforce_for_label(
    ledger_payload: Any,
    label: str,
    row: Optional[dict] = None,
) -> EnforcementResult:
    """
    Validate a ledger for a specific terminal label.

    If label is not in PROBABILITY_BEARING_LABELS, returns an
    ENFORCER_SKIP_NON_PROBABILITY_LABEL result with enforcer_passed=True
    (no ledger validation is required).

    The enforcement check is:   label in PROBABILITY_BEARING_LABELS
    — never an if/elif on specific label names.
    """
    label_str = (label or "").strip()
    is_prob = is_probability_bearing_label(label_str)

    if not is_prob:
        return EnforcementResult(
            enforcer_passed=True,
            enforcement_code="ENFORCER_SKIP_NON_PROBABILITY_LABEL",
            label_is_probability_bearing=False,
            violations=(),
            missing_fields=(),
            invalid_fields=(),
            source_violations=(),
            terminal_label_authority=False,
            can_execute=False,
        )

    base = enforce(ledger_payload, row=row)
    return EnforcementResult(
        enforcer_passed=base.enforcer_passed,
        enforcement_code=base.enforcement_code,
        label_is_probability_bearing=True,
        violations=base.violations,
        missing_fields=base.missing_fields,
        invalid_fields=base.invalid_fields,
        source_violations=base.source_violations,
        terminal_label_authority=False,
        can_execute=False,
    )
