"""
gate_engine/moneyline/dynamic_calibration.py
WOW v16 — Dynamic candidate-specific calibration.

Computes a per-candidate uncertainty from eight factors:
  1. Sport volatility constant
  2. Sample size (games in log)
  3. Lineup / starter certainty
  4. Source conflict (from quorum result)
  5. Model disagreement (from DisagreementAudit widening factor)
  6. Status freshness
  7. Historical calibration performance
  8. Model status (ACTIVE / PROVISIONAL)

Then applies bounded market shrinkage:
  calibrated_probability = (1 - w) * independent + w * market_no_vig
  Clamps market_weight to [0.0, 0.50].
  Above 0.50 → MARKET_DEPENDENT_MODEL flag.

Returns four clean outputs: independent, calibrated, lower_bound, upper_bound.
Market data is NEVER accessed before this function. net_edge is computed here
but returned as a downstream-only annotation.

can_execute=False unconditional.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False

# ---------------------------------------------------------------------------
# Sport volatility constants (base uncertainty before other adjustments)
# ---------------------------------------------------------------------------

_SPORT_VOLATILITY: dict[str, float] = {
    "MLB":    0.10,   # high game-to-game variance
    "NBA":    0.07,
    "WNBA":   0.09,   # smaller sample, thinner market
    "NFL":    0.11,   # high variance per game
    "NHL":    0.12,   # goalie variance
    "SOCCER": 0.13,   # draw uncertainty + tactical variance
    "EPL":    0.12,
    "MLS":    0.13,
    "ATP":    0.08,
    "WTA":    0.09,
    "TENNIS": 0.09,
    "MMA":    0.14,   # finish variance very high
    "UFC":    0.14,
}

# Minimum sample size for full confidence (below → penalty)
_MIN_SAMPLE  = 10
_MAX_PENALTY_SAMPLE = 0.06   # added to uncertainty when n=1

# Uncertainty cap: calibrated probability cannot be negative or >0.99
_UNCERTAINTY_FLOOR = 0.01
_UNCERTAINTY_CAP   = 0.30

# Market weight cap: >0.50 → MARKET_DEPENDENT_MODEL
_MARKET_WEIGHT_CAP = 0.50

# Market type trust ordering (full-game H2H most trusted)
_MARKET_TYPE_TRUST: dict[str, float] = {
    "full_game_h2h": 1.00,
    "full_game":     1.00,
    "h2h":           1.00,
    "moneyline":     1.00,
    "first_half":    0.70,
    "first_quarter": 0.50,
    "live":          0.40,
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CalibrationResult:
    """
    All four clean probability outputs plus full observability.

    independent_prob, calibrated_probability, lower_bound, upper_bound are
    the canonical outputs.  market_weight and model_weight are exposed for
    the GPT schema.  net_edge is downstream only.
    """
    independent_prob:             float
    calibrated_probability:       float
    calibrated_lower_bound:       float
    calibrated_upper_bound:       float
    dynamic_uncertainty:          float
    model_weight:                 float
    market_weight:                float
    market_no_vig_used:           float | None
    market_dependent_flag:        bool
    model_status:                 str
    net_edge:                     float | None   # downstream only
    calibration_notes:            list[str]      = field(default_factory=list)
    uncertainty_components:       dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "independent_probability":            round(self.independent_prob, 4),
            "calibrated_probability":             round(self.calibrated_probability, 4),
            "calibrated_lower_bound":             round(self.calibrated_lower_bound, 4),
            "calibrated_upper_bound":             round(self.calibrated_upper_bound, 4),
            "dynamic_uncertainty":                round(self.dynamic_uncertainty, 4),
            "model_weight":                       round(self.model_weight, 4),
            "market_weight":                      round(self.market_weight, 4),
            "market_no_vig_used":                 (round(self.market_no_vig_used, 4)
                                                   if self.market_no_vig_used is not None else None),
            "market_dependent_flag":              self.market_dependent_flag,
            "model_status":                       self.model_status,
            "net_edge":                           (round(self.net_edge, 4)
                                                   if self.net_edge is not None else None),
            "calibration_notes":                  self.calibration_notes,
            "uncertainty_components":             {k: round(v, 4)
                                                   for k, v in self.uncertainty_components.items()},
        }


# ---------------------------------------------------------------------------
# Uncertainty factor helpers
# ---------------------------------------------------------------------------

def _sample_size_penalty(n_games: int) -> float:
    """More games → less uncertainty. Penalty decays from MAX_PENALTY_SAMPLE at n=1."""
    if n_games >= _MIN_SAMPLE:
        return 0.0
    if n_games <= 0:
        return _MAX_PENALTY_SAMPLE
    return _MAX_PENALTY_SAMPLE * (1.0 - n_games / _MIN_SAMPLE)


def _lineup_certainty_reduction(enrichment: dict[str, Any]) -> float:
    """
    Confirmed lineup / confirmed starter → reduction in uncertainty.
    Unconfirmed / TBD → no reduction or small increase.
    """
    lineup_confirmed = enrichment.get("lineup_confirmed")
    starter_confirmed = enrichment.get("starter_confirmed") or enrichment.get("sp_confirmed")

    if lineup_confirmed is True and starter_confirmed is True:
        return -0.01   # subtract from uncertainty (high certainty)
    if lineup_confirmed is True or starter_confirmed is True:
        return -0.005
    if lineup_confirmed is False or starter_confirmed is False:
        return 0.02    # add to uncertainty (unconfirmed)
    return 0.0


def _source_conflict_penalty(quorum_result: dict | None) -> float:
    """SOURCE_CONFLICT in quorum → extra uncertainty."""
    if quorum_result is None:
        return 0.0
    if quorum_result.get("quorum_status") == "SOURCE_CONFLICT":
        return quorum_result.get("calibration_penalty", 0.04)
    return 0.0


def _freshness_penalty(enrichment: dict[str, Any]) -> float:
    """Staleness adds uncertainty linearly above 1 hour."""
    freshness_h = enrichment.get("status_freshness_hours")
    if freshness_h is None:
        return 0.005   # unknown freshness → small penalty
    try:
        h = float(freshness_h)
    except (TypeError, ValueError):
        return 0.005
    if h <= 1.0:
        return 0.0
    if h <= 4.0:
        return 0.01
    return 0.025   # very stale


def _historical_cal_adjustment(enrichment: dict[str, Any]) -> float:
    """
    If calibration_health grade is available, adjust uncertainty:
      GREEN   → -0.005 (confirmed reliable)
      WATCH   → +0.01
      SUPPRESS → +0.03
      DATA_GAP → 0 (no information)
    """
    grade = (enrichment.get("calibration_health_grade") or "").upper()
    return {"GREEN": -0.005, "WATCH": 0.01, "SUPPRESS": 0.03}.get(grade, 0.0)


def _model_status_penalty(model_status: str) -> float:
    """PROVISIONAL models carry an additional uncertainty floor."""
    if model_status == "PROVISIONAL":
        return 0.03
    if model_status == "UNAVAILABLE":
        return 0.10
    return 0.0


# ---------------------------------------------------------------------------
# Market weight computation
# ---------------------------------------------------------------------------

def _compute_market_weight(market_inputs: dict[str, Any]) -> tuple[float, bool, list[str]]:
    """
    Compute market weight scaled by liquidity, maturity, freshness, hold, type.
    Returns (weight, market_dependent_flag, notes).
    """
    notes: list[str] = []

    n_books = int(market_inputs.get("bookmaker_count") or 0)
    hours_open = float(market_inputs.get("hours_since_open") or 0.0)
    hold_pct   = float(market_inputs.get("hold_pct") or 0.05)
    freshness_h = float(market_inputs.get("market_freshness_hours") or 0.0)
    market_type = (market_inputs.get("market_type") or "full_game_h2h").lower().replace(" ", "_")

    if n_books == 0:
        return 0.0, False, ["NO_MARKET_DATA:market_weight=0"]

    # Liquidity: saturates at 10+ books
    liquidity_factor = min(1.0, n_books / 10.0)

    # Maturity: hours since line opened; fully mature at 24h
    maturity_factor = min(1.0, hours_open / 24.0)

    # Hold quality: tighter hold → more informative
    hold_factor = max(0.0, 1.0 - (hold_pct - 0.03) * 5.0)
    hold_factor = min(1.0, hold_factor)

    # Freshness: stale market loses weight
    if freshness_h > 2.0:
        fresh_factor = max(0.2, 1.0 - (freshness_h - 2.0) / 20.0)
    else:
        fresh_factor = 1.0

    # Market type trust
    type_trust = _MARKET_TYPE_TRUST.get(market_type, 0.60)

    # Raw market weight: uses a 0.70 scale so that a fully mature, high-liquidity,
    # tight-spread H2H market produces a weight above the 0.50 cap and correctly
    # triggers MARKET_DEPENDENT_MODEL.  For typical 2-3 book markets the raw
    # weight stays well below 0.50.
    # Example: 10 books, 24h open, 3% hold, H2H → raw_w = 0.70 (> 0.50 → flagged)
    # Example: 2 books,  4h  open, 5% hold, H2H → raw_w ≈ 0.07 (not flagged)
    _BASE_SCALE = 0.70
    base_w = _BASE_SCALE * liquidity_factor * maturity_factor * hold_factor * fresh_factor * type_trust

    market_dependent = base_w > _MARKET_WEIGHT_CAP   # flags when raw > 0.50
    clamped_w = min(base_w, _MARKET_WEIGHT_CAP)       # hard cap at 0.50

    if market_dependent:
        notes.append(
            f"MARKET_DEPENDENT_MODEL:raw_weight={base_w:.3f} clamped to {_MARKET_WEIGHT_CAP}"
        )
    notes.append(
        f"market_weight_factors: liquidity={liquidity_factor:.2f} "
        f"maturity={maturity_factor:.2f} hold={hold_factor:.2f} "
        f"fresh={fresh_factor:.2f} type_trust={type_trust:.2f}"
    )

    return clamped_w, market_dependent, notes


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def calibrate(
    independent_prob:     float,
    model_status:         str,
    sport:                str,
    enrichment:           dict[str, Any],
    quorum_result:        dict | None    = None,
    disagreement_audit:   "Any | None"  = None,
    market_no_vig:        float | None  = None,
    market_inputs:        dict | None   = None,
) -> CalibrationResult:
    """
    Compute dynamic calibration for a single moneyline candidate.

    Parameters
    ----------
    independent_prob   : Output of independent sport model (zero market input)
    model_status       : "ACTIVE" | "PROVISIONAL" | "UNAVAILABLE"
    sport              : Sport string (for volatility lookup)
    enrichment         : Full enrichment dict (for sample size, lineup, freshness)
    quorum_result      : From opportunity_acquisition quorum resolver (optional)
    disagreement_audit : DisagreementAudit from model_disagreement.py (optional)
    market_no_vig      : Two-book no-vig market probability (optional;
                         first enters here — never upstream)
    market_inputs      : Metadata for market weight computation (optional)

    Returns CalibrationResult with four clean outputs.
    """
    notes:  list[str] = []
    comps:  dict[str, float] = {}

    sport_key = sport.upper().strip()
    base_vol = _SPORT_VOLATILITY.get(sport_key, 0.10)
    comps["sport_volatility"] = base_vol

    # Sample size
    n_games = 0
    game_log = enrichment.get("game_log")
    if isinstance(game_log, list):
        n_games = len([g for g in game_log if g is not None])
    elif enrichment.get("sample_size"):
        try:
            n_games = int(enrichment["sample_size"])
        except (TypeError, ValueError):
            pass
    sp = _sample_size_penalty(n_games)
    comps["sample_size_penalty"] = sp

    # Lineup / starter certainty
    lc = _lineup_certainty_reduction(enrichment)
    comps["lineup_certainty_adj"] = lc

    # Source conflict
    sc = _source_conflict_penalty(quorum_result)
    comps["source_conflict_penalty"] = sc

    # Freshness
    fp = _freshness_penalty(enrichment)
    comps["freshness_penalty"] = fp

    # Historical calibration
    hc = _historical_cal_adjustment(enrichment)
    comps["historical_cal_adj"] = hc

    # Model status
    ms = _model_status_penalty(model_status)
    comps["model_status_penalty"] = ms

    # Sum base uncertainty
    raw_uncertainty = base_vol + sp + lc + sc + fp + hc + ms

    # Model disagreement widening
    widening_factor = 1.0
    if disagreement_audit is not None:
        wf = getattr(disagreement_audit, "uncertainty_widening_factor", None)
        if wf is None and isinstance(disagreement_audit, dict):
            wf = disagreement_audit.get("uncertainty_widening_factor", 1.0)
        widening_factor = float(wf or 1.0)
    comps["model_disagreement_widening_factor"] = widening_factor

    dynamic_uncertainty = max(
        _UNCERTAINTY_FLOOR,
        min(_UNCERTAINTY_CAP, raw_uncertainty * widening_factor),
    )
    comps["dynamic_uncertainty_final"] = dynamic_uncertainty

    # Market weight and shrinkage
    mw, mkt_dep, mw_notes = _compute_market_weight(market_inputs or {})
    notes.extend(mw_notes)

    if market_no_vig is not None and mw > 0.0:
        calibrated = (1.0 - mw) * independent_prob + mw * market_no_vig
        notes.append(
            f"market_shrinkage: (1-{mw:.3f})*{independent_prob:.4f}"
            f" + {mw:.3f}*{market_no_vig:.4f} = {calibrated:.4f}"
        )
    else:
        calibrated = independent_prob
        mw = 0.0
        mkt_dep = False
        notes.append("no_market_data:calibrated=independent")

    calibrated = max(0.01, min(0.99, calibrated))
    model_weight = 1.0 - mw

    lb = max(0.01, calibrated - dynamic_uncertainty)
    ub = min(0.99, calibrated + dynamic_uncertainty)

    # Net edge (downstream only — not fed back upstream)
    net_edge: float | None = None
    if market_no_vig is not None:
        net_edge = round(calibrated - market_no_vig, 4)

    return CalibrationResult(
        independent_prob=round(independent_prob, 4),
        calibrated_probability=round(calibrated, 4),
        calibrated_lower_bound=round(lb, 4),
        calibrated_upper_bound=round(ub, 4),
        dynamic_uncertainty=round(dynamic_uncertainty, 4),
        model_weight=round(model_weight, 4),
        market_weight=round(mw, 4),
        market_no_vig_used=round(market_no_vig, 4) if market_no_vig is not None else None,
        market_dependent_flag=mkt_dep,
        model_status=model_status,
        net_edge=net_edge,
        calibration_notes=notes,
        uncertainty_components=comps,
    )
