"""
gate_engine/mlb/first_inning_efficiency.py
WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE
WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY

Implements two mandatory pre- and post-event-tree audits for MLB
first-inning pitch-count LESS candidates:

  1. Recent First-Inning Efficiency Deterioration Score
     Seven-metric weighted score + Tier-2 modifiers.  Controls the
     efficiency_ceiling applied before the batter-by-batter event tree.

  2. Directional Fragility Score (DFS)
     Computed from event-tree outputs.  Controls the directional_ceiling
     applied after the event tree but before market/payout ceilings.

The event-tree itself remains the controlling model.  Neither gate can
erase an upstream or downstream ceiling — only add a more restrictive one.

can_execute = False unconditional.
"""
from __future__ import annotations

can_execute = False

from typing import Any

# ---------------------------------------------------------------------------
# Efficiency band labels
# ---------------------------------------------------------------------------

BAND_STABLE              = "STABLE"
BAND_MILD                = "MILD_DETERIORATION"
BAND_MATERIAL            = "MATERIAL_DETERIORATION"
BAND_SEVERE              = "SEVERE_DETERIORATION"
BAND_INCOMPLETE          = "EFFICIENCY_SCORE_INCOMPLETE"

# Directional fragility labels
DFS_LOW      = "LOW_DIRECTIONAL_FRAGILITY"
DFS_MODERATE = "MODERATE_DIRECTIONAL_FRAGILITY"
DFS_HIGH     = "HIGH_DIRECTIONAL_FRAGILITY"
DFS_SEVERE   = "SEVERE_DIRECTIONAL_FRAGILITY"

# Ceiling constants (mirrors WOW terminal label names)
CEILING_NONE    = None            # no additional restriction
CEILING_HOLD    = "MODEL_QUALIFIED_HOLD"
CEILING_WATCH   = "WATCH"

# ---------------------------------------------------------------------------
# Tier-1 metric definitions
# ---------------------------------------------------------------------------
# Each entry: (key_in_metrics, weight, adverse_condition_description)
_TIER1_METRICS: list[tuple[str, float]] = [
    ("pbf_deterioration",        0.20),   # P/BF recent >= baseline * 1.08
    ("pitches_per_start_det",    0.20),   # 1IP pitches/start recent >= baseline * 1.10
    ("walk_rate_1ip_det",        0.15),   # 1IP walk rate recent >= baseline + 3.0 pp
    ("first_pitch_strike_det",   0.15),   # first-pitch strike rate <= baseline - 5.0 pp
    ("zone_rate_det",            0.10),   # zone rate recent <= baseline - 4.0 pp
    ("overall_bb_rate_det",      0.10),   # overall BB rate recent >= baseline + 3.0 pp
    ("csw_rate_det",             0.10),   # CSW rate recent <= baseline - 4.0 pp
]

MIN_TIER1_REQUIRED = 4   # fewer → EFFICIENCY_SCORE_INCOMPLETE

# ---------------------------------------------------------------------------
# Public API: efficiency score
# ---------------------------------------------------------------------------

