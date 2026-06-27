"""
prob_ledger.py  —  Module D: Probability Component Ledger + Shrinkage
WOW v16 / Section 8.4

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
          final_model_prob:     float   (0–1)
          confidence_interval:  str     e.g. "0.53–0.61"
          uncertainty_haircut:  float   e.g. 0.04
          usable_probability:   float   (final_model_prob minus haircut)
          calibration_status:   "CALIBRATED"|"UNCALIBRATED"|"PROXY_ONLY"
          shrinkage_applied:    bool
          shrinkage_baseline:   str | None
        }

    Returns:
        {
          passed:                bool
          calibration_status:    str
          final_model_prob:      float | None
          usable_probability:    float | None
          uncertainty_haircut:   float | None
          confidence_interval:   str | None
          shrinkage_applied:     bool | None
          missing_required:      list[str]
          blocked_found:         list[str]
          influence_violations:  list[str]
          shrinkage_required:    bool
          uncalibrated_penalty:  float    (extra haircut when UNCALIBRATED)
          code:                  str
          detail:                str
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

    # 4. Shrinkage requirement
    shrinkage_required = (
        final_model_prob is not None
        and final_model_prob >= SHRINKAGE_THRESHOLD
        and not shrinkage_applied
    )

    # 5. Confidence interval required for FINAL_APPROVED
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
        violations.append("confidence_interval_missing: point estimate alone cannot produce FINAL_APPROVED")

    passed = len(violations) == 0

    code = "PROB_LEDGER_OK" if passed else "PROB_LEDGER_FAIL"
    if blocked_found:
        code = "NARRATIVE_COMPONENT_BLOCKED"

    result: dict[str, Any] = {
        "passed":                passed,
        "calibration_status":    effective_status,
        "final_model_prob":      final_model_prob,
        "usable_probability":    usable_probability,
        "uncertainty_haircut":   uncertainty_haircut,
        "confidence_interval":   confidence_interval,
        "shrinkage_applied":     shrinkage_applied,
        "shrinkage_baseline":    shrinkage_baseline,
        "missing_required":      missing_required,
        "blocked_found":         blocked_found,
        "influence_violations":  influence_violations,
        "shrinkage_required":    shrinkage_required,
        "has_confidence_interval": has_ci,
        "uncalibrated_penalty":  uncalibrated_penalty,
        "uncalibrated_kelly_cap": UNCALIBRATED_KELLY_CAP if effective_status == "UNCALIBRATED" else None,
        "code":                  code,
        "detail": (
            "Probability component ledger valid." if passed
            else f"Ledger violations: {'; '.join(violations)}"
        ),
    }

    row.setdefault("gates", {})["prob_ledger"] = result
    for v in violations:
        row["blockers"].append(f"PROB_LEDGER:{v}")

    return result
