"""
prob_ledger.py  —  Module D: Probability Component Ledger + Shrinkage
WOW v16 / Section 8.4 — Stage 2 Item 3 extended

model_prob is not a single analyst estimate. It must be constructed from
documented components with influence caps enforced.

COMPONENT TABLE (Section 8.4):
  No-vig market / sportsbook comp   Required   40–50%
  L10 distribution / median / rate  Required   25–35%
  Role / minutes / usage            Required   10–20%
  L5 trend modifier                 Optional   ±5% hard cap
  Matchup / context                 Optional   ±3–5% if quantified
  Narrative / story                 Never      0% — blocked

SHRINKAGE RULE:
  No L5/L10 sample can produce a model_prob ≥ 60% without shrinkage applied
  to at least one baseline: season, role-split, or market baseline.

CALIBRATION STATUS:
  CALIBRATED   — all required components present, shrinkage applied where needed
  UNCALIBRATED — missing components or shrinkage skipped; add 3% buffer,
                 quarter-Kelly max, block Power
  PROXY_ONLY   — market or L10 data is a proxy, not direct

STAGE 2 PROBABILITY SCHEMA (Item 3):
  Seven fields required before rank_eligible=True:
    raw_probability       — model output before calibration transform
    calibrated_probability — post-calibration estimate
    lower_bound           — numeric lower confidence bound (not a display string)
    upper_bound           — numeric upper confidence bound
    model_timestamp       — ISO timestamp when the model was built
    source_snapshot_id    — ID linking to llp_source_snapshots table
    calibration_method    — how calibration was applied (e.g. "platt", "isotonic")
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Component definitions
# ---------------------------------------------------------------------------

REQUIRED_COMPONENTS = {
    "market_no_vig",   # No-vig market / sportsbook comp
    "l10_distribution",  # L10 distribution / median / hit rate
    "role_usage",      # Role / minutes / usage
}

OPTIONAL_COMPONENTS = {
    "l5_trend",        # L5 trend modifier — ±5% hard cap
    "matchup_context", # Matchup / context — ±3–5% if quantified
}

BLOCKED_COMPONENTS = {
    "narrative",       # Narrative / story — 0%, always blocked
    "story",
    "feeling",
    "hunch",
}

# Influence bounds per component
COMPONENT_BOUNDS: dict[str, tuple[float, float]] = {
    "market_no_vig":    (0.40, 0.50),
    "l10_distribution": (0.25, 0.35),
    "role_usage":       (0.10, 0.20),
    "l5_trend":         (-0.05, 0.05),    # ±5% hard cap
    "matchup_context":  (-0.05, 0.05),    # ±3–5% if quantified
}

# Shrinkage threshold
SHRINKAGE_THRESHOLD = 0.60   # model_prob ≥ 60% requires documented shrinkage

# UNCALIBRATED penalties
UNCALIBRATED_EXTRA_HAIRCUT = 0.03   # +3% uncertainty buffer
UNCALIBRATED_KELLY_CAP = 0.25       # quarter-Kelly max

# WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — market/model lane separation.
# Model-side components: the sporting probability model is complete without
# market_no_vig; market readiness is an independent lane.
MODEL_REQUIRED_COMPONENTS = {"l10_distribution", "role_usage"}

# Typed market statuses (module-level constants — never in labels.py).
MARKET_STATUS_STALE_MARKET       = "STALE_MARKET"
MARKET_STATUS_REHYDRATE_REQUIRED = "REHYDRATE_REQUIRED"
MARKET_STATUS_AVAILABLE          = "MARKET_AVAILABLE"

# Line-drift tolerance: an old-line probability must never attach to a new line.
_LINE_DRIFT_TOLERANCE = 1e-9

# ---------------------------------------------------------------------------
# Stage 2 probability schema — required fields (Item 3)
# ---------------------------------------------------------------------------

STAGE2_REQUIRED_FIELDS = (
    "raw_probability",
    "calibrated_probability",
    "lower_bound",
    "upper_bound",
    "model_timestamp",
    "source_snapshot_id",
    "calibration_method",
)

# Numeric fields that must be parseable as float
STAGE2_NUMERIC_FIELDS = ("raw_probability", "calibrated_probability",
                          "lower_bound", "upper_bound")

# Probability range guard: bounds must be in (0, 1)
_PROB_RANGE_MIN = 0.0
_PROB_RANGE_MAX = 1.0


def _check_narrative_blocked(ledger: list[dict]) -> list[str]:
    """Return names of any blocked (narrative) components found in the ledger."""
    return [
        c.get("name", "")
        for c in ledger
        if c.get("name", "").lower() in BLOCKED_COMPONENTS
        and c.get("weight", 0) > 0
    ]


def _check_influence_bounds(ledger: list[dict]) -> list[str]:
    """Return violation strings for components outside their influence bounds."""
    violations: list[str] = []
    for c in ledger:
        name   = c.get("name", "")
        weight = c.get("weight")
        if weight is None or name not in COMPONENT_BOUNDS:
            continue
        lo, hi = COMPONENT_BOUNDS[name]
        if not (lo <= weight <= hi):
            violations.append(
                f"{name}: weight={weight:.2%} outside bounds [{lo:.0%}, {hi:.0%}]"
            )
    return violations


def _validate_stage2_schema(
    ledger_payload: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate the Stage 2 probability schema.

    Fields are sourced from ledger_payload first, then row (fallback).
    Returns:
      {
        complete           bool   — all 7 required fields present and valid
        rank_eligible      bool   — same as complete (future: may add extra gates)
        missing_fields     list[str]
        type_violations    list[str]
        bound_violations   list[str]
        violations         list[str]   — combined
      }
    """
    missing_fields:  list[str] = []
    type_violations: list[str] = []
    bound_violations: list[str] = []

    def _get(field: str):
        v = ledger_payload.get(field)
        if v is None:
            v = row.get(field)
        return v

    for field in STAGE2_REQUIRED_FIELDS:
        val = _get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing_fields.append(field)
            continue

        # Numeric type check
        if field in STAGE2_NUMERIC_FIELDS:
            # Item 2 (hardening): reject Python booleans — bool is a subclass of int
            # and float(True)==1.0, float(False)==0.0 which would pass range checks.
            if isinstance(val, bool):
                type_violations.append(
                    f"{field}: bool values are not accepted as probability numbers "
                    f"(got {val!r}); supply a float between 0 and 1"
                )
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                type_violations.append(
                    f"{field}: expected numeric float, got {type(val).__name__}={val!r}"
                )
                continue
            # Range check for probability fields — strict open interval (0, 1)
            if field in ("raw_probability", "calibrated_probability",
                          "lower_bound", "upper_bound"):
                if not (_PROB_RANGE_MIN < fval < _PROB_RANGE_MAX):
                    bound_violations.append(
                        f"{field}={fval:.4f} outside valid range "
                        f"({_PROB_RANGE_MIN}, {_PROB_RANGE_MAX})"
                    )

    # lower_bound must be ≤ upper_bound
    lb_raw = _get("lower_bound")
    ub_raw = _get("upper_bound")
    if lb_raw is not None and ub_raw is not None and not isinstance(lb_raw, bool) and not isinstance(ub_raw, bool):
        try:
            if float(lb_raw) > float(ub_raw):
                bound_violations.append(
                    f"lower_bound={lb_raw} > upper_bound={ub_raw}"
                )
        except (TypeError, ValueError):
            pass

    # Item 1 (hardening): lower_bound <= calibrated_probability <= upper_bound
    # A calibrated estimate outside its own interval is self-contradictory.
    cp_raw = _get("calibrated_probability")
    if (
        cp_raw is not None and not isinstance(cp_raw, bool)
        and lb_raw is not None and not isinstance(lb_raw, bool)
        and ub_raw is not None and not isinstance(ub_raw, bool)
    ):
        try:
            cp = float(cp_raw)
            lb = float(lb_raw)
            ub = float(ub_raw)
            if not (lb <= cp <= ub):
                bound_violations.append(
                    f"calibrated_probability={cp:.4f} is outside its own "
                    f"confidence interval [{lb:.4f}, {ub:.4f}]"
                )
        except (TypeError, ValueError):
            pass

    violations = (
        [f"missing:{f}" for f in missing_fields]
        + type_violations
        + bound_violations
    )
    complete = len(violations) == 0

    return {
        "complete":        complete,
        "rank_eligible":   complete,
        "missing_fields":  missing_fields,
        "type_violations": type_violations,
        "bound_violations": bound_violations,
        "violations":      violations,
    }


