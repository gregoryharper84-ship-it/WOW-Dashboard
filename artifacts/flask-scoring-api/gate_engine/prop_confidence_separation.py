"""
prop_confidence_separation.py
WOW-PATCH-2026-07-15-PROP-CONFIDENCE-AND-MARKET-LABEL-SEPARATION

Separates three decisions that the engine previously mixed:
  1. Prop hit-confidence (HIT_CONFIDENCE)
  2. Market/line verification (MARKET_EDGE)
  3. Slip EV and money qualification (SLIP_EV / FULL_APPROVAL)

Design constraints:
  - Pure Python — no Flask, no app.py, no global state.
  - No new terminal labels added to the six LLP labels.
  - final_label remains one of the six existing LLP values.
  - confidence_decision / market_decision / money_decision / slip_decision
    are SEPARATE output fields — never collapsed into one label.
  - FINAL_CONFIDENCE_HIGH never aliases FINAL_APPROVED.
  - Missing payout blocks MONEY_QUALIFIED/FINAL_APPROVED only; it must NOT
    block individual hit-probability estimates or prop-pool ranking.
  - Remote governance failure with valid local governance permits confidence
    grading; it blocks money_qualified and final_approved only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ===========================================================================
# 1. Analysis Mode
# ===========================================================================

class AnalysisMode:
    HIT_CONFIDENCE = "HIT_CONFIDENCE"
    MARKET_EDGE    = "MARKET_EDGE"
    SLIP_EV        = "SLIP_EV"
    FULL_APPROVAL  = "FULL_APPROVAL"

_MODES_REQUIRING_PAYOUT = {
    AnalysisMode.SLIP_EV,
    AnalysisMode.FULL_APPROVAL,
}

_ALL_MODES = {
    AnalysisMode.HIT_CONFIDENCE,
    AnalysisMode.MARKET_EDGE,
    AnalysisMode.SLIP_EV,
    AnalysisMode.FULL_APPROVAL,
}


def resolve_analysis_mode(requested: str | None) -> str:
    """
    Return the canonical analysis mode string.
    Defaults to HIT_CONFIDENCE when unspecified.
    """
    if requested and requested.upper() in _ALL_MODES:
        return requested.upper()
    return AnalysisMode.HIT_CONFIDENCE


# ===========================================================================
# 2. Market Evidence Labels
# ===========================================================================

class MarketEvidenceLabel:
    MARKET_UNVERIFIED_HOLD   = "MARKET_UNVERIFIED_HOLD"
    ONE_SIDED_MARKET_SUPPORT = "ONE_SIDED_MARKET_SUPPORT"
    MARKET_CORROBORATED_HOLD = "MARKET_CORROBORATED_HOLD"
    MARKET_VERIFIED_HOLD     = "MARKET_VERIFIED_HOLD"

# Ordinal rank — higher is better evidence quality
_MARKET_LABEL_RANK: dict[str, int] = {
    MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD:   0,
    MarketEvidenceLabel.ONE_SIDED_MARKET_SUPPORT: 1,
    MarketEvidenceLabel.MARKET_CORROBORATED_HOLD: 2,
    MarketEvidenceLabel.MARKET_VERIFIED_HOLD:     3,
}


def lower_market_label(current: str, candidate: str) -> str:
    """Return whichever market label is ranked lower."""
    return (
        current
        if _MARKET_LABEL_RANK.get(current, 0) <= _MARKET_LABEL_RANK.get(candidate, 0)
        else candidate
    )


# ===========================================================================
# 3. Confidence Labels
# ===========================================================================

class ConfidenceLabel:
    FINAL_CONFIDENCE_HIGH    = "FINAL_CONFIDENCE_HIGH"
    FINAL_CONFIDENCE_MEDIUM  = "FINAL_CONFIDENCE_MEDIUM"
    FINAL_CONFIDENCE_LOW     = "FINAL_CONFIDENCE_LOW"
    CONFIDENCE_UNOBTAINABLE  = "CONFIDENCE_UNOBTAINABLE"

# Default thresholds (lower bound on conservative probability)
# A verified active patch can override these — must supply patch_id + rule_key.
_DEFAULT_HIGH_LB   = 0.60
_DEFAULT_MEDIUM_LB = 0.55


# ===========================================================================
# 4. Payout Scope Enforcement
# ===========================================================================

class PayoutStatus:
    NOT_REQUIRED_FOR_HIT_CONFIDENCE = "NOT_REQUIRED_FOR_HIT_CONFIDENCE"
    REQUIRED_AND_AVAILABLE          = "REQUIRED_AND_AVAILABLE"
    REQUIRED_AND_MISSING            = "REQUIRED_AND_MISSING"
    NOT_EVALUATED                   = "NOT_EVALUATED"


def enforce_payout_scope(
    analysis_mode: str,
    payout_available: bool,
) -> dict[str, Any]:
    """
    Determine whether PrizePicks payout is required and whether it blocks.

    Rules:
      - HIT_CONFIDENCE / MARKET_EDGE: payout NOT required → never blocking.
      - SLIP_EV / FULL_APPROVAL: payout required; missing → blocks money/slip.

    Returns:
        {
          payout_status:    PayoutStatus constant
          payout_blocking:  bool
          ev_status:        "NOT_EVALUATED" | "CAN_EVALUATE" | "BLOCKED_MISSING_PAYOUT"
          money_qualified:  bool  — False when payout missing and required
        }
    """
    mode = (analysis_mode or AnalysisMode.HIT_CONFIDENCE).upper()

    if mode not in _MODES_REQUIRING_PAYOUT:
        return {
            "payout_status":   PayoutStatus.NOT_REQUIRED_FOR_HIT_CONFIDENCE,
            "payout_blocking": False,
            "ev_status":       "NOT_EVALUATED",
            "money_qualified": False,
        }

    if payout_available:
        return {
            "payout_status":   PayoutStatus.REQUIRED_AND_AVAILABLE,
            "payout_blocking": False,
            "ev_status":       "CAN_EVALUATE",
            "money_qualified": True,   # payout gate cleared; other gates may still block
        }

    return {
        "payout_status":   PayoutStatus.REQUIRED_AND_MISSING,
        "payout_blocking": True,
        "ev_status":       "BLOCKED_MISSING_PAYOUT",
        "money_qualified": False,
    }


# ===========================================================================
# 5. Governance Degradation
# ===========================================================================

class GovernanceStatus:
    FULL_ATTESTATION              = "GOVERNANCE_FULL_ATTESTATION"
    ATTESTATION_DEGRADED          = "GOVERNANCE_ATTESTATION_DEGRADED"
    LOCAL_INVALID                 = "GOVERNANCE_LOCAL_INVALID"
    UNKNOWN                       = "GOVERNANCE_UNKNOWN"


def assess_governance_state(
    local_master_loaded:      bool,
    local_patch_registry_loaded: bool,
    local_schema_valid:       bool,
    remote_governance_status: str | None = None,  # "OK" | "UNAVAILABLE" | None
    degraded_reason:          str | None = None,
) -> dict[str, Any]:
    """
    Separate local governance validity from remote attestation.

    Local validity is required for research and confidence grading.
    Remote attestation is required only for money_qualified / final_approved.

    Any governance rule that lowers a ceiling must supply:
      patch_id, patch_version, rule_key, governance_source.
    If those fields are absent → emit UNVERIFIED_GOVERNANCE_RULE_IGNORED.

    Returns:
        {
          local_master_loaded:           bool
          local_patch_registry_loaded:   bool
          local_schema_valid:            bool
          remote_governance_status:      str
          governance_status:             GovernanceStatus constant
          governance_degradation_reason: str | None
          research_allowed:              bool
          confidence_grading_allowed:    bool
          money_qualified:               bool
          final_approved:                bool
        }
    """
    local_valid = (
        local_master_loaded
        and local_patch_registry_loaded
        and local_schema_valid
    )

    remote_ok     = (remote_governance_status or "").upper() == "OK"
    remote_unavail = (remote_governance_status or "").upper() == "UNAVAILABLE"

    if not local_valid:
        return {
            "local_master_loaded":           local_master_loaded,
            "local_patch_registry_loaded":   local_patch_registry_loaded,
            "local_schema_valid":            local_schema_valid,
            "remote_governance_status":      remote_governance_status or "NOT_CHECKED",
            "governance_status":             GovernanceStatus.LOCAL_INVALID,
            "governance_degradation_reason": degraded_reason or "local_governance_invalid",
            "research_allowed":              False,
            "confidence_grading_allowed":    False,
            "money_qualified":               False,
            "final_approved":                False,
        }

    if remote_ok:
        return {
            "local_master_loaded":           True,
            "local_patch_registry_loaded":   True,
            "local_schema_valid":            True,
            "remote_governance_status":      "OK",
            "governance_status":             GovernanceStatus.FULL_ATTESTATION,
            "governance_degradation_reason": None,
            "research_allowed":              True,
            "confidence_grading_allowed":    True,
            "money_qualified":               True,   # governance gate cleared; others may still block
            "final_approved":                False,  # requires all gates
        }

    # Local valid but remote unavailable → DEGRADED
    reason = degraded_reason or "remote_governance_endpoint_unavailable"
    return {
        "local_master_loaded":           True,
        "local_patch_registry_loaded":   True,
        "local_schema_valid":            True,
        "remote_governance_status":      remote_governance_status or "UNAVAILABLE",
        "governance_status":             GovernanceStatus.ATTESTATION_DEGRADED,
        "governance_degradation_reason": reason,
        "research_allowed":              True,
        "confidence_grading_allowed":    True,
        "money_qualified":               False,
        "final_approved":                False,
    }


def validate_governance_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """
    Validate that a ceiling-lowering governance rule supplies all required fields.

    Required: patch_id, patch_version, rule_key, governance_source.

    Returns:
        {
          valid:    bool
          verdict:  "RULE_VERIFIED" | "UNVERIFIED_GOVERNANCE_RULE_IGNORED"
          missing:  list[str]
        }
    """
    required = ["patch_id", "patch_version", "rule_key", "governance_source"]
    missing  = [f for f in required if not rule.get(f)]
    if missing:
        return {
            "valid":   False,
            "verdict": "UNVERIFIED_GOVERNANCE_RULE_IGNORED",
            "missing": missing,
        }
    return {
        "valid":   True,
        "verdict": "RULE_VERIFIED",
        "missing": [],
    }


# ===========================================================================
# 6. No-Vig Computation (two-sided strict)
# ===========================================================================

def compute_no_vig_two_sided(
    over_american:  float | None,
    under_american: float | None,
) -> dict[str, Any]:
    """
    Compute no-vig probability from two-sided American odds.

    Formula:
        raw_prob_negative = abs(odds) / (abs(odds) + 100)
        raw_prob_positive = 100 / (odds + 100)
        no_vig_over = raw_over / (raw_over + raw_under)

    Rules:
      - BOTH sides required; if either is None → no_vig_available=False.
      - Never call a one-sided implied probability "no-vig."
      - market_support_direction reported even when no-vig is unavailable.

    Returns:
        {
          no_vig_available:          bool
          no_vig_over:               float | None
          no_vig_under:              float | None
          raw_over:                  float | None
          raw_under:                 float | None
          overround:                 float | None
          market_support_direction:  "OVER" | "UNDER" | "NONE"
          rejection_reason:          str | None
        }
    """
    def _implied(american: float) -> float:
        if american < 0:
            return abs(american) / (abs(american) + 100.0)
        return 100.0 / (american + 100.0)

    if over_american is None and under_american is None:
        return {
            "no_vig_available":         False,
            "no_vig_over":              None,
            "no_vig_under":             None,
            "raw_over":                 None,
            "raw_under":                None,
            "overround":                None,
            "market_support_direction": "NONE",
            "rejection_reason":         "both_sides_missing",
        }

    if over_american is None:
        raw_under = _implied(under_american)
        direction = "UNDER" if raw_under > 0.5 else "NONE"
        return {
            "no_vig_available":         False,
            "no_vig_over":              None,
            "no_vig_under":             None,
            "raw_over":                 None,
            "raw_under":                round(raw_under, 6),
            "overround":                None,
            "market_support_direction": direction,
            "rejection_reason":         "over_side_missing:no_vig_requires_both_sides",
        }

    if under_american is None:
        raw_over = _implied(over_american)
        direction = "OVER" if raw_over > 0.5 else "NONE"
        return {
            "no_vig_available":         False,
            "no_vig_over":              None,
            "no_vig_under":             None,
            "raw_over":                 round(raw_over, 6),
            "raw_under":                None,
            "overround":                None,
            "market_support_direction": direction,
            "rejection_reason":         "under_side_missing:no_vig_requires_both_sides",
        }

    try:
        raw_over  = _implied(over_american)
        raw_under = _implied(under_american)
        overround = raw_over + raw_under
        nv_over   = raw_over  / overround
        nv_under  = raw_under / overround
        direction = "OVER" if nv_over > 0.5 else ("UNDER" if nv_under > 0.5 else "NONE")
        return {
            "no_vig_available":         True,
            "no_vig_over":              round(nv_over, 6),
            "no_vig_under":             round(nv_under, 6),
            "raw_over":                 round(raw_over, 6),
            "raw_under":                round(raw_under, 6),
            "overround":                round(overround, 6),
            "market_support_direction": direction,
            "rejection_reason":         None,
        }
    except Exception as exc:
        return {
            "no_vig_available":         False,
            "no_vig_over":              None,
            "no_vig_under":             None,
            "raw_over":                 None,
            "raw_under":                None,
            "overround":                None,
            "market_support_direction": "NONE",
            "rejection_reason":         f"math_error:{exc}",
        }


# ===========================================================================
# 7. Market Evidence Classification
# ===========================================================================

class EvidenceType:
    EXACT_LINE    = "EXACT_LINE"
    ADJACENT_LINE = "ADJACENT_LINE"
    NO_EVIDENCE   = "NO_EVIDENCE"
    ONE_SIDED     = "ONE_SIDED"


def classify_market_evidence(
    pp_line:         float | None,
    sportsbook_line: float | None,
    over_american:   float | None,
    under_american:  float | None,
) -> dict[str, Any]:
    """
    Classify market evidence and assign a market evidence label.

    Rules:
      - No sportsbook at all           → MARKET_UNVERIFIED_HOLD
      - Sportsbook has only one side   → ONE_SIDED_MARKET_SUPPORT (no no-vig)
      - Adjacent line (different from PP) with both sides
                                       → MARKET_CORROBORATED_HOLD
      - Exact same line with both sides → MARKET_VERIFIED_HOLD
      - Adjacent line → do NOT reuse sportsbook probability as PP probability.

    Returns:
        {
          market_label:    MarketEvidenceLabel constant
          evidence_type:   EvidenceType constant
          pp_line:         float | None
          sportsbook_line: float | None
          line_delta:      float | None
          no_vig_result:   dict (from compute_no_vig_two_sided)
          max_label:       MarketEvidenceLabel constant
          notes:           list[str]
        }
    """
    notes: list[str] = []

    if sportsbook_line is None:
        nv = compute_no_vig_two_sided(None, None)
        return {
            "market_label":    MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD,
            "evidence_type":   EvidenceType.NO_EVIDENCE,
            "pp_line":         pp_line,
            "sportsbook_line": None,
            "line_delta":      None,
            "no_vig_result":   nv,
            "max_label":       MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD,
            "notes":           ["no_sportsbook_line_available"],
        }

    line_delta   = round(abs((pp_line or 0.0) - sportsbook_line), 4) if pp_line is not None else None
    is_exact     = line_delta is not None and line_delta < 0.001
    nv           = compute_no_vig_two_sided(over_american, under_american)
    has_both     = over_american is not None and under_american is not None
    has_one_side = (over_american is not None) != (under_american is not None)

    if not has_both and not has_one_side:
        # sportsbook_line present but no price info
        notes.append("sportsbook_line_present_but_no_price")
        return {
            "market_label":    MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD,
            "evidence_type":   EvidenceType.NO_EVIDENCE,
            "pp_line":         pp_line,
            "sportsbook_line": sportsbook_line,
            "line_delta":      line_delta,
            "no_vig_result":   nv,
            "max_label":       MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD,
            "notes":           notes,
        }

    if has_one_side:
        notes.append("one_sided_price_only:no_vig_forbidden")
        return {
            "market_label":    MarketEvidenceLabel.ONE_SIDED_MARKET_SUPPORT,
            "evidence_type":   EvidenceType.ONE_SIDED,
            "pp_line":         pp_line,
            "sportsbook_line": sportsbook_line,
            "line_delta":      line_delta,
            "no_vig_result":   nv,
            "max_label":       MarketEvidenceLabel.ONE_SIDED_MARKET_SUPPORT,
            "notes":           notes,
        }

    # Both sides present
    if is_exact:
        label = MarketEvidenceLabel.MARKET_VERIFIED_HOLD
        ev_type = EvidenceType.EXACT_LINE
        notes.append("exact_line_two_sided_verified")
    else:
        label = MarketEvidenceLabel.MARKET_CORROBORATED_HOLD
        ev_type = EvidenceType.ADJACENT_LINE
        notes.append(
            f"adjacent_line:pp={pp_line} sb={sportsbook_line} "
            f"delta={line_delta}:do_not_reuse_sb_probability"
        )

    return {
        "market_label":    label,
        "evidence_type":   ev_type,
        "pp_line":         pp_line,
        "sportsbook_line": sportsbook_line,
        "line_delta":      line_delta,
        "no_vig_result":   nv,
        "max_label":       label,
        "notes":           notes,
    }


def classify_adjacent_line(
    pp_line:        float,
    sb_line:        float,
    sb_over_price:  float | None,
    sb_under_price: float | None,
) -> dict[str, Any]:
    """
    Full adjacent-line analysis with interpolation uncertainty required fields.

    Returns mandatory fields when evidence_type=ADJACENT_LINE:
        pp_line, sportsbook_line, line_delta,
        sportsbook_over_price, sportsbook_under_price,
        evidence_type, interpolation_method, interpolation_uncertainty
    """
    line_delta = round(abs(pp_line - sb_line), 4)
    is_exact   = line_delta < 0.001

    ev_type = EvidenceType.EXACT_LINE if is_exact else EvidenceType.ADJACENT_LINE

    return {
        "pp_line":              pp_line,
        "sportsbook_line":      sb_line,
        "line_delta":           line_delta,
        "sportsbook_over_price":  sb_over_price,
        "sportsbook_under_price": sb_under_price,
        "evidence_type":        ev_type,
        "max_market_label":     (
            MarketEvidenceLabel.MARKET_VERIFIED_HOLD
            if is_exact
            else MarketEvidenceLabel.MARKET_CORROBORATED_HOLD
        ),
        "interpolation_method":      None if is_exact else "distribution_model_required",
        "interpolation_uncertainty": None if is_exact else "UNQUANTIFIED",
        "note": (
            None if is_exact
            else "do_not_reuse_sportsbook_probability_as_pp_exact_line_probability"
        ),
    }


# ===========================================================================
# 8. Confidence Grader
# ===========================================================================

@dataclass
class ConfidenceInputs:
    """Structured inputs for confidence grading."""
    conservative_lower_bound: float | None = None
    exact_line_verified:      bool = False
    role_status_verified:     bool = False
    projection_reproducible:  bool = False
    no_material_conflict:     bool = True
    analysis_mode:            str  = AnalysisMode.HIT_CONFIDENCE
    # Optional patch override for thresholds
    threshold_override:       dict[str, Any] | None = None


def grade_confidence(inputs: ConfidenceInputs) -> dict[str, Any]:
    """
    Grade hit-confidence from probability inputs.

    Default thresholds:
      HIGH:   lower_bound >= 0.60, exact line verified, role verified,
              projection reproducible, no material conflict
      MEDIUM: lower_bound >= 0.55, sufficient evidence with ≤1 non-critical uncertainty
      LOW:    lower_bound < 0.55 or edge near friction

    A patch may override HIGH/MEDIUM lower bounds only when it supplies
    patch_id, patch_version, rule_key, governance_source.

    Returns:
        {
          confidence_label:   ConfidenceLabel constant
          lower_bound_used:   float | None
          high_threshold:     float
          medium_threshold:   float
          threshold_source:   "default" | "patch_override"
          threshold_patch_id: str | None
          reasons:            list[str]
        }
    """
    reasons: list[str] = []
    threshold_source = "default"
    threshold_patch_id: str | None = None

    high_lb   = _DEFAULT_HIGH_LB
    medium_lb = _DEFAULT_MEDIUM_LB

    override = inputs.threshold_override
    if override:
        v_result = validate_governance_rule(override)
        if v_result["valid"]:
            high_lb            = override.get("high_lower_bound", high_lb)
            medium_lb          = override.get("medium_lower_bound", medium_lb)
            threshold_source   = "patch_override"
            threshold_patch_id = override.get("patch_id")
        else:
            reasons.append("UNVERIFIED_GOVERNANCE_RULE_IGNORED:"
                           f"missing={v_result['missing']}")

    lb = inputs.conservative_lower_bound

    # CONFIDENCE_UNOBTAINABLE conditions
    if lb is None:
        reasons.append("missing_conservative_lower_bound")
        return _conf_result(ConfidenceLabel.CONFIDENCE_UNOBTAINABLE, lb,
                            high_lb, medium_lb, threshold_source, threshold_patch_id, reasons)

    if not inputs.exact_line_verified:
        reasons.append("exact_line_not_verified")
    if not inputs.role_status_verified:
        reasons.append("role_status_not_verified")
    if not inputs.projection_reproducible:
        reasons.append("projection_not_reproducible")

    unobtainable_reasons = [r for r in reasons if r in (
        "exact_line_not_verified", "role_status_not_verified", "projection_not_reproducible"
    )]
    if unobtainable_reasons:
        return _conf_result(ConfidenceLabel.CONFIDENCE_UNOBTAINABLE, lb,
                            high_lb, medium_lb, threshold_source, threshold_patch_id, reasons)

    if lb >= high_lb and inputs.no_material_conflict:
        return _conf_result(ConfidenceLabel.FINAL_CONFIDENCE_HIGH, lb,
                            high_lb, medium_lb, threshold_source, threshold_patch_id, reasons)

    if lb >= medium_lb:
        if not inputs.no_material_conflict:
            reasons.append("material_conflict_present")
        return _conf_result(ConfidenceLabel.FINAL_CONFIDENCE_MEDIUM, lb,
                            high_lb, medium_lb, threshold_source, threshold_patch_id, reasons)

    reasons.append(f"lower_bound_{lb:.3f}<medium_threshold_{medium_lb}")
    return _conf_result(ConfidenceLabel.FINAL_CONFIDENCE_LOW, lb,
                        high_lb, medium_lb, threshold_source, threshold_patch_id, reasons)


def _conf_result(
    label: str,
    lb: float | None,
    high_lb: float,
    medium_lb: float,
    threshold_source: str,
    threshold_patch_id: str | None,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "confidence_label":   label,
        "lower_bound_used":   lb,
        "high_threshold":     high_lb,
        "medium_threshold":   medium_lb,
        "threshold_source":   threshold_source,
        "threshold_patch_id": threshold_patch_id,
        "reasons":            reasons,
    }


# ===========================================================================
# 9. Probability Audit
# ===========================================================================

_REQUIRED_AUDIT_FIELDS = [
    "season_mean", "l10_raw_values", "l10_mean", "l10_median",
    "l10_exact_line_hit_rate", "l5_raw_values", "l5_exact_line_hit_rate",
    "role_split_sample", "projected_minutes", "projected_stat_mean",
    "opponent_context_adjustment", "scenario_weights", "distribution_method",
    "base_probability", "uncertainty_haircut", "final_probability",
    "conservative_lower_bound",
]

_OPTIONAL_AUDIT_FIELDS = [
    "l9_outlier_adjusted_mean",  # required when outlier trigger fires
]


def build_probability_audit(data: dict[str, Any]) -> dict[str, Any]:
    """
    Build a probability audit record.

    If required fields are missing, the probability is labeled PROVISIONAL.
    PROVISIONAL probabilities must never be described as calibrated.

    Returns:
        {
          complete:              bool
          provisional:           bool
          missing_fields:        list[str]
          audit_record:          dict
          calibrated:            bool  — always False when provisional
        }
    """
    missing = [f for f in _REQUIRED_AUDIT_FIELDS if data.get(f) is None]
    complete   = len(missing) == 0
    provisional = not complete

    return {
        "complete":       complete,
        "provisional":    provisional,
        "missing_fields": missing,
        "audit_record":   {f: data.get(f) for f in _REQUIRED_AUDIT_FIELDS + _OPTIONAL_AUDIT_FIELDS},
        "calibrated":     False if provisional else bool(data.get("calibrated", False)),
        "note": (
            "PROVISIONAL_PROBABILITY_RANGE:do_not_describe_as_calibrated"
            if provisional else None
        ),
    }


# ===========================================================================
# 10. Board Source / Screenshot Classification
# ===========================================================================

class BoardSourceType:
    LIVE_VERIFIED          = "LIVE_VERIFIED"
    OPERATOR_SCREENSHOT    = "OPERATOR_SUPPLIED_SCREENSHOT"
    UNKNOWN                = "UNKNOWN"


def classify_board_source(source_type: str | None) -> dict[str, Any]:
    """
    Classify PrizePicks board source and its verification requirements.

    A recent screenshot may support HIT_CONFIDENCE research but requires
    a live recheck before submission lock, FINAL_APPROVED, or slip EV.

    Returns:
        {
          board_source:                 str
          board_line_verified_for_research: bool
          board_live_verified:          bool
          requires_live_recheck_for:    list[str]
          recheck_required:             bool
        }
    """
    src = (source_type or "").upper().strip()
    is_screenshot = "SCREENSHOT" in src or src in (
        "OPERATOR_SUPPLIED_SCREENSHOT", "SCREENSHOT", "OPERATOR_SCREENSHOT"
    )

    if is_screenshot:
        return {
            "board_source":                   BoardSourceType.OPERATOR_SCREENSHOT,
            "board_line_verified_for_research": True,
            "board_live_verified":             False,
            "requires_live_recheck_for":       [
                "submission_lock", "FINAL_APPROVED", "SLIP_EV", "availability_confirmation"
            ],
            "recheck_required": True,
        }

    if src in ("LIVE", "LIVE_VERIFIED", "LIVE_BOARD"):
        return {
            "board_source":                   BoardSourceType.LIVE_VERIFIED,
            "board_line_verified_for_research": True,
            "board_live_verified":             True,
            "requires_live_recheck_for":       [],
            "recheck_required":                False,
        }

    return {
        "board_source":                   BoardSourceType.UNKNOWN,
        "board_line_verified_for_research": False,
        "board_live_verified":             False,
        "requires_live_recheck_for":       ["all"],
        "recheck_required":                True,
    }


# ===========================================================================
# 11. Same-Game Correlation Gate
# ===========================================================================

class CorrelationStatus:
    OBTAINABLE            = "CORRELATION_OBTAINABLE"
    UNOBTAINABLE          = "CORRELATION_UNOBTAINABLE"
    NARRATIVE_ONLY        = "CORRELATION_NARRATIVE_ONLY_REJECTED"


def assess_correlation(
    pair: tuple[str, str] | None,
    correlation_data: dict[str, Any] | None,
    analysis_mode: str = AnalysisMode.HIT_CONFIDENCE,
) -> dict[str, Any]:
    """
    Same-game correlation gate.

    Rules:
      - Do NOT reject or downgrade individual confidence from narrative alone.
      - Correlation failure blocks SLIP_APPROVED but not individual hit confidence.
      - Required fields for an approved correlation: event_id, estimated_correlation,
        method, independent_joint_prob, adjusted_joint_prob.
      - When analysis_mode is SLIP_EV/FULL_APPROVAL, absent correlation → blocks slip.

    Returns:
        {
          status:                      CorrelationStatus constant
          blocks_slip_approval:        bool
          blocks_individual_confidence: bool  — always False (narrative never blocks)
          narrative_correlation_rejected: bool
          required_fields_present:     bool
          missing_fields:              list[str]
          slip_breakeven_required:     bool
        }
    """
    required = [
        "event_id", "estimated_correlation", "correlation_method",
        "independent_joint_prob", "adjusted_joint_prob",
    ]

    cd = correlation_data or {}
    missing = [f for f in required if cd.get(f) is None]
    has_all = len(missing) == 0

    slip_mode = (analysis_mode or "").upper() in (AnalysisMode.SLIP_EV, AnalysisMode.FULL_APPROVAL)

    if has_all:
        return {
            "status":                        CorrelationStatus.OBTAINABLE,
            "blocks_slip_approval":          False,
            "blocks_individual_confidence":  False,
            "narrative_correlation_rejected": False,
            "required_fields_present":       True,
            "missing_fields":                [],
            "slip_breakeven_required":       slip_mode,
        }

    is_narrative_only = bool(cd.get("narrative_correlation_only"))
    return {
        "status":                        (
            CorrelationStatus.NARRATIVE_ONLY
            if is_narrative_only
            else CorrelationStatus.UNOBTAINABLE
        ),
        "blocks_slip_approval":          True,     # no correlation data → slip blocked
        "blocks_individual_confidence":  False,    # NEVER blocks individual confidence
        "narrative_correlation_rejected": is_narrative_only,
        "required_fields_present":       False,
        "missing_fields":                missing,
        "slip_breakeven_required":       slip_mode,
    }


# ===========================================================================
# 12. Terminal Output Builder
# ===========================================================================

_NOT_EVALUATED = "NOT_EVALUATED"
_NOT_REQUESTED = "NOT_REQUESTED"


def build_terminal_output(
    confidence_decision: str,
    market_decision:     str,
    money_decision:      str  = _NOT_EVALUATED,
    slip_decision:       str  = _NOT_REQUESTED,
    extra:               dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the separate four-decision terminal output.

    Invariants enforced:
      - can_execute always False (WOW governance requirement).
      - FINAL_CONFIDENCE_HIGH is never aliased to FINAL_APPROVED.
      - money_qualified is False unless money_decision == "MONEY_QUALIFIED".
      - Decisions are never collapsed into one label.

    Returns:
        {
          confidence_decision: str
          market_decision:     str
          money_decision:      str
          slip_decision:       str
          can_execute:         bool  — always False
          money_qualified:     bool
          final_approved:      bool  — always False in this module
          warnings:            list[str]
        }
    """
    warnings: list[str] = []

    # Invariant: FINAL_CONFIDENCE_HIGH must never alias FINAL_APPROVED
    if confidence_decision == "FINAL_APPROVED":
        confidence_decision = ConfidenceLabel.FINAL_CONFIDENCE_HIGH
        warnings.append(
            "confidence_decision_was_FINAL_APPROVED:corrected_to_FINAL_CONFIDENCE_HIGH"
        )

    money_qualified = money_decision == "MONEY_QUALIFIED"
    final_approved  = False  # execution decisions live outside this module

    out = {
        "confidence_decision": confidence_decision,
        "market_decision":     market_decision,
        "money_decision":      money_decision,
        "slip_decision":       slip_decision,
        "can_execute":         False,
        "money_qualified":     money_qualified,
        "final_approved":      final_approved,
        "warnings":            warnings,
    }
    if extra:
        out.update(extra)
    return out


