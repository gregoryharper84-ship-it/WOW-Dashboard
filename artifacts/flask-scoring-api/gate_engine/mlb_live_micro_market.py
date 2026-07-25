"""
mlb_live_micro_market.py — cm_mlb_live_micro_market_model

WOW Stage 2 live micro-market analysis for MLB 1–3 inning props.

Accepts live game-state inputs and returns a structured probability analysis
with opportunity distribution, failure path, and terminal label. The backend
is the sole source of truth for these calculations — the Custom GPT orchestrates
and explains, but does not recompute or override.

HARD RULE:
    can_execute = False
    EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
MODULE_VERSION = "v1.0"

# Staleness thresholds
LIVE_STATE_STALE_SECONDS = 300     # 5 minutes — data must be this fresh
LIVE_STATE_CRITICAL_SECONDS = 600  # 10 minutes — data this old is rejected

# Pitch count leash thresholds by regime
PITCH_COUNT_REGIMES = [
    (0,  50,  "fresh",       0.00),   # full leash remaining
    (50, 70,  "moderate",    0.10),   # slight uncertainty added
    (70, 85,  "elevated",    0.25),   # meaningful pull risk
    (85, 95,  "high",        0.45),   # high pull risk
    (95, 105, "critical",    0.70),   # very likely near exit
    (105, 999, "exceeded",   1.00),   # at or past typical leash
]

# Batters faced leash thresholds
BATTERS_FACED_LEASH = {
    "fresh":    (0,  15),
    "moderate": (15, 21),
    "elevated": (21, 24),
    "high":     (24, 999),
}

# PrizePicks Fantasy Score (Baseball / Hitter) scoring table
PRIZEPICKS_FANTASY_SCORE = {
    "single":       3.0,
    "double":       6.0,
    "triple":       9.0,
    "home_run":    12.0,
    "run":          3.0,
    "rbi":          3.0,
    "stolen_base":  6.0,
    "walk":         2.0,
}

# Default PA rates when season_log is unavailable (MLB average approximations)
_MLB_DEFAULT_RATES = {
    "k_rate":    0.220,
    "bb_rate":   0.085,
    "hbp_rate":  0.010,
    "hr_per_pa": 0.035,
    "triple_per_pa": 0.004,
    "double_per_pa": 0.050,
    "single_per_pa": 0.145,
    "sb_per_game":   0.12,
    "run_per_pa":    0.052,
    "rbi_per_pa":    0.048,
}


# ---------------------------------------------------------------------------
# Live state validation
# ---------------------------------------------------------------------------

def validate_live_state(state: dict[str, Any]) -> dict[str, Any]:
    """
    Validate the freshness and completeness of a live game-state snapshot.

    Required fields:
        capture_timestamp (ISO-8601 UTC), inning (int), outs (int 0–2)

    Returns:
        {
          status: FRESH | STALE | CRITICAL | MISSING_REQUIRED_FIELDS | INVALID
          age_seconds: float | None
          missing_fields: list[str]
          data_freshness: str   (description)
          passed: bool
        }
    """
    required = ["capture_timestamp", "inning", "outs"]
    missing = [f for f in required if state.get(f) is None]

    if missing:
        return {
            "status":       "MISSING_REQUIRED_FIELDS",
            "age_seconds":  None,
            "missing_fields": missing,
            "data_freshness": "UNAVAILABLE",
            "passed":        False,
        }

    # Parse capture_timestamp
    ts_raw = state.get("capture_timestamp")
    try:
        if isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        else:
            ts = ts_raw
        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_seconds = (now - ts).total_seconds()
    except Exception:
        return {
            "status":       "INVALID",
            "age_seconds":  None,
            "missing_fields": [],
            "data_freshness": "PARSE_ERROR",
            "passed":        False,
        }

    inning = state.get("inning", 0)
    outs   = state.get("outs", 0)
    if inning < 1 or outs not in (0, 1, 2):
        return {
            "status":        "INVALID",
            "age_seconds":   age_seconds,
            "missing_fields": [],
            "data_freshness": f"Invalid inning={inning} outs={outs}",
            "passed":         False,
        }

    if age_seconds <= LIVE_STATE_STALE_SECONDS:
        status = "FRESH"
        passed = True
        desc   = f"Live state is fresh ({round(age_seconds)}s old)"
    elif age_seconds <= LIVE_STATE_CRITICAL_SECONDS:
        status = "STALE"
        passed = False
        desc   = f"Live state is stale ({round(age_seconds)}s old, limit {LIVE_STATE_STALE_SECONDS}s)"
    else:
        status = "CRITICAL"
        passed = False
        desc   = f"Live state age {round(age_seconds)}s exceeds critical threshold ({LIVE_STATE_CRITICAL_SECONDS}s)"

    return {
        "status":        status,
        "age_seconds":   round(age_seconds, 1),
        "missing_fields": [],
        "data_freshness": desc,
        "passed":         passed,
    }


# ---------------------------------------------------------------------------
# Opportunity distribution
# ---------------------------------------------------------------------------

def compute_opportunity_distribution(state: dict[str, Any]) -> dict[str, Any]:
    """
    Estimate remaining plate-appearance / out opportunities for the market scope.

    For pitchers: remaining outs available within scope.
    For hitters:  remaining plate appearances available within scope.

    Inputs from state:
        inning, outs, remaining_innings_scope, batting_order (1-9),
        pitch_count, batters_faced, market_type

    Returns:
        {
          remaining_pa_hitter: float
          remaining_outs_pitcher: float
          pitch_count_regime: str
          pitch_leash_pull_probability: float
          batters_faced_regime: str
          scope_outs_total: int
          outs_consumed: int
          outs_remaining: int
        }
    """
    inning                 = max(1, int(state.get("inning", 1)))
    outs                   = max(0, min(2, int(state.get("outs", 0))))
    remaining_innings_scope = int(state.get("remaining_innings_scope", 3))
    batting_order          = max(1, min(9, int(state.get("batting_order", 1))))
    pitch_count            = int(state.get("pitch_count", 0))
    batters_faced          = int(state.get("batters_faced", 0))

    # Outs consumed so far within the scope
    # scope starts at inning 1 (or a specific starting inning)
    scope_start_inning = max(1, inning - (remaining_innings_scope - 1))
    outs_in_completed  = max(0, (inning - scope_start_inning)) * 3
    outs_consumed      = outs_in_completed + outs
    scope_outs_total   = remaining_innings_scope * 3
    outs_remaining     = max(0, scope_outs_total - outs_consumed)

    # Hitter: remaining PA estimation using batting-order position
    # In 3 remaining innings, each spot in the order gets approx 3/9 PAs
    # Adjusted by where in the order relative to current batter
    pa_per_inning_per_hitter = 1.0 / 3.0  # rough average
    remaining_innings = max(0, outs_remaining / 3.0)
    remaining_pa_hitter = round(remaining_innings * (1 + (9 - batting_order) / 27), 2)
    remaining_pa_hitter = max(0.0, remaining_pa_hitter)

    # Pitcher: leash regime from pitch count
    pitch_leash_pull_probability = 0.0
    pitch_count_regime = "fresh"
    for lo, hi, regime, pull_prob in PITCH_COUNT_REGIMES:
        if lo <= pitch_count < hi:
            pitch_count_regime         = regime
            pitch_leash_pull_probability = pull_prob
            break

    # Batters faced regime
    bf = batters_faced
    if bf < 15:
        bf_regime = "fresh"
    elif bf < 21:
        bf_regime = "moderate"
    elif bf < 24:
        bf_regime = "elevated"
    else:
        bf_regime = "high"

    # Remaining outs for pitcher adjusted for pull probability
    remaining_outs_pitcher = round(outs_remaining * (1.0 - pitch_leash_pull_probability), 2)

    return {
        "remaining_pa_hitter":            remaining_pa_hitter,
        "remaining_outs_pitcher":         remaining_outs_pitcher,
        "pitch_count_regime":             pitch_count_regime,
        "pitch_leash_pull_probability":   round(pitch_leash_pull_probability, 3),
        "batters_faced_regime":           bf_regime,
        "scope_outs_total":               scope_outs_total,
        "outs_consumed":                  outs_consumed,
        "outs_remaining":                 outs_remaining,
    }


# ---------------------------------------------------------------------------
# Pitcher K distribution
# ---------------------------------------------------------------------------

def compute_pitcher_k_distribution(
    state: dict[str, Any],
    opp_dist: dict[str, Any],
    k_rate: float | None = None,
) -> dict[str, Any]:
    """
    Compute remaining strikeout distribution for a pitcher.

    k_rate: per-batter K rate (0–1). Defaults to MLB average if None.

    Returns:
        {
          k_rate_used: float
          expected_remaining_k: float
          std_remaining_k: float
          p_at_least_n: dict[str, float]   # {"0":p, "1":p, "2":p, "3":p}
          cushion_risk: str                 # LOW | MODERATE | HIGH | CRITICAL
          one_more_batter_risk: float       # P(one more hit = no K) if near cushion
        }
    """
    k_rate_used = k_rate if (k_rate is not None and 0 < k_rate < 1) else _MLB_DEFAULT_RATES["k_rate"]

    # Remaining batters = remaining pitcher outs / (1 - k_rate) roughly
    # More precisely: batters ≈ outs / (1 - hit_through_rate)
    remaining_outs = opp_dist.get("remaining_outs_pitcher", 0)
    # Each batter results in 1 out (K or field out), 1 hit, or BB
    # Average outs per batter ≈ 1 - BB_rate - HBP_rate ≈ 0.905
    outs_per_batter = 1.0 - _MLB_DEFAULT_RATES["bb_rate"] - _MLB_DEFAULT_RATES["hbp_rate"]
    remaining_batters = max(0.0, remaining_outs / max(outs_per_batter, 0.01))

    # Binomial distribution: n=remaining_batters, p=k_rate
    n    = remaining_batters
    p    = k_rate_used
    mean = round(n * p, 3)
    std  = round(math.sqrt(n * p * (1 - p)) if n > 0 else 0, 3)

    # CDF values: P(K >= k) for k in 0..4
    p_at_least: dict[str, float] = {}
    for k in range(5):
        if n <= 0:
            p_at_least[str(k)] = 1.0 if k == 0 else 0.0
        else:
            # P(X >= k) = 1 - P(X <= k-1) using regularized incomplete beta
            # Approximate via normal CDF for speed
            if k == 0:
                p_at_least["0"] = 1.0
                continue
            z = (k - 0.5 - mean) / max(std, 0.001)
            p_at_least[str(k)] = round(max(0.0, min(1.0, 1.0 - _norm_cdf(z))), 4)

    # Cushion risk: how close is the expected remaining K to 0
    pull_prob = opp_dist.get("pitch_leash_pull_probability", 0)
    if pull_prob >= 0.70 or mean < 0.3:
        cushion_risk = "CRITICAL"
    elif pull_prob >= 0.45 or mean < 0.8:
        cushion_risk = "HIGH"
    elif pull_prob >= 0.25 or mean < 1.5:
        cushion_risk = "MODERATE"
    else:
        cushion_risk = "LOW"

    # One-more-batter risk: P(0 Ks in 1 remaining PA)
    one_more_batter_risk = round(max(0.0, 1.0 - p), 4)

    return {
        "k_rate_used":           round(k_rate_used, 4),
        "expected_remaining_k":  mean,
        "std_remaining_k":       std,
        "p_at_least_n":          p_at_least,
        "cushion_risk":          cushion_risk,
        "one_more_batter_risk":  one_more_batter_risk,
    }


def _norm_cdf(z: float) -> float:
    """Approximate standard normal CDF using Abramowitz & Stegun."""
    t = 1.0 / (1.0 + 0.2316419 * abs(z))
    coeffs = (0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429)
    poly = sum(c * t ** (i + 1) for i, c in enumerate(coeffs))
    p = 1.0 - math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi) * poly
    return p if z >= 0 else 1.0 - p


# ---------------------------------------------------------------------------
# Hitter scoring event distribution
# ---------------------------------------------------------------------------

def compute_hitter_event_distribution(
    state: dict[str, Any],
    opp_dist: dict[str, Any],
    rates: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Compute the expected discrete scoring event distribution for a hitter.

    rates: per-PA rates for each event type. Falls back to MLB defaults.

    Returns:
        {
          remaining_pa: float
          expected_events: dict[event, float]    # expected count per remaining PA
          expected_fantasy_score: float
          scoring_event_distribution: dict[event, float]  # probability ≥1 of each
          p_zero_hits: float
          fantasy_score_std: float
        }
    """
    r = _MLB_DEFAULT_RATES.copy()
    if rates:
        r.update({k: v for k, v in rates.items() if isinstance(v, (int, float))})

    remaining_pa = opp_dist.get("remaining_pa_hitter", 0.0)
    n = max(0.0, float(remaining_pa))

    # Per-PA probabilities
    p_hr     = min(r["hr_per_pa"], 0.12)
    p_triple = min(r["triple_per_pa"], 0.02)
    p_double = min(r["double_per_pa"], 0.10)
    p_single = min(r["single_per_pa"], 0.25)
    p_bb     = min(r["bb_rate"], 0.20)
    p_sb_per_pa = min(r.get("sb_per_game", _MLB_DEFAULT_RATES["sb_per_game"]) / 4.0, 0.05)
    run_per_pa  = min(r["run_per_pa"], 0.15)
    rbi_per_pa  = min(r["rbi_per_pa"], 0.15)

    hit_per_pa = p_single + p_double + p_triple + p_hr

    # Expected event counts over remaining PA
    exp: dict[str, float] = {
        "single":      round(p_single * n, 3),
        "double":      round(p_double * n, 3),
        "triple":      round(p_triple * n, 3),
        "home_run":    round(p_hr * n, 3),
        "walk":        round(p_bb * n, 3),
        "stolen_base": round(p_sb_per_pa * n, 3),
        "run":         round(run_per_pa * n, 3),
        "rbi":         round(rbi_per_pa * n, 3),
    }

    # Expected fantasy score
    scoring = PRIZEPICKS_FANTASY_SCORE
    efs = sum(exp[k] * scoring[k] for k in scoring)

    # Variance contribution per event (Poisson: Var = mean)
    # FS variance ≈ sum(pts² * expected_count) for each event
    var_fs = sum((scoring[k] ** 2) * exp[k] for k in scoring)
    std_fs = round(math.sqrt(max(0, var_fs)), 3)

    # P(≥1 occurrence) per event type: 1 - P(0) = 1 - (1-p)^n
    sed: dict[str, float] = {}
    for event, p_per_pa in [
        ("single", p_single), ("double", p_double), ("triple", p_triple),
        ("home_run", p_hr), ("walk", p_bb), ("stolen_base", p_sb_per_pa),
    ]:
        sed[event] = round(max(0.0, min(1.0, 1.0 - (1 - p_per_pa) ** n)), 4)

    p_zero_hits = round(max(0.0, (1.0 - hit_per_pa) ** n), 4)

    return {
        "remaining_pa":               round(n, 2),
        "expected_events":            exp,
        "expected_fantasy_score":     round(efs, 3),
        "scoring_event_distribution": sed,
        "p_zero_hits":                p_zero_hits,
        "fantasy_score_std":          std_fs,
    }