def calculate_recent_1ip_efficiency_score(
    pitcher_id: str,
    as_of: str,
    *,
    # Caller supplies pre-computed metric flags and raw values.
    # Each metric_flags entry is keyed by the _TIER1_METRICS key and is one of:
    #   1.0  — adverse trigger fired
    #   0.5  — marginal (e.g. threshold nearly met)
    #   0.0  — no adverse signal
    #   None — data unavailable
    metric_flags: dict[str, float | None] | None = None,
    # Tier-2 modifiers: each True/False/None
    whip_increase_15pct: bool | None = None,
    hard_hit_increase_5pp: bool | None = None,
    chase_decrease_4pp: bool | None = None,
    # Window metadata
    recent_window_starts: int = 3,
    baseline_label: str = "current_season",
) -> dict[str, Any]:
    """
    Compute the Recent First-Inning Efficiency Deterioration Score.

    Parameters
    ----------
    pitcher_id : str
        Opaque pitcher identifier (used for traceability only).
    as_of : str
        ISO date string.  Must be a pregame timestamp — postgame data
        must not be used to reconstruct a pregame score.
    metric_flags : dict mapping Tier-1 metric keys to 0.0 / 0.5 / 1.0 / None.
        If None, every metric is treated as unavailable.
    whip_increase_15pct, hard_hit_increase_5pp, chase_decrease_4pp :
        Tier-2 adverse triggers (True = trigger fired, None = unknown).
    recent_window_starts : int
        How many recent starts were used.  3 is preferred; 5 is the fallback.
    baseline_label : str
        Human-readable description of the baseline used.

    Returns
    -------
    dict with keys matching the handoff spec:
        recent_window, baseline_window, metric_values, metric_flags,
        tier_1_score, tier_2_modifier, final_score, band,
        probability_haircut, ceiling, data_coverage_count
    """
    mf = metric_flags or {}

    # ── Tier 1: count available metrics and compute weighted score ──────────
    available_keys: list[str] = []
    weighted_sum: float = 0.0
    weight_available: float = 0.0

    for key, weight in _TIER1_METRICS:
        val = mf.get(key)
        if val is None:
            continue   # data unavailable — exclude from denominator
        available_keys.append(key)
        weighted_sum += val * weight
        weight_available += weight

    data_coverage_count = len(available_keys)
    incomplete = data_coverage_count < MIN_TIER1_REQUIRED

    if incomplete:
        tier1_score = None
    elif weight_available == 0.0:
        tier1_score = 0.0
    else:
        # Re-normalise to the sum of available weights so missing metrics don't
        # deflate the score.
        tier1_score = weighted_sum / weight_available

    # ── Tier 2: modifiers (capped at 0.10 total) ───────────────────────────
    tier2_raw = 0.0
    _T2_PER_TRIGGER = 0.05   # two triggers = 0.10 (cap); third adds no more
    if whip_increase_15pct is True:
        tier2_raw += _T2_PER_TRIGGER
    if hard_hit_increase_5pp is True:
        tier2_raw += _T2_PER_TRIGGER
    if chase_decrease_4pp is True:
        tier2_raw += _T2_PER_TRIGGER
    tier2_modifier = min(tier2_raw, 0.10)

    # ── Final score ─────────────────────────────────────────────────────────
    if incomplete:
        final_score = None
        band = BAND_INCOMPLETE
        probability_haircut = 0.0
        ceiling = CEILING_HOLD   # missing data → cap at HOLD
    else:
        final_score = min(1.0, tier1_score + tier2_modifier)
        band, probability_haircut, ceiling = _classify_efficiency(final_score)

    return {
        "pitcher_id":             pitcher_id,
        "as_of":                  as_of,
        "recent_window":          f"last_{recent_window_starts}_starts",
        "baseline_window":        baseline_label,
        "metric_flags":           {k: mf.get(k) for k, _ in _TIER1_METRICS},
        "metric_values":          mf,
        "data_coverage_count":    data_coverage_count,
        "tier_1_score":           tier1_score,
        "tier_2_modifier":        tier2_modifier,
        "final_efficiency_deterioration_score": final_score,
        "efficiency_band":        band,
        "efficiency_probability_haircut": probability_haircut,
        "efficiency_ceiling":     ceiling,
        "can_execute":            False,
    }


def _classify_efficiency(score: float) -> tuple[str, float, str | None]:
    """
    Returns (band, probability_haircut, ceiling).

    Bands and enforcement (from v3 skill spec):
      score < 0.30                     → STABLE; no haircut, no ceiling
      0.30 <= score < 0.50             → MILD;   haircut 0.02, no ceiling
      0.50 <= score < 0.70             → MATERIAL; top-confidence prohibited
                                                   ceiling = MODEL_QUALIFIED_HOLD
      score >= 0.70                    → SEVERE;  ceiling = WATCH
    """
    if score < 0.30:
        return BAND_STABLE,   0.00, CEILING_NONE
    if score < 0.50:
        return BAND_MILD,     0.02, CEILING_NONE
    if score < 0.70:
        return BAND_MATERIAL, 0.00, CEILING_HOLD
    return BAND_SEVERE,       0.00, CEILING_WATCH


# ---------------------------------------------------------------------------
# Public API: Directional Fragility Score
# ---------------------------------------------------------------------------