# ===========================================================================
# 13. Full Row Analysis (entry point for the separation engine)
# ===========================================================================

def run_prop_confidence_separation(
    row:              dict[str, Any],
    analysis_mode:    str | None = None,
    payout_available: bool = False,
    market_evidence:  dict[str, Any] | None = None,
    confidence_inputs: ConfidenceInputs | None = None,
    governance_state:  dict[str, Any] | None = None,
    correlation_data:  dict[str, Any] | None = None,
    board_source_type: str | None = None,
) -> dict[str, Any]:
    """
    Top-level entry point for the prop confidence separation engine.

    Returns a fully-formed four-decision output without modifying any
    existing gate or terminal_label on the row.
    """
    mode = resolve_analysis_mode(analysis_mode)

    # Payout scope
    payout_scope = enforce_payout_scope(mode, payout_available)

    # Governance
    gov = governance_state or {
        "governance_status":          GovernanceStatus.FULL_ATTESTATION,
        "research_allowed":           True,
        "confidence_grading_allowed": True,
        "money_qualified":            False,
        "final_approved":             False,
    }
    if not gov.get("confidence_grading_allowed", True):
        confidence_label = ConfidenceLabel.CONFIDENCE_UNOBTAINABLE
    else:
        ci = confidence_inputs or ConfidenceInputs()
        ci.analysis_mode = mode
        conf_result      = grade_confidence(ci)
        confidence_label = conf_result["confidence_label"]

    # Market evidence
    me = market_evidence or {}
    market_label = me.get("market_label", MarketEvidenceLabel.MARKET_UNVERIFIED_HOLD)

    # Money / slip decisions
    payout_ok    = not payout_scope["payout_blocking"]
    gov_money_ok = gov.get("money_qualified", False)

    if mode in _MODES_REQUIRING_PAYOUT and payout_ok and gov_money_ok:
        money_decision = "MONEY_QUALIFIED"
    elif mode in _MODES_REQUIRING_PAYOUT and not payout_ok:
        money_decision = "NOT_QUALIFIED_MISSING_PAYOUT"
    elif mode in _MODES_REQUIRING_PAYOUT and not gov_money_ok:
        money_decision = "NOT_QUALIFIED_GOVERNANCE_DEGRADED"
    else:
        money_decision = _NOT_EVALUATED

    # Correlation / slip
    corr = assess_correlation(None, correlation_data, mode)
    if mode in (AnalysisMode.SLIP_EV, AnalysisMode.FULL_APPROVAL):
        slip_decision = (
            "SLIP_APPROVED_PENDING_EXECUTION"
            if money_decision == "MONEY_QUALIFIED" and not corr["blocks_slip_approval"]
            else "SLIP_NOT_APPROVED"
        )
    else:
        slip_decision = _NOT_REQUESTED

    # Board source
    board = classify_board_source(board_source_type)

    return build_terminal_output(
        confidence_decision=confidence_label,
        market_decision=market_label,
        money_decision=money_decision,
        slip_decision=slip_decision,
        extra={
            "analysis_mode":   mode,
            "payout_scope":    payout_scope,
            "governance":      gov,
            "board_source":    board,
            "correlation":     corr,
        },
    )