# ---------------------------------------------------------------------------
# Probability computation (P_MORE / P_LESS)
# ---------------------------------------------------------------------------

def compute_raw_probability(
    market_type: str,
    direction: str,
    line: float,
    opp_dist: dict[str, Any],
    k_dist:   dict[str, Any] | None = None,
    hit_dist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute P_MORE and P_LESS for the live micro prop.

    For pitcher K markets:
        Uses expected_remaining_k and CDF from k_dist.

    For hitter/fantasy markets:
        Uses expected_fantasy_score from hit_dist.

    Returns:
        { P_MORE, P_LESS, raw_probability, method }
    """
    market_lower = (market_type or "").lower()
    dir_upper    = (direction or "MORE").upper()

    if "strikeout" in market_lower or "pitcher k" in market_lower or "k" == market_lower:
        # Pitcher strikeout market
        if k_dist is None:
            return {"P_MORE": 0.5, "P_LESS": 0.5, "raw_probability": 0.5, "method": "default_no_k_dist"}
        # P(K >= ceil(line)) from the p_at_least dict
        threshold = int(math.ceil(line - 0.5))  # 6.5 line → need ≥7
        threshold = max(0, threshold)
        p_more = float(k_dist["p_at_least_n"].get(str(threshold), 0.5))
        p_more = max(0.01, min(0.99, p_more))
        p_less = 1.0 - p_more
    elif "fantasy" in market_lower or "hitter" in market_lower or "fs" == market_lower:
        # Hitter fantasy score market
        if hit_dist is None:
            return {"P_MORE": 0.5, "P_LESS": 0.5, "raw_probability": 0.5, "method": "default_no_hit_dist"}
        efs = hit_dist["expected_fantasy_score"]
        std = hit_dist["fantasy_score_std"]
        # Normal approximation: P(FS > line)
        if std <= 0:
            p_more = 0.5
        else:
            z = (line - efs) / std
            p_more = 1.0 - _norm_cdf(z)
        p_more = max(0.01, min(0.99, p_more))
        p_less = 1.0 - p_more
    else:
        # Generic: treat expected remaining events vs line using normal approx
        exp_opp = opp_dist.get("remaining_pa_hitter") or opp_dist.get("remaining_outs_pitcher") or 3.0
        # Without specific rates, use a conservative 50/50 with uncertainty
        pull_adj = opp_dist.get("pitch_leash_pull_probability", 0.0)
        p_more = max(0.01, min(0.99, 0.5 - pull_adj * 0.2))
        p_less = 1.0 - p_more

    raw_probability = p_more if dir_upper == "MORE" else p_less
    return {
        "P_MORE":            round(p_more, 4),
        "P_LESS":            round(p_less, 4),
        "raw_probability":   round(raw_probability, 4),
        "method":            f"live_micro_{market_lower.replace(' ','_')}",
    }


def compute_calibrated_bounds(
    raw_probability: float,
    opp_dist: dict[str, Any],
    live_state_status: str = "FRESH",
) -> dict[str, Any]:
    """
    Compute calibrated lower and upper bounds.

    Uncertainty increases with:
    - Elevated pitch count (higher pull probability → fewer remaining Ks)
    - Stale live state
    - Low remaining opportunity count

    Returns:
        { calibrated_lower_bound, calibrated_upper_bound, uncertainty_margin }
    """
    pull_prob    = opp_dist.get("pitch_leash_pull_probability", 0.0)
    remaining    = (opp_dist.get("remaining_pa_hitter") or
                    opp_dist.get("remaining_outs_pitcher") or 3.0)

    # Base uncertainty: higher remaining → lower uncertainty (more sample to play out)
    base_uncertainty = max(0.03, 0.15 - remaining * 0.015)

    # Pitch-count penalty
    pitch_penalty = pull_prob * 0.12

    # Staleness penalty
    staleness_penalty = 0.05 if live_state_status == "STALE" else 0.0

    total_uncertainty = min(0.30, base_uncertainty + pitch_penalty + staleness_penalty)

    lower = round(max(0.01, raw_probability - total_uncertainty), 4)
    upper = round(min(0.99, raw_probability + total_uncertainty * 0.5), 4)

    return {
        "calibrated_lower_bound": lower,
        "calibrated_upper_bound": upper,
        "uncertainty_margin":     round(total_uncertainty, 4),
    }


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------

def identify_primary_failure_path(
    state: dict[str, Any],
    opp_dist: dict[str, Any],
    k_dist: dict[str, Any] | None = None,
    prob:   dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Identify the single most likely path to a bust/miss.

    Returns:
        {
          primary_failure_path: str
          failure_probability: float
          failure_description: str
          secondary_failure_path: str | None
        }
    """
    candidates: list[tuple[float, str, str]] = []

    pull_prob = opp_dist.get("pitch_leash_pull_probability", 0.0)
    if pull_prob > 0.0:
        candidates.append((
            pull_prob,
            "EARLY_PITCHER_EXIT",
            f"Pitcher removed before scope completes (pitch count regime: "
            f"{opp_dist.get('pitch_count_regime','unknown')}, "
            f"pull_probability={pull_prob:.0%})",
        ))

    if opp_dist.get("outs_remaining", 9) < 3:
        candidates.append((
            0.55,
            "INSUFFICIENT_REMAINING_SCOPE",
            f"Only {opp_dist.get('outs_remaining',0)} outs remain in scope — "
            f"insufficient sample for prop to resolve at line",
        ))

    if k_dist and k_dist.get("cushion_risk") in ("HIGH", "CRITICAL"):
        k_fail_p = round(1.0 - (k_dist.get("p_at_least_n") or {}).get("1", 0.5), 4)
        candidates.append((
            k_fail_p,
            "K_CUSHION_EXHAUSTED",
            f"Expected remaining Ks ({k_dist.get('expected_remaining_k',0):.1f}) "
            f"below line — cushion risk={k_dist.get('cushion_risk')}",
        ))

    inning = int(state.get("inning", 1))
    if inning >= 4 and not state.get("remaining_innings_scope"):
        candidates.append((
            0.40,
            "SCOPE_BOUNDARY_AMBIGUITY",
            f"Live game in inning {inning} without confirmed remaining_innings_scope "
            f"— scope boundary may not align with prop resolution",
        ))

    if state.get("pitch_count", 0) > 85 and state.get("batters_faced", 0) > 21:
        candidates.append((
            0.80,
            "WORKLOAD_EXIT",
            f"Pitch count {state.get('pitch_count')} + "
            f"batters faced {state.get('batters_faced')} exceed dual-leash threshold",
        ))

    if not candidates:
        candidates.append((0.15, "LINE_VARIANCE", "Prop misses due to normal variance; no structural failure detected"))

    candidates.sort(key=lambda x: x[0], reverse=True)
    primary = candidates[0]
    secondary = candidates[1] if len(candidates) > 1 else None

    return {
        "primary_failure_path":    primary[1],
        "failure_probability":     round(primary[0], 4),
        "failure_description":     primary[2],
        "secondary_failure_path":  secondary[1] if secondary else None,
    }


# ---------------------------------------------------------------------------
# Terminal label assignment
# ---------------------------------------------------------------------------

def assign_terminal_label(
    live_state_result: dict[str, Any],
    prob:              dict[str, Any],
    cal_bounds:        dict[str, Any],
    opp_dist:          dict[str, Any],
    market_type:       str,
) -> tuple[str, list[str]]:
    """
    Assign a terminal label and blocker list based on live micro analysis.

    Label hierarchy (most severe → least severe):
      REJECT_DATA_QUALITY     — missing/stale/invalid live state
      NO_PLAY                 — insufficient edge or scope
      MODEL_QUALIFIED_HOLD    — analyzable but below approval threshold
      FINAL_APPROVED          — all gates pass (rare in live micro)

    Returns: (terminal_label, blockers)
    """
    blockers: list[str] = []

    # Live state gate (hard)
    if not live_state_result.get("passed"):
        status = live_state_result.get("status", "MISSING")
        blockers.append(f"LIVE_STATE:{status}")
        return "REJECT_DATA_QUALITY", blockers

    # Scope gate
    outs_remaining = opp_dist.get("outs_remaining", 0)
    if outs_remaining < 1:
        blockers.append("LIVE_MICRO:SCOPE_EXHAUSTED")
        return "NO_PLAY", blockers

    pull_prob = opp_dist.get("pitch_leash_pull_probability", 0.0)
    if pull_prob >= 0.70:
        blockers.append(f"LIVE_MICRO:PITCHER_WORKLOAD_CRITICAL:{pull_prob:.0%}_PULL_RISK")
        return "NO_PLAY", blockers

    # Probability gate
    lower_bound = cal_bounds.get("calibrated_lower_bound", 0.5)
    raw_prob    = prob.get("raw_probability", 0.5)

    if lower_bound < 0.52:
        blockers.append(f"LIVE_MICRO:CALIBRATED_LOWER_BOUND_BELOW_THRESHOLD:{lower_bound:.3f}")
        return "NO_PLAY", blockers

    if raw_prob < 0.55:
        blockers.append(f"LIVE_MICRO:RAW_PROBABILITY_INSUFFICIENT:{raw_prob:.3f}")
        return "MODEL_QUALIFIED_HOLD", blockers

    if lower_bound < 0.55:
        blockers.append(f"LIVE_MICRO:LOWER_BOUND_BELOW_APPROVAL:{lower_bound:.3f}")
        return "MODEL_QUALIFIED_HOLD", blockers

    return "FINAL_APPROVED", blockers


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------

def analyze(
    game_id:                 str,
    player_id:               str,
    market_type:             str,
    line:                    float,
    direction:               str,
    inning:                  int,
    outs:                    int,
    base_state:              str,
    score:                   str | dict,
    current_pitcher:         str,
    pitch_count:             int,
    batters_faced:           int,
    current_batter:          str,
    batting_order:           int,
    remaining_innings_scope: int,
    capture_timestamp:       str,
    # Optional enrichment
    k_rate:       float | None = None,
    player_rates: dict | None = None,
) -> dict[str, Any]:
    """
    Full live micro-market analysis. Entry point for the /api/wow/mlb/live-micro/analyze endpoint.

    All 14 game-state fields are required. Returns a structured result dict
    with opportunity_distribution, scoring_event_distribution, terminal_label,
    and calibrated probability bounds.

    can_execute is always False. This is a DRY_RUN analysis only.
    """
    state = {
        "game_id":                 game_id,
        "player_id":               player_id,
        "market_type":             market_type,
        "line":                    line,
        "direction":               direction,
        "inning":                  inning,
        "outs":                    outs,
        "base_state":              base_state,
        "score":                   score,
        "current_pitcher":         current_pitcher,
        "pitch_count":             pitch_count,
        "batters_faced":           batters_faced,
        "current_batter":          current_batter,
        "batting_order":           batting_order,
        "remaining_innings_scope": remaining_innings_scope,
        "capture_timestamp":       capture_timestamp,
    }

    # Step 1: Validate live state freshness
    live_state_result = validate_live_state(state)

    # Step 2: Opportunity distribution (runs regardless of staleness for diagnostic)
    opp_dist = compute_opportunity_distribution(state)

    # Step 3: Market-specific distributions
    k_dist   = None
    hit_dist = None
    market_lower = market_type.lower()

    if "strikeout" in market_lower or "pitcher k" in market_lower:
        k_dist = compute_pitcher_k_distribution(state, opp_dist, k_rate=k_rate)
    else:
        hit_dist = compute_hitter_event_distribution(state, opp_dist, rates=player_rates)

    # Step 4: Raw probability
    prob_result = compute_raw_probability(
        market_type=market_type,
        direction=direction,
        line=float(line),
        opp_dist=opp_dist,
        k_dist=k_dist,
        hit_dist=hit_dist,
    )

    # Step 5: Calibrated bounds
    cal_bounds = compute_calibrated_bounds(
        raw_probability=prob_result["raw_probability"],
        opp_dist=opp_dist,
        live_state_status=live_state_result.get("status", "FRESH"),
    )

    # Step 6: Primary failure path
    failure_path = identify_primary_failure_path(state, opp_dist, k_dist, prob_result)

    # Step 7: Terminal label
    terminal_label, blockers = assign_terminal_label(
        live_state_result=live_state_result,
        prob=prob_result,
        cal_bounds=cal_bounds,
        opp_dist=opp_dist,
        market_type=market_type,
    )

    return {
        "ok":                   terminal_label not in ("REJECT_DATA_QUALITY",),
        "module":               "cm_mlb_live_micro_market_model",
        "module_version":       MODULE_VERSION,
        "can_execute":          False,
        "execution_rule":       EXECUTION_RULE,
        # Identity
        "game_id":              game_id,
        "player_id":            player_id,
        "market_type":          market_type,
        "line":                 line,
        "direction":            direction,
        # Freshness
        "data_freshness":       live_state_result.get("data_freshness"),
        "live_state_age_seconds": live_state_result.get("age_seconds"),
        "identity_status":      "IDENTIFIED",
        "live_state_status":    live_state_result.get("status"),
        # Distributions
        "opportunity_distribution":   opp_dist,
        "scoring_event_distribution": (hit_dist or {}).get("scoring_event_distribution", {}),
        "pitcher_k_distribution":     k_dist,
        "hitter_event_distribution":  hit_dist,
        # Probabilities
        "P_MORE":                   prob_result["P_MORE"],
        "P_LESS":                   prob_result["P_LESS"],
        "raw_probability":          prob_result["raw_probability"],
        "calibrated_lower_bound":   cal_bounds["calibrated_lower_bound"],
        "calibrated_upper_bound":   cal_bounds["calibrated_upper_bound"],
        "uncertainty_margin":       cal_bounds["uncertainty_margin"],
        "remaining_plate_appearances": opp_dist.get("remaining_pa_hitter"),
        "single_event_bust_probability": failure_path.get("failure_probability"),
        # Failure path
        "primary_failure_path":     failure_path["primary_failure_path"],
        "failure_description":      failure_path["failure_description"],
        "secondary_failure_path":   failure_path.get("secondary_failure_path"),
        # Decision
        "terminal_label":    terminal_label,
        "blockers":          blockers,
        "final_action":      "DRY_RUN_ANALYSIS_ONLY",
    }
