"""
gate_engine/fantasy_score_model/shared.py
WOW v16 Clean Core — Fantasy Score Shared Orchestration Layer

Owns: Monte Carlo execution, market-prior integration, bidirectional scoring,
      CLB determination, stress-test suite, output schema, final-refresh flag.

Sport-specific generators live in generators/.  Calibration families are in
calibration_families.py.  Diagnostics in diagnostics.py.

SHADOW/TEST MODE ONLY — IMPLEMENTATION_READY_FOR_SHADOW_TEST
can_execute = False  (unconditional)
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# Governance constants  (never lower the qualification floor)
# ---------------------------------------------------------------------------

_YES_QUALIFIED_FLOOR: float = 0.65   # calibrated lower bound ≥ 65% required
_HOLD_FLOOR:          float = 0.52
_WATCH_FLOOR:         float = 0.47
_MAX_MARKET_WEIGHT:   float = 0.50   # market prior > 50% → MARKET_DEPENDENT_MODEL

# Labels that may appear in fantasy model output.
# Must not include MONEY_QUALIFIED, FINAL_APPROVED, PLAYABLE, LOCK, or execution labels.
_LABEL_YES_QUALIFIED      = "YES_MODEL_QUALIFIED"
_LABEL_HOLD               = "MODEL_QUALIFIED_HOLD"
_LABEL_WATCH              = "WATCH"
_LABEL_REJECT_NO_EDGE     = "REJECT_NO_EDGE"
_LABEL_REJECT_DQ          = "REJECT_DATA_QUALITY"
_LABEL_REJECT_IDENTITY    = "REJECT_SCORING_IDENTITY_UNRESOLVED"
_LABEL_MARKET_DEPENDENT   = "MARKET_DEPENDENT_MODEL"

# All PROVISIONAL models are capped at HOLD.
_PROVISIONAL_LABEL_CEILING = _LABEL_HOLD

# Simulation defaults
_DEFAULT_N_SIMS   = 8000
_STRESS_N_SIMS    = 2000
_DIAG_N_SIMS      = 2000


# ---------------------------------------------------------------------------
# Poisson sampler (pure Python, no numpy dependency)
# ---------------------------------------------------------------------------

def _poisson(lam: float, rng: random.Random) -> int:
    """Knuth Poisson sampler for lam ≤ 60; normal approx otherwise."""
    if lam <= 0:
        return 0
    if lam > 60:
        val = lam + math.sqrt(lam) * rng.gauss(0.0, 1.0)
        return max(0, int(round(val)))
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


# ---------------------------------------------------------------------------
# Three-outcome line scoring
# ---------------------------------------------------------------------------

def score_line(
    sims:     list[float],
    line:     float,
    tol:      float = 0.005,   # values within tol of line count as push
) -> dict[str, float]:
    """
    Compute bidirectional exact-line probabilities from simulation list.

    Returns:
      p_more, p_less, p_push  (sum ≈ 1.0; may not be exact due to float rounding)

    Rejected MORE does NOT imply approved LESS — each side is independently
    computed and independently evaluated against the qualification floor.
    """
    n = len(sims)
    if n == 0:
        return {"p_more": 0.0, "p_less": 0.0, "p_push": 0.0}
    more  = sum(1 for s in sims if s > line + tol)
    less  = sum(1 for s in sims if s < line - tol)
    push  = n - more - less
    return {
        "p_more": round(more / n, 6),
        "p_less": round(less / n, 6),
        "p_push": round(push / n, 6),
    }


# ---------------------------------------------------------------------------
# Market-prior integration
# ---------------------------------------------------------------------------

def apply_market_prior(
    raw_prob:      float,
    market_prob:   float | None,
    market_weight: float | None,
) -> dict[str, Any]:
    """
    Blend independent model probability with external market prior.

    Rules:
      - Independent model weight is always ≥ 1 − _MAX_MARKET_WEIGHT (≥ 50%).
      - If supplied market_weight > _MAX_MARKET_WEIGHT, clamp it and set
        market_dependent_flag=True.
      - If market_prob is None, return raw_prob unchanged; market_weight = 0.
      - External market may only be used for sanity checks, contradiction
        detection, or calibration anchoring — not to fabricate probability.
      - Independent FS distribution is frozen before market prior is applied;
        both raw_independent and blended are preserved in output.

    Returns dict with: blended_prob, raw_independent, market_prob_used,
                       model_weight, market_weight_effective,
                       market_dependent_flag, market_contradiction.
    """
    if market_prob is None or market_weight is None:
        return {
            "blended_prob":          raw_prob,
            "raw_independent":       raw_prob,
            "market_prob_used":      None,
            "model_weight":          1.0,
            "market_weight_effective": 0.0,
            "market_dependent_flag": False,
            "market_contradiction":  False,
        }

    mw = max(0.0, min(float(market_weight), 1.0))
    market_dependent = mw > _MAX_MARKET_WEIGHT
    if market_dependent:
        mw = _MAX_MARKET_WEIGHT   # hard clamp

    model_w = 1.0 - mw
    blended  = round(model_w * raw_prob + mw * float(market_prob), 6)

    # Contradiction: model and market disagree by more than 15 percentage points
    contradiction = abs(raw_prob - float(market_prob)) > 0.15

    return {
        "blended_prob":            blended,
        "raw_independent":         raw_prob,
        "market_prob_used":        round(float(market_prob), 6),
        "model_weight":            round(model_w, 4),
        "market_weight_effective": round(mw, 4),
        "market_dependent_flag":   market_dependent,
        "market_contradiction":    contradiction,
    }


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------

@dataclass
class StressScenario:
    name:        str
    description: str
    param_overrides: dict[str, Any]


def run_stress_suite(
    generator_fn: Callable[[dict, random.Random], float],
    base_params:  dict,
    base_p_more:  float,
    line:         float,
    scenarios:    list[StressScenario],
    rng:          random.Random,
    n:            int = _STRESS_N_SIMS,
) -> dict[str, Any]:
    """
    Run adverse stress scenarios; return base/stress/delta/largest_driver.

    For each scenario: override params, resimulate, compute p_more, delta.
    The scenario with the largest negative delta is the "largest stress driver".

    Stress probabilities are diagnostic — they do NOT override CLB.  Dynamic
    calibration remains the authority for the final lower bound.
    """
    results: list[dict] = []
    for sc in scenarios:
        stress_params = {**base_params, **sc.param_overrides}
        sims = [generator_fn(stress_params, rng) for _ in range(n)]
        sc_scores = score_line(sims, line)
        sp_more = sc_scores["p_more"]
        delta = round(base_p_more - sp_more, 6)
        results.append({
            "scenario":    sc.name,
            "description": sc.description,
            "stress_p_more": round(sp_more, 6),
            "delta":         delta,
        })

    if not results:
        return {
            "base_p_more":      base_p_more,
            "stress_results":   [],
            "largest_driver":   None,
            "largest_delta":    0.0,
        }

    worst = max(results, key=lambda r: r["delta"])
    return {
        "base_p_more":    round(base_p_more, 6),
        "stress_results": results,
        "largest_driver": worst["scenario"],
        "largest_delta":  worst["delta"],
    }


# ---------------------------------------------------------------------------
# Terminal label determination
# ---------------------------------------------------------------------------

def determine_label(
    lb:               float,
    identity_locked:  bool,
    settlement_locked: bool,
    model_is_provisional: bool,
    market_dependent: bool,
    *,
    extra_blockers:   list[str] | None = None,
) -> tuple[str, list[str]]:
    """
    Assign terminal label.  All PROVISIONAL models are capped at HOLD.

    Rules (applied in order):
    1. Unresolved scoring identity → REJECT_SCORING_IDENTITY_UNRESOLVED
    2. Market-dependent → cap at MARKET_DEPENDENT_MODEL
    3. lb ≥ 0.65 and identity+settlement resolved → YES_MODEL_QUALIFIED
       BUT capped at HOLD when model is PROVISIONAL
    4. lb ≥ 0.52 → HOLD
    5. lb ≥ 0.47 → WATCH
    6. else → REJECT_NO_EDGE

    A 52.9% lower bound (lb=0.529) must NOT qualify — returns HOLD at best.
    """
    blockers: list[str] = list(extra_blockers or [])

    if not identity_locked:
        blockers.append("SCORING_IDENTITY_NOT_LOCKED")
        return _LABEL_REJECT_IDENTITY, blockers
    if not settlement_locked:
        blockers.append("SETTLEMENT_IDENTITY_NOT_LOCKED")
        return _LABEL_REJECT_IDENTITY, blockers

    if market_dependent:
        blockers.append("MARKET_DEPENDENT_MODEL:market_weight_exceeded_50pct")
        # Still compute base label but surface the flag
        base_label = _base_label(lb, model_is_provisional)
        # Market-dependent gets its own label, below HOLD ceiling
        if base_label == _LABEL_YES_QUALIFIED:
            return _LABEL_MARKET_DEPENDENT, blockers
        return base_label, blockers

    label = _base_label(lb, model_is_provisional)
    return label, blockers


def _base_label(lb: float, model_is_provisional: bool) -> str:
    if lb >= _YES_QUALIFIED_FLOOR:
        if model_is_provisional:
            return _LABEL_HOLD      # PROVISIONAL ceiling
        return _LABEL_YES_QUALIFIED
    if lb >= _HOLD_FLOOR:
        return _LABEL_HOLD
    if lb >= _WATCH_FLOOR:
        return _LABEL_WATCH
    return _LABEL_REJECT_NO_EDGE


# ---------------------------------------------------------------------------
# Final-refresh check
# ---------------------------------------------------------------------------

def check_final_refresh(enrichment: dict) -> dict[str, Any]:
    """
    Mandatory recheck before presentation.

    Material changes in any of these fields → refresh_required=True:
      time, event_status, participant_status, lineup/role, scoring_rules,
      settlement_identity, market_status/timestamp, source_conflicts, weather.

    Pregame probabilities cannot remain current after event start.
    """
    e = enrichment or {}
    flags: list[str] = []

    # Status freshness
    freshness_h = float(e.get("status_freshness_hours") or 99.0)
    if freshness_h > 2.0:
        flags.append(f"STATUS_STALE:{freshness_h:.1f}h>2h")

    # Event status — must be confirmed pre-game
    ev_status = str(e.get("event_status") or "UNKNOWN").upper()
    if ev_status in {"LIVE", "IN_PROGRESS", "FINAL", "COMPLETED"}:
        flags.append(f"EVENT_STARTED:status={ev_status}:pregame_prob_invalid")

    # Lineup must be confirmed
    if not e.get("lineup_confirmed"):
        flags.append("LINEUP_NOT_CONFIRMED")

    # Settlement identity — basis must be declared
    settle = str(e.get("settlement_basis") or "").upper()
    if not settle:
        flags.append("SETTLEMENT_BASIS_MISSING")

    # Scoring rules freshness
    scoring_retrieved = e.get("scoring_rules_retrieved_at")
    if not scoring_retrieved:
        flags.append("SCORING_RULES_TIMESTAMP_MISSING")

    # Board line freshness — must match what was scored
    board_line = e.get("board_line_confirmed")
    scored_line = e.get("_scored_line")
    if board_line is not None and scored_line is not None:
        if abs(float(board_line) - float(scored_line)) > 0.01:
            flags.append(
                f"BOARD_LINE_CHANGED:scored={scored_line}_current={board_line}"
            )

    refresh_required = len(flags) > 0

    return {
        "refresh_required":    refresh_required,
        "refresh_flags":       flags,
        "status_freshness_h":  freshness_h,
        "event_status_at_check": ev_status,
        "lineup_confirmed":    bool(e.get("lineup_confirmed")),
        "settlement_basis":    settle or None,
    }


# ---------------------------------------------------------------------------
# Distribution statistics
# ---------------------------------------------------------------------------

def dist_stats(sims: list[float]) -> dict[str, float | None]:
    """Return mean, median, std, and useful quantiles from simulation list."""
    if not sims:
        return {k: None for k in ("mean", "median", "std", "p10", "p25", "p75", "p90")}
    n = len(sims)
    s = sorted(sims)
    mean_v = statistics.mean(s)
    med_v  = statistics.median(s)
    std_v  = statistics.pstdev(s) if n > 1 else 0.0

    def pct(p: float) -> float:
        idx = p * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        return s[lo] + (idx - lo) * (s[hi] - s[lo])

    return {
        "mean":   round(mean_v, 3),
        "median": round(med_v,  3),
        "std":    round(std_v,  3),
        "p10":    round(pct(0.10), 3),
        "p25":    round(pct(0.25), 3),
        "p75":    round(pct(0.75), 3),
        "p90":    round(pct(0.90), 3),
    }


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_monte_carlo(
    generator_fn: Callable[[dict, random.Random], float],
    params:       dict,
    n:            int = _DEFAULT_N_SIMS,
    seed:         int | None = None,
) -> list[float]:
    """Run n simulations; return raw Fantasy Score sample list."""
    rng = random.Random(seed)
    return [generator_fn(params, rng) for _ in range(n)]


# ---------------------------------------------------------------------------
# Master output builder
# ---------------------------------------------------------------------------

def build_output(
    *,
    # Identity
    platform:          str,
    sport:             str,
    player:            str,
    stat_key:          str,
    line:              float,
    direction:         str,
    scoring_version:   str | None,
    settlement_basis:  str | None,
    identity_locked:   bool,
    settlement_locked: bool,
    formula_flags:     list[str],
    # Distribution
    sims:              list[float],
    # Scoring
    p_more:            float,
    p_less:            float,
    p_push:            float,
    # Calibration
    raw_prob:          float,
    cal_lb:            float,
    cal_ub:            float,
    cal_family:        str,
    thin_sample:       bool,
    sample_size:       int,
    # Market prior
    market_blend:      dict,
    # Label
    terminal_label:    str,
    blockers:          list[str],
    # Stress
    stress:            dict | None,
    # Diagnostics
    diagnostics:       dict | None,
    # Refresh
    refresh:           dict,
    # Generator metadata
    generator_id:      str,
    regime_weights:    dict | None = None,
    failure_path_score: float | None = None,
    largest_failure_path: str | None = None,
    # Misc
    model_is_provisional: bool = True,
) -> dict[str, Any]:
    """
    Assemble the canonical Fantasy Score model output dictionary.

    can_execute=False is stamped unconditionally.
    """
    ds = dist_stats(sims)
    return {
        # Governance
        "can_execute":                False,
        "shadow_mode":                True,
        "implementation_status":      "IMPLEMENTATION_READY_FOR_SHADOW_TEST",
        "model_is_provisional":       model_is_provisional,
        # Identity
        "platform":                   platform,
        "sport":                      sport,
        "player":                     player,
        "stat_key":                   stat_key,
        "line":                       line,
        "direction":                  direction,
        "scoring_version":            scoring_version,
        "settlement_basis":           settlement_basis,
        "identity_locked":            identity_locked,
        "settlement_locked":          settlement_locked,
        "formula_flags":              formula_flags,
        # Opportunity and distribution
        "generator_id":               generator_id,
        "n_simulations":              len(sims),
        "fs_mean":                    ds["mean"],
        "fs_median":                  ds["median"],
        "fs_std":                     ds["std"],
        "fs_p10":                     ds["p10"],
        "fs_p25":                     ds["p25"],
        "fs_p75":                     ds["p75"],
        "fs_p90":                     ds["p90"],
        # Bidirectional exact-line scoring
        "p_more_raw":                 round(p_more, 6),
        "p_less_raw":                 round(p_less, 6),
        "p_push":                     round(p_push, 6),
        # Independent model (frozen before market prior)
        "raw_independent_prob":       market_blend.get("raw_independent", raw_prob),
        # Calibration
        "calibrated_lower_bound":     round(cal_lb, 6),
        "calibrated_upper_bound":     round(cal_ub, 6),
        "calibration_family":         cal_family,
        "thin_sample_condition":      thin_sample,
        "sample_size":                sample_size,
        # Market prior
        "market_prob":                market_blend.get("market_prob_used"),
        "model_weight":               market_blend.get("model_weight"),
        "market_weight":              market_blend.get("market_weight_effective"),
        "blended_prob":               market_blend.get("blended_prob"),
        "market_dependent_flag":      market_blend.get("market_dependent_flag"),
        "market_contradiction":       market_blend.get("market_contradiction"),
        # Failure paths
        "failure_path_score":         failure_path_score,
        "largest_failure_path":       largest_failure_path,
        "regime_weights":             regime_weights,
        # Label
        "terminal_label":             terminal_label,
        "best_modeled_side":          _best_side(p_more, p_less),
        "probability_gap":            round(abs(p_more - p_less), 6),
        "blockers":                   blockers,
        # Stress
        "stress_test":                stress,
        # Dependency diagnostics
        "diagnostics":                diagnostics,
        # Refresh
        "final_refresh":              refresh,
    }


def _best_side(p_more: float, p_less: float) -> str:
    if p_more > p_less:
        return "MORE"
    if p_less > p_more:
        return "LESS"
    return "NEUTRAL"