def calculate_directional_fragility_score(
    *,
    # Event-tree outputs (all probabilities in [0, 1])
    p_less_and_bf3: float | None = None,      # P(LESS ∩ BF=3)
    p_less: float | None = None,               # P(LESS)
    p_more_given_bf4_plus: float | None = None, # P(MORE | BF≥4)
    right_tail_mass_line_plus_3: float | None = None,  # P(pitches ≥ line + 3)
    raw_p_less: float | None = None,           # raw (pre-haircut) P(LESS)
    calibrated_lower_bound_less: float | None = None,  # post-haircut lower bound
) -> dict[str, Any]:
    """
    Compute the Directional Fragility Score for a 1IP LESS candidate.

    All inputs must come from the batter-by-batter event-tree simulation —
    not from averaging recent results or game-level ERA/K%.

    Formula
    -------
    DFS = 0.35 * three_batter_less_dependence
        + 0.30 * extended_inning_loss_rate
        + 0.20 * right_tail_mass
        + 0.15 * min(1, probability_uncertainty_gap / 0.10)

    Returns
    -------
    dict with all required handoff fields.
    """
    missing_inputs: list[str] = []

    # ── three_batter_less_dependence ─────────────────────────────────────
    if p_less_and_bf3 is None or p_less is None:
        tbl = None
        missing_inputs.append("p_less_and_bf3 or p_less")
    elif p_less == 0.0:
        tbl = 0.0   # near-zero MORE → preserve spec
    else:
        tbl = min(1.0, p_less_and_bf3 / p_less)

    # ── extended_inning_loss_rate ─────────────────────────────────────────
    if p_more_given_bf4_plus is None:
        eilr = None
        missing_inputs.append("p_more_given_bf4_plus")
    else:
        eilr = p_more_given_bf4_plus

    # ── right_tail_mass ──────────────────────────────────────────────────
    if right_tail_mass_line_plus_3 is None:
        rtm = None
        missing_inputs.append("right_tail_mass_line_plus_3")
    else:
        rtm = right_tail_mass_line_plus_3

    # ── probability_uncertainty_gap ──────────────────────────────────────
    if raw_p_less is None or calibrated_lower_bound_less is None:
        gap = None
        norm_gap = None
        missing_inputs.append("raw_p_less or calibrated_lower_bound_less")
    else:
        gap = max(0.0, raw_p_less - calibrated_lower_bound_less)
        norm_gap = min(1.0, gap / 0.10)

    # ── DFS computation (requires all four components) ───────────────────
    if any(v is None for v in (tbl, eilr, rtm, norm_gap)):
        dfs_score = None
        dfs_label = DFS_HIGH     # fail-closed: treat missing as HIGH
        directional_ceiling = CEILING_HOLD
        hard_override = False
        override_reason = None
    else:
        dfs_score = (
            0.35 * tbl
            + 0.30 * eilr
            + 0.20 * rtm
            + 0.15 * norm_gap
        )

        # Hard override check (before threshold classification)
        hard_override = bool(tbl >= 0.80 and eilr >= 0.70)
        if hard_override:
            dfs_label = DFS_SEVERE
            directional_ceiling = CEILING_WATCH
            override_reason = (
                f"three_batter_less_dependence={tbl:.3f}>=0.80 "
                f"AND extended_inning_loss_rate={eilr:.3f}>=0.70"
            )
        else:
            override_reason = None
            dfs_label, directional_ceiling = _classify_dfs(dfs_score)

    return {
        # Input passthrough
        "p_less_and_bf3":               p_less_and_bf3,
        "p_less":                        p_less,
        "p_more_given_bf4_plus":         p_more_given_bf4_plus,
        "right_tail_mass_line_plus_3":   right_tail_mass_line_plus_3,
        "raw_p_less":                    raw_p_less,
        "calibrated_lower_bound_less":   calibrated_lower_bound_less,
        # Computed components
        "three_batter_less_dependence":  tbl,
        "extended_inning_loss_rate":     eilr,
        "right_tail_mass":               rtm,
        "probability_uncertainty_gap":   gap,
        "normalized_uncertainty_gap":    norm_gap,
        # Score and classification
        "directional_fragility_score":   dfs_score,
        "directional_fragility_label":   dfs_label,
        "directional_ceiling":           directional_ceiling,
        # Hard override
        "hard_override_triggered":       hard_override,
        "hard_override_reason":          override_reason,
        # Missing input list
        "missing_inputs":                missing_inputs,
        "can_execute":                   False,
    }


def _classify_dfs(score: float) -> tuple[str, str | None]:
    """
    Returns (label, ceiling) for a computed DFS value.

    Thresholds from v3 skill:
      DFS < 0.55          → LOW;      no additional ceiling
      0.55 <= DFS < 0.70  → MODERATE; subtract 0.02 from lower bound (caller's job),
                                       ceiling = None (mild enough not to cap label)
      0.70 <= DFS < 0.80  → HIGH;     top-confidence prohibited, ceiling = HOLD
      DFS >= 0.80         → SEVERE;   ceiling = WATCH
    """
    if score < 0.55:
        return DFS_LOW,      CEILING_NONE
    if score < 0.70:
        return DFS_MODERATE, CEILING_NONE    # caller must subtract 0.02 from lower bound
    if score < 0.80:
        return DFS_HIGH,     CEILING_HOLD
    return DFS_SEVERE,       CEILING_WATCH


# ---------------------------------------------------------------------------
# Ceiling propagation helper
# ---------------------------------------------------------------------------

_CEILING_RANK: dict[str | None, int] = {
    CEILING_NONE:  0,
    CEILING_HOLD:  1,
    CEILING_WATCH: 2,
}


def apply_lowest_ceiling(*ceilings: str | None) -> str | None:
    """
    Return the most restrictive ceiling from an ordered list.

    Ranking: None (no ceiling) < MODEL_QUALIFIED_HOLD < WATCH.

    No downstream pass may erase an upstream ceiling — this helper
    enforces that invariant.
    """
    result = CEILING_NONE
    for c in ceilings:
        if _CEILING_RANK.get(c, 0) > _CEILING_RANK.get(result, 0):
            result = c
    return result