def run(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Validate the probability component ledger for a prop row.

    Expects enrichment["model_probability_ledger"] to be a dict with:
        {
          components: [
            {name: str, weight: float, value: float, source: str},
            ...
          ],
          final_model_prob:       float   (0–1)
          confidence_interval:    str     e.g. "0.53–0.61"  (display only)
          uncertainty_haircut:    float   e.g. 0.04
          usable_probability:     float   (final_model_prob minus haircut)
          calibration_status:     "CALIBRATED"|"UNCALIBRATED"|"PROXY_ONLY"
          shrinkage_applied:      bool
          shrinkage_baseline:     str | None
          -- Stage 2 fields (Item 3) --
          raw_probability:        float
          calibrated_probability: float
          lower_bound:            float   (numeric, not a display string)
          upper_bound:            float
          model_timestamp:        str     (ISO-8601)
          source_snapshot_id:     str
          calibration_method:     str
        }

    Returns:
        {
          passed:                   bool
          rank_eligible:            bool   (Stage 2: True only when all 7 schema fields valid)
          probability_schema:       dict   (Stage 2 validation result)
          calibration_status:       str
          final_model_prob:         float | None
          usable_probability:       float | None
          uncertainty_haircut:      float | None
          confidence_interval:      str | None
          shrinkage_applied:        bool | None
          missing_required:         list[str]
          blocked_found:            list[str]
          influence_violations:     list[str]
          shrinkage_required:       bool
          uncalibrated_penalty:     float
          code:                     str
          detail:                   str
        }
    """
    enr   = enrichment or {}
    ledger_payload = enr.get("model_probability_ledger") or {}
    if isinstance(ledger_payload, str):
        ledger_payload = {}

    components          = ledger_payload.get("components") or []
    final_model_prob    = ledger_payload.get("final_model_prob")
    confidence_interval = ledger_payload.get("confidence_interval")
    uncertainty_haircut = ledger_payload.get("uncertainty_haircut")
    usable_probability  = ledger_payload.get("usable_probability")
    calibration_status  = ledger_payload.get("calibration_status", "UNCALIBRATED")
    shrinkage_applied   = ledger_payload.get("shrinkage_applied", False)
    shrinkage_baseline  = ledger_payload.get("shrinkage_baseline")

    component_names = {c.get("name", "").lower() for c in components}

    # 1. Check required components are present
    missing_required = [
        r for r in REQUIRED_COMPONENTS
        if r not in component_names
    ]

    # 2. Check for blocked (narrative) components
    blocked_found = _check_narrative_blocked(components)

    # 3. Check influence bounds
    influence_violations = _check_influence_bounds(components)
    model_influence_violations = [
        violation for violation in influence_violations
        if not violation.startswith("market_no_vig:")
    ]
    market_influence_violations = [
        violation for violation in influence_violations
        if violation.startswith("market_no_vig:")
    ]

    # 4. Shrinkage requirement
    shrinkage_required = (
        final_model_prob is not None
        and final_model_prob >= SHRINKAGE_THRESHOLD
        and not shrinkage_applied
    )

    # 5. Confidence interval required for FINAL_APPROVED (display string)
    has_ci = bool(confidence_interval)

    # 6. Calibration override
    if missing_required or blocked_found or shrinkage_required:
        effective_status = "UNCALIBRATED"
    elif "proxy" in (calibration_status or "").lower():
        effective_status = "PROXY_ONLY"
    else:
        effective_status = calibration_status or "UNCALIBRATED"

    # 7. UNCALIBRATED penalty
    uncalibrated_penalty = (
        UNCALIBRATED_EXTRA_HAIRCUT if effective_status == "UNCALIBRATED" else 0.0
    )

    # Build violations list for blockers
    violations: list[str] = []
    if missing_required:
        violations.append(f"missing_required_components: {missing_required}")
    if blocked_found:
        violations.append(f"narrative_components_blocked: {blocked_found}")
    if influence_violations:
        violations.append(f"influence_out_of_bounds: {influence_violations}")
    if shrinkage_required:
        violations.append(
            f"shrinkage_required: final_model_prob={final_model_prob:.1%} "
            f">= {SHRINKAGE_THRESHOLD:.0%} but shrinkage_applied=False"
        )
    if not has_ci and final_model_prob is not None:
        violations.append(
            "confidence_interval_missing: point estimate alone cannot produce FINAL_APPROVED"
        )

    # ── Stage 2 Item 3: Probability schema validation ─────────────────────────
    schema_result = _validate_stage2_schema(ledger_payload, row)
    if schema_result["violations"]:
        for sv in schema_result["violations"]:
            violations.append(f"stage2_schema:{sv}")

    passed = len(violations) == 0

    # ── WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF ─────────────────────────────
    # Split rank_eligible into two independent sub-flags:
    #   model_probability_complete — 7 Stage-2 fields + l10_distribution +
    #     role_usage valid.  NOT affected by market_no_vig.
    #   market_lane_available — market_no_vig populated, valid, and not stale.
    # rank_eligible = model_probability_complete (sporting entry); an absent /
    # stale market gates only the money/edge lane via a typed market_status.
    model_missing_components = [
        c for c in MODEL_REQUIRED_COMPONENTS if c not in component_names
    ]
    model_probability_complete = (
        schema_result["complete"]
        and not model_missing_components
        and not blocked_found
        and not shrinkage_required
        and not model_influence_violations
    )

    market_lane_available = "market_no_vig" in component_names
    market_status = MARKET_STATUS_AVAILABLE
    if market_lane_available:
        # Stale/drifted line check: the market snapshot's line must match the
        # row's current line — an old-line probability never attaches to a
        # new line (REHYDRATE_REQUIRED rejects the stale snapshot).
        _mkt_comp = next(
            (c for c in components
             if (c.get("name") or "").lower() == "market_no_vig"),
            {},
        )
        _snap_line = _mkt_comp.get("snapshot_line")
        _row_line  = row.get("line")
        _drifted = bool(ledger_payload.get("market_line_drifted"))
        if not _drifted and _snap_line is not None and _row_line is not None:
            try:
                _drifted = abs(float(_snap_line) - float(_row_line)) > _LINE_DRIFT_TOLERANCE
            except (TypeError, ValueError):
                _drifted = True
        if _drifted or market_influence_violations:
            market_lane_available = False
            market_status = MARKET_STATUS_REHYDRATE_REQUIRED
    else:
        market_status = MARKET_STATUS_STALE_MARKET

    rank_eligible = model_probability_complete

    code = "PROB_LEDGER_OK" if passed else "PROB_LEDGER_FAIL"
    if blocked_found:
        code = "NARRATIVE_COMPONENT_BLOCKED"
    if not schema_result["complete"]:
        code = "PROB_SCHEMA_INCOMPLETE"

    result: dict[str, Any] = {
        "passed":                  passed,
        "rank_eligible":           rank_eligible,
        # WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — lane separation
        "model_probability_complete": model_probability_complete,
        "market_lane_available":      market_lane_available,
        "market_status":              market_status,
        "model_missing_components":   model_missing_components,
        "contract_version":           ledger_payload.get("contract_version"),
        "probability_schema":      schema_result,
        "calibration_status":      effective_status,
        "final_model_prob":        final_model_prob,
        "usable_probability":      usable_probability,
        "uncertainty_haircut":     uncertainty_haircut,
        "confidence_interval":     confidence_interval,
        "shrinkage_applied":       shrinkage_applied,
        "shrinkage_baseline":      shrinkage_baseline,
        "missing_required":        missing_required,
        "blocked_found":           blocked_found,
        "influence_violations":    influence_violations,
        "model_influence_violations": model_influence_violations,
        "market_influence_violations": market_influence_violations,
        "shrinkage_required":      shrinkage_required,
        "has_confidence_interval": has_ci,
        "uncalibrated_penalty":    uncalibrated_penalty,
        "uncalibrated_kelly_cap":  UNCALIBRATED_KELLY_CAP if effective_status == "UNCALIBRATED" else None,
        "code":                    code,
        "detail": (
            "Probability component ledger valid and Stage 2 schema complete." if passed
            else f"Ledger violations: {'; '.join(violations)}"
        ),
    }

    row.setdefault("gates", {})["prob_ledger"] = result
    # rank_eligible is surfaced directly on the row for downstream gates
    row["rank_eligible"] = rank_eligible
    row["model_probability_complete"] = model_probability_complete
    row["market_lane_available"]      = market_lane_available
    row["market_status"]              = market_status
    row.setdefault("blockers", [])
    if not market_lane_available:
        _mkt_blocker = f"MARKET_LANE:{market_status}:money_edge_lane_held"
        if _mkt_blocker not in row["blockers"]:
            row["blockers"].append(_mkt_blocker)
    for v in violations:
        row["blockers"].append(f"PROB_LEDGER:{v}")

    return result
