"""
gate_engine/moneyline/mlb_starter_change.py
WOW-PATCH-2026-08-08-MLB-SP-SCRATCH

Retrospective patch: Aug. 8, 2026 Rays–Mariners postmortem.

PROBLEM IDENTIFIED
------------------
A late SP scratch (Griffin Jax, Rays) triggered an automatic ~10pp negative
penalty regardless of the actual quality of the replacement pitching plan.
This confounded two distinct effects:
  1. Replacement plan quality relative to the original starter  (point estimate)
  2. Additional uncertainty from an unconfirmed, multi-arm plan (distribution)

WHAT THIS PATCH DOES
--------------------
1. Classifies the starter change into one of four categories:
     KNOWN_DOWNGRADE        — replacement ERA clearly worse than original
     ROUGHLY_NEUTRAL        — ERA gap < 0.50 runs; opener/bulk counted as plan
     KNOWN_UPGRADE          — replacement clearly better
     UNRESOLVED_REPLACEMENT — critical plan data missing; fail-closed to HOLD

2. Evaluates the replacement pitching plan as a complete architecture:
     SINGLE_REPLACEMENT  — direct 1-for-1 pitcher swap
     OPENER_BULK         — opener (1–3 IP) + bulk arm(s) + bullpen
     BULLPEN_GAME        — full committee from inning 1

   An OPENER_BULK or BULLPEN_GAME plan is treated as a LEGITIMATE ARCHITECTURE
   — not an automatically degraded single-starter state.

3. Computes a point-estimate adjustment from the quality delta alone:
     probability_adjustment = -era_delta × ERA_TO_PROB_SLOPE
     era_delta = replacement_era – original_era  (positive → worse)
     Cap: ±MAX_PROB_ADJUSTMENT (8pp from pitcher change alone)

4. Separates uncertainty (distribution widening) as an independent effect:
     uncertainty_expansion   — added to calibration uncertainty separately
     Does NOT enter the point estimate; avoids double-counting.

5. Fail-closed contract:
     If replacement_plan data is missing/unresolved → UNRESOLVED_REPLACEMENT
     → should_hold=True → pipeline caps at MODEL_QUALIFIED_HOLD, no fabrication.

WHAT THIS PATCH DOES NOT DO
---------------------------
- Does NOT use retrospective game results (Tampa Bay won 3–2) as model inputs.
- Does NOT blend market odds into the quality delta.
- Does NOT weaken any existing governance ceiling.
- Does NOT set can_execute=True.

can_execute=False unconditional.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False  # UNCONDITIONAL


# ---------------------------------------------------------------------------
# Patch constants
# ---------------------------------------------------------------------------

PATCH_ID:     str   = "WOW-PATCH-2026-08-08-MLB-SP-SCRATCH"
PATCH_ENGINE: str   = "v1.0"

# MLB average starter ERA reference (used when original ERA is missing)
_MLB_AVG_STARTER_ERA: float = 4.10

# Conversion: 1 ERA-point difference → probability shift (home-team perspective)
# Calibrated from Pythagorean-expectation margins across a full 9-inning game.
# A full ERA difference of 1.0 run over ~6 starter innings ≈ 2.5pp win-prob shift.
_ERA_TO_PROB_SLOPE: float = 0.025

# Maximum probability adjustment from starter change alone (8pp cap)
_MAX_PROB_ADJUSTMENT: float = 0.08

# ERA delta thresholds for classification
_DOWNGRADE_STRONG_THRESHOLD:  float = 1.50   # era_delta ≥ 1.50 → KNOWN_DOWNGRADE (strong)
_DOWNGRADE_MODERATE_THRESHOLD: float = 0.50  # era_delta ≥ 0.50 → KNOWN_DOWNGRADE (moderate)
_UPGRADE_THRESHOLD:            float = -0.50  # era_delta ≤ -0.50 → KNOWN_UPGRADE

# Uncertainty expansion constants (basis points, architecture-driven)
_UNCERTAINTY_ARCHITECTURE: dict[str, float] = {
    "OPENER_BULK":        0.015,   # multi-arm handoff adds variability
    "BULLPEN_GAME":       0.025,   # full committee: highest variability
    "SINGLE_REPLACEMENT": 0.005,   # direct swap: minimal extra uncertainty
    "UNKNOWN":            0.020,   # architecture not specified
}

_UNCERTAINTY_WORKLOAD: dict[str, float] = {
    "FRESH":    0.000,
    "TAXED":    0.010,
    "DEPLETED": 0.020,
    "UNKNOWN":  0.015,   # no workload data → penalise for unknowns
}

_UNCERTAINTY_NO_LEVERAGE_ARMS: float = 0.010

# Absolute cap on uncertainty expansion from this patch
_MAX_UNCERTAINTY_EXPANSION: float = 0.05


# ---------------------------------------------------------------------------
# Classification constants
# ---------------------------------------------------------------------------

class StarterChangeClassification:
    NO_CHANGE_DETECTED      = "NO_CHANGE_DETECTED"
    KNOWN_DOWNGRADE         = "KNOWN_DOWNGRADE"
    ROUGHLY_NEUTRAL         = "ROUGHLY_NEUTRAL"
    KNOWN_UPGRADE           = "KNOWN_UPGRADE"
    UNRESOLVED_REPLACEMENT  = "UNRESOLVED_REPLACEMENT"


class ReplacementArchitecture:
    SINGLE_REPLACEMENT = "SINGLE_REPLACEMENT"
    OPENER_BULK        = "OPENER_BULK"
    BULLPEN_GAME       = "BULLPEN_GAME"
    UNKNOWN            = "UNKNOWN"


class ResearchLabel:
    PROCEED    = "PROCEED"
    HOLD       = "HOLD"
    UNRESOLVED = "UNRESOLVED"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class StarterChangePlan:
    """
    Complete output of the MLB starter-change analysis.

    Auditable output fields required by the patch spec:
      original_pitching_plan_expectation   — ERA of original plan
      replacement_pitching_plan_expectation — ERA of replacement plan
      point_estimate_delta                  — probability_adjustment (signed)
      uncertainty_calibration_delta         — uncertainty_expansion (additive)
      bullpen_workload_status               — from enrichment
      late_news_trigger                     — what triggered the scratch
      final_research_label                  — HOLD | PROCEED | UNRESOLVED
    """
    # Governance
    can_execute:  bool  = False   # UNCONDITIONAL
    patch_id:     str   = PATCH_ID
    patch_engine: str   = PATCH_ENGINE

    # Classification
    classification: str = StarterChangeClassification.NO_CHANGE_DETECTED

    # Fail-closed flag
    should_hold: bool = False

    # ── Core outputs ──────────────────────────────────────────────────────────
    # point-estimate delta: additive to independent_prob_post_sim (home perspective)
    probability_adjustment: float = 0.0

    # calibration layer: additive to dynamic_uncertainty, NOT to point estimate
    uncertainty_expansion:  float = 0.0

    # ── Auditable fields ──────────────────────────────────────────────────────
    original_pitching_plan_expectation:    float | None = None   # ERA
    replacement_pitching_plan_expectation: float | None = None   # ERA
    point_estimate_delta:                  float = 0.0           # same as probability_adjustment
    uncertainty_calibration_delta:         float = 0.0           # same as uncertainty_expansion
    quality_delta_era:                     float = 0.0           # replacement_era - original_era

    bullpen_workload_status: str        = "UNKNOWN"
    late_news_trigger:       str | None = None
    replacement_architecture: str       = ReplacementArchitecture.UNKNOWN
    final_research_label:    str        = ResearchLabel.UNRESOLVED

    # ── Change side ───────────────────────────────────────────────────────────
    scratched_team: str | None = None   # "home" or "away"

    # ── Detailed breakdown ────────────────────────────────────────────────────
    uncertainty_components: dict[str, float] = field(default_factory=dict)
    notes:                  list[str]        = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_execute":                         self.can_execute,
            "patch_id":                            self.patch_id,
            "patch_engine":                        self.patch_engine,
            "classification":                      self.classification,
            "should_hold":                         self.should_hold,
            "scratched_team":                      self.scratched_team,
            "replacement_architecture":            self.replacement_architecture,
            # Four canonical outputs (no double-counting)
            "probability_adjustment":              round(self.probability_adjustment, 4),
            "uncertainty_expansion":               round(self.uncertainty_expansion, 4),
            # Auditable synonym fields for GPT/postmortem review
            "original_pitching_plan_expectation":  (
                round(self.original_pitching_plan_expectation, 2)
                if self.original_pitching_plan_expectation is not None else None
            ),
            "replacement_pitching_plan_expectation": (
                round(self.replacement_pitching_plan_expectation, 2)
                if self.replacement_pitching_plan_expectation is not None else None
            ),
            "point_estimate_delta":                round(self.point_estimate_delta, 4),
            "uncertainty_calibration_delta":       round(self.uncertainty_calibration_delta, 4),
            "quality_delta_era":                   round(self.quality_delta_era, 3),
            "bullpen_workload_status":             self.bullpen_workload_status,
            "late_news_trigger":                   self.late_news_trigger,
            "final_research_label":                self.final_research_label,
            "uncertainty_components":              {
                k: round(v, 4) for k, v in self.uncertainty_components.items()
            },
            "notes":                               self.notes,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_era(value: Any, default: float | None = None) -> float | None:
    """Parse an ERA-like numeric value from enrichment."""
    if value is None:
        return default
    try:
        v = float(value)
        # Sanity bounds: ERAs below 0.5 or above 12 are data errors
        if 0.5 <= v <= 12.0:
            return v
    except (TypeError, ValueError):
        pass
    return default


def _evaluate_replacement_plan_era(enrichment: dict[str, Any]) -> float | None:
    """
    Derive the aggregate ERA of the replacement pitching plan.

    Priority order:
      1. Explicit sp_replacement_plan_era field
      2. Weighted average of sp_replacement_arms list (innings-weighted)
      3. sp_bullpen_aggregate_era (if BULLPEN_GAME architecture)
      4. None (missing → caller handles as UNRESOLVED)
    """
    explicit = _safe_era(enrichment.get("sp_replacement_plan_era"))
    if explicit is not None:
        return explicit

    arms = enrichment.get("sp_replacement_arms")
    if isinstance(arms, list) and arms:
        total_ip = 0.0
        weighted_era = 0.0
        for arm in arms:
            if not isinstance(arm, dict):
                continue
            ip  = _safe_era(arm.get("expected_innings"), 0.0)
            era = _safe_era(arm.get("era"))
            if ip and era:
                weighted_ip  = float(ip)
                total_ip    += weighted_ip
                weighted_era += era * weighted_ip
        if total_ip > 0.0:
            return round(weighted_era / total_ip, 2)

    # BULLPEN_GAME fallback
    arch = (enrichment.get("sp_replacement_architecture") or "").upper()
    if arch == "BULLPEN_GAME":
        return _safe_era(enrichment.get("sp_bullpen_aggregate_era"))

    return None


def _classify_from_era_delta(era_delta: float) -> str:
    """
    Classify the starter change from the ERA delta.
    era_delta = replacement_era − original_era  (positive = worse replacement)
    """
    if era_delta >= _DOWNGRADE_MODERATE_THRESHOLD:
        return StarterChangeClassification.KNOWN_DOWNGRADE
    if era_delta <= _UPGRADE_THRESHOLD:
        return StarterChangeClassification.KNOWN_UPGRADE
    return StarterChangeClassification.ROUGHLY_NEUTRAL


def _compute_uncertainty_expansion(
    architecture:      str,
    workload_status:   str,
    leverage_arms:     bool | None,
) -> tuple[float, dict[str, float]]:
    """
    Compute the uncertainty expansion attributable to the replacement plan.

    This is SEPARATE from the quality-delta probability adjustment and is
    NOT double-counted with it.  It widens the probability distribution and
    lowers the calibrated lower bound.

    Returns (total_expansion, component_breakdown).
    """
    comps: dict[str, float] = {}

    arch_unc = _UNCERTAINTY_ARCHITECTURE.get(
        architecture.upper() if architecture else "UNKNOWN",
        _UNCERTAINTY_ARCHITECTURE["UNKNOWN"],
    )
    comps["architecture"] = arch_unc

    wl_unc = _UNCERTAINTY_WORKLOAD.get(
        workload_status.upper() if workload_status else "UNKNOWN",
        _UNCERTAINTY_WORKLOAD["UNKNOWN"],
    )
    comps["workload"] = wl_unc

    lev_unc = 0.0
    if leverage_arms is False:
        # No fresh high-leverage arms → cannot suppress late-inning variance
        lev_unc = _UNCERTAINTY_NO_LEVERAGE_ARMS
    comps["no_leverage_arms"] = lev_unc

    total = min(_MAX_UNCERTAINTY_EXPANSION, arch_unc + wl_unc + lev_unc)
    comps["total_capped"] = total
    return total, comps


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_mlb_starter_change(
    row:        dict[str, Any],
    enrichment: dict[str, Any],
) -> StarterChangePlan:
    """
    Analyse a potential MLB starter-change event and return a StarterChangePlan.

    This function is called at pipeline stage 5.5 (between failure-path integration
    and candidate-side extraction).  It only fires when sport == "MLB".

    The function reads ONLY non-market enrichment fields:
      sp_change_detected, sp_change_side, sp_original_era,
      sp_replacement_plan_era, sp_replacement_architecture,
      sp_bullpen_workload, sp_late_trigger, sp_leverage_arms_available,
      sp_replacement_arms, sp_bullpen_aggregate_era.

    Market odds (no_vig, implied_prob, sportsbook_odds etc.) are NEVER read
    here — they enter the pipeline only at stage 8 (calibration).

    Parameters
    ----------
    row        : Candidate row (sport, team, home_away, etc.)
    enrichment : Non-market enrichment dict (odds already stripped).

    Returns
    -------
    StarterChangePlan with:
      - probability_adjustment  — additive shift to independent_prob_post_sim
      - uncertainty_expansion   — additive to calibration uncertainty
      - should_hold             — True when UNRESOLVED_REPLACEMENT
      - Full audit trail
    """
    # ── Gate: only MLB ───────────────────────────────────────────────────────
    sport = (row.get("sport") or "").upper().strip()
    if sport != "MLB":
        return StarterChangePlan(
            classification=StarterChangeClassification.NO_CHANGE_DETECTED,
            final_research_label=ResearchLabel.PROCEED,
            notes=["MLB_STARTER_CHANGE:sport_not_MLB:skipped"],
        )

    # ── Gate: change detected? ────────────────────────────────────────────────
    change_detected = enrichment.get("sp_change_detected")
    if not change_detected:
        return StarterChangePlan(
            classification=StarterChangeClassification.NO_CHANGE_DETECTED,
            final_research_label=ResearchLabel.PROCEED,
            notes=["MLB_STARTER_CHANGE:sp_change_detected=False_or_absent:no_adjustment"],
        )

    notes: list[str] = [f"MLB_STARTER_CHANGE:{PATCH_ID}:active"]

    # ── Read enrichment fields ────────────────────────────────────────────────
    scratched_team    = str(enrichment.get("sp_change_side") or "").lower() or None
    late_trigger      = enrichment.get("sp_late_trigger") or None
    architecture_raw  = (enrichment.get("sp_replacement_architecture") or "UNKNOWN").upper()
    workload_raw      = (enrichment.get("sp_bullpen_workload") or "UNKNOWN").upper()
    leverage_arms     = enrichment.get("sp_leverage_arms_available")   # bool or None

    # Normalise architecture to known set
    architecture = architecture_raw if architecture_raw in (
        ReplacementArchitecture.SINGLE_REPLACEMENT,
        ReplacementArchitecture.OPENER_BULK,
        ReplacementArchitecture.BULLPEN_GAME,
    ) else ReplacementArchitecture.UNKNOWN

    notes.append(f"scratched_team={scratched_team!r} architecture={architecture!r} "
                 f"workload={workload_raw!r} late_trigger={late_trigger!r}")

    # ── Derive ERA values ─────────────────────────────────────────────────────
    original_era     = _safe_era(enrichment.get("sp_original_era"), _MLB_AVG_STARTER_ERA)
    replacement_era  = _evaluate_replacement_plan_era(enrichment)

    # ── Unresolved check — fail-closed ────────────────────────────────────────
    if replacement_era is None:
        notes.append(
            "UNRESOLVED_REPLACEMENT_PLAN:sp_replacement_plan_era absent "
            "and no usable replacement_arms or bullpen_aggregate_era; "
            "fail-closed to HOLD"
        )
        return StarterChangePlan(
            classification=StarterChangeClassification.UNRESOLVED_REPLACEMENT,
            should_hold=True,
            probability_adjustment=0.0,
            uncertainty_expansion=0.0,
            original_pitching_plan_expectation=original_era,
            replacement_pitching_plan_expectation=None,
            point_estimate_delta=0.0,
            uncertainty_calibration_delta=0.0,
            quality_delta_era=0.0,
            bullpen_workload_status=workload_raw,
            late_news_trigger=late_trigger,
            replacement_architecture=architecture,
            final_research_label=ResearchLabel.UNRESOLVED,
            scratched_team=scratched_team,
            notes=notes,
        )

    # ── Quality delta (ERA-based, not a fixed penalty) ────────────────────────
    era_delta = replacement_era - original_era        # positive → replacement is worse
    classification = _classify_from_era_delta(era_delta)

    notes.append(
        f"original_era={original_era:.2f} replacement_era={replacement_era:.2f} "
        f"era_delta={era_delta:+.2f} classification={classification}"
    )

    # Convert ERA delta to probability shift.
    # The shift is expressed in HOME-TEAM perspective (same convention as stages 3–5).
    #   - If the HOME team's starter was scratched:  home win-prob moves by +(-era_delta*slope)
    #     (worse home pitcher → lower home win prob → negative adjustment)
    #   - If the AWAY team's starter was scratched:  INVERT — worse away pitcher → home wins more
    #     (positive adjustment to home win prob)
    raw_shift = -era_delta * _ERA_TO_PROB_SLOPE      # negative when replacement is worse
    raw_shift = max(-_MAX_PROB_ADJUSTMENT, min(_MAX_PROB_ADJUSTMENT, raw_shift))

    if scratched_team == "away":
        # Away team pitcher is worse → HOME team benefits → invert the sign
        prob_adj_home_perspective = -raw_shift
        notes.append(
            f"away_scratch:home_perspective_adjustment={prob_adj_home_perspective:+.4f} "
            f"(inverted: worse away SP → home advantage)"
        )
    else:
        # Home team pitcher is worse (or side unknown → home perspective unchanged)
        prob_adj_home_perspective = raw_shift
        notes.append(
            f"home_scratch:home_perspective_adjustment={prob_adj_home_perspective:+.4f}"
        )

    notes.append(
        f"point_estimate_delta={prob_adj_home_perspective:+.4f} "
        f"(from quality delta only — NOT a fixed scratch penalty)"
    )

    # ── Uncertainty expansion (separate effect, not double-counted) ───────────
    unc_expansion, unc_comps = _compute_uncertainty_expansion(
        architecture=architecture,
        workload_status=workload_raw,
        leverage_arms=(bool(leverage_arms) if leverage_arms is not None else None),
    )

    notes.append(
        f"uncertainty_expansion={unc_expansion:.4f} "
        f"(distribution_widening_only; NOT added to point_estimate)"
    )
    notes.append(
        "no_double_counting: probability_adjustment and uncertainty_expansion "
        "are independent effects applied at separate pipeline stages"
    )

    # OPENER_BULK and BULLPEN_GAME are legitimate architectures — annotate explicitly
    if architecture in (ReplacementArchitecture.OPENER_BULK,
                        ReplacementArchitecture.BULLPEN_GAME):
        notes.append(
            f"architecture={architecture!r}:treated_as_legitimate_pitching_plan; "
            "not_flagged_as_degraded_state"
        )

    final_label = ResearchLabel.HOLD if classification == StarterChangeClassification.KNOWN_DOWNGRADE \
        and era_delta >= _DOWNGRADE_STRONG_THRESHOLD else ResearchLabel.PROCEED

    return StarterChangePlan(
        classification=classification,
        should_hold=False,   # only UNRESOLVED triggers should_hold
        probability_adjustment=round(prob_adj_home_perspective, 4),
        uncertainty_expansion=round(unc_expansion, 4),
        original_pitching_plan_expectation=original_era,
        replacement_pitching_plan_expectation=replacement_era,
        point_estimate_delta=round(prob_adj_home_perspective, 4),
        uncertainty_calibration_delta=round(unc_expansion, 4),
        quality_delta_era=round(era_delta, 3),
        bullpen_workload_status=workload_raw,
        late_news_trigger=late_trigger,
        replacement_architecture=architecture,
        final_research_label=final_label,
        scratched_team=scratched_team,
        uncertainty_components=unc_comps,
        notes=notes,
    )
